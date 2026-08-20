use crate::attr::{serialize_bounding_box, Attr, Item, ItemExt};
use crate::constants::{OcrJobType, OcrStatus};
use crate::error::{Error, Result};
use crate::geometry::BoundingBox;
use crate::keys::{self, assert_valid_uuid, EntityType};

use super::{Entity, PrimaryKey};

#[derive(Clone, Debug, PartialEq)]
pub struct OcrJob {
    pub image_id: String,
    pub job_id: String,
    pub s3_bucket: String,
    pub s3_key: String,
    pub created_at: String,
    pub updated_at: Option<String>,
    pub status: OcrStatus,
    pub job_type: OcrJobType,
    pub receipt_id: Option<u32>,
    pub reocr_region: Option<BoundingBox>,
    pub reocr_reason: Option<String>,
    pub reocr_strategy: Option<String>,
}

impl OcrJob {
    pub fn new(
        image_id: impl Into<String>,
        job_id: impl Into<String>,
        s3_bucket: impl Into<String>,
        s3_key: impl Into<String>,
        created_at: impl Into<String>,
    ) -> Result<Self> {
        let image_id = image_id.into();
        let job_id = job_id.into();
        assert_valid_uuid(&image_id)?;
        assert_valid_uuid(&job_id)?;
        let s3_bucket = s3_bucket.into();
        let s3_key = s3_key.into();
        if s3_bucket.is_empty() {
            return Err(Error::validation("s3_bucket must be non-empty"));
        }
        if s3_key.is_empty() {
            return Err(Error::validation("s3_key must be non-empty"));
        }
        Ok(Self {
            image_id,
            job_id,
            s3_bucket,
            s3_key,
            created_at: created_at.into(),
            updated_at: None,
            status: OcrStatus::Pending,
            job_type: OcrJobType::FirstPass,
            receipt_id: None,
            reocr_region: None,
            reocr_reason: None,
            reocr_strategy: None,
        })
    }

    pub fn from_item(item: &Item) -> Result<Self> {
        <Self as Entity>::from_item(item)
    }
}

impl Entity for OcrJob {
    const TYPE: EntityType = EntityType::OcrJob;

    fn primary_key(&self) -> PrimaryKey {
        PrimaryKey {
            pk: keys::image_pk(&self.image_id),
            sk: keys::ocr_job_sk(&self.job_id),
        }
    }

    fn to_item(&self) -> Item {
        let mut item = Item::with_capacity(18);
        keys::put_pk_sk(
            &mut item,
            keys::image_pk(&self.image_id),
            keys::ocr_job_sk(&self.job_id),
        );
        keys::put_gsi(
            &mut item,
            "GSI1PK",
            format!("OCR_JOB_STATUS#{}", self.status.as_str()),
            "GSI1SK",
            keys::ocr_job_sk(&self.job_id),
        );
        keys::put_gsi(
            &mut item,
            "GSI2PK",
            format!("OCR_JOB_STATUS#{}", self.status.as_str()),
            "GSI2SK",
            keys::ocr_job_sk(&self.job_id),
        );
        item.insert("TYPE".into(), Attr::s(Self::TYPE.as_str()));
        item.insert("s3_bucket".into(), Attr::s(self.s3_bucket.clone()));
        item.insert("s3_key".into(), Attr::s(self.s3_key.clone()));
        item.insert("created_at".into(), Attr::s(self.created_at.clone()));
        item.insert(
            "updated_at".into(),
            match &self.updated_at {
                Some(s) => Attr::s(s.clone()),
                None => Attr::null(),
            },
        );
        item.insert("status".into(), Attr::s(self.status.as_str()));
        item.insert("job_type".into(), Attr::s(self.job_type.as_str()));
        item.insert(
            "receipt_id".into(),
            match self.receipt_id {
                Some(id) => Attr::n_uint(id),
                None => Attr::null(),
            },
        );
        item.insert(
            "reocr_region".into(),
            match self.reocr_region {
                Some(bb) => serialize_bounding_box(bb.x, bb.y, bb.width, bb.height),
                None => Attr::null(),
            },
        );
        item.insert(
            "reocr_reason".into(),
            Attr::optional_s(self.reocr_reason.as_deref()),
        );
        item.insert(
            "reocr_strategy".into(),
            Attr::optional_s(self.reocr_strategy.as_deref()),
        );
        item
    }

    fn from_item(item: &Item) -> Result<Self> {
        let pk = item.require_s("PK")?;
        let image_id = keys::split_hash(pk, "IMAGE")?.to_string();
        let sk = item.require_s("SK")?;
        let job_id = keys::split_hash(sk, "OCR_JOB")?.to_string();
        let receipt_id = match item.get("receipt_id") {
            Some(attr) if !attr.is_null() => Some(attr.as_n_u32()?),
            _ => None,
        };
        let reocr_region = match item.get("reocr_region") {
            Some(attr) if !attr.is_null() => Some(crate::attr::deserialize_bbox(attr)?),
            _ => None,
        };
        let opt_s = |key: &str| match item.get(key) {
            Some(attr) if !attr.is_null() => Some(attr.as_s().ok()?.to_string()),
            _ => None,
        };
        Ok(Self {
            image_id,
            job_id,
            s3_bucket: item.require_s("s3_bucket")?.to_string(),
            s3_key: item.require_s("s3_key")?.to_string(),
            created_at: item.require_s("created_at")?.to_string(),
            updated_at: opt_s("updated_at"),
            status: OcrStatus::parse(item.require_s("status")?)?,
            job_type: OcrJobType::parse(item.require_s("job_type")?)?,
            receipt_id,
            reocr_region,
            reocr_reason: opt_s("reocr_reason"),
            reocr_strategy: opt_s("reocr_strategy"),
        })
    }
}
