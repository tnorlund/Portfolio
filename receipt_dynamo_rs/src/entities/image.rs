use crate::attr::{Attr, Item, ItemExt};
use crate::constants::ImageType;
use crate::error::Result;
use crate::keys::{self, assert_valid_uuid, EntityType};

use super::{validate_positive_dimensions, CdnFields, Entity, PrimaryKey};

#[derive(Clone, Debug, PartialEq)]
pub struct Image {
    pub image_id: String,
    pub width: u32,
    pub height: u32,
    pub timestamp_added: String,
    pub raw_s3_bucket: String,
    pub raw_s3_key: String,
    pub image_type: ImageType,
    pub receipt_count: Option<u32>,
    pub cdn: CdnFields,
}

impl Image {
    pub fn new(
        image_id: impl Into<String>,
        width: u32,
        height: u32,
        timestamp_added: impl Into<String>,
        raw_s3_bucket: impl Into<String>,
        raw_s3_key: impl Into<String>,
    ) -> Result<Self> {
        let image_id = image_id.into();
        assert_valid_uuid(&image_id)?;
        validate_positive_dimensions(width, height)?;
        Ok(Self {
            image_id,
            width,
            height,
            timestamp_added: timestamp_added.into(),
            raw_s3_bucket: raw_s3_bucket.into(),
            raw_s3_key: raw_s3_key.into(),
            image_type: ImageType::Scan,
            receipt_count: None,
            cdn: CdnFields::default(),
        })
    }
}

impl Entity for Image {
    const TYPE: EntityType = EntityType::Image;

    fn primary_key(&self) -> PrimaryKey {
        PrimaryKey {
            pk: keys::image_pk(&self.image_id),
            sk: "IMAGE".to_string(),
        }
    }

    fn to_item(&self) -> Item {
        let mut item = Item::with_capacity(24);
        let pk = keys::image_pk(&self.image_id);
        keys::put_pk_sk(&mut item, pk.clone(), "IMAGE".into());
        keys::put_gsi(&mut item, "GSI1PK", pk.clone(), "GSI1SK", "IMAGE".into());
        keys::put_gsi(&mut item, "GSI2PK", pk.clone(), "GSI2SK", "IMAGE".into());
        let receipt_count_str = format!("{:05}", self.receipt_count.unwrap_or(0));
        keys::put_gsi(
            &mut item,
            "GSI3PK",
            format!("IMAGE#{}", self.image_type.as_str()),
            "GSI3SK",
            format!("NUM_RECEIPTS#{receipt_count_str}"),
        );
        item.insert("TYPE".into(), Attr::s(Self::TYPE.as_str()));
        item.insert("width".into(), Attr::n_uint(self.width));
        item.insert("height".into(), Attr::n_uint(self.height));
        item.insert(
            "timestamp_added".into(),
            Attr::s(self.timestamp_added.clone()),
        );
        item.insert("raw_s3_bucket".into(), Attr::s(self.raw_s3_bucket.clone()));
        item.insert("raw_s3_key".into(), Attr::s(self.raw_s3_key.clone()));
        item.insert(
            "sha256".into(),
            Attr::optional_s(self.cdn.sha256.as_deref()),
        );
        self.cdn.write_to_item(&mut item);
        item.insert("image_type".into(), Attr::s(self.image_type.as_str()));
        item.insert(
            "receipt_count".into(),
            match self.receipt_count {
                Some(n) => Attr::n_uint(n),
                None => Attr::null(),
            },
        );
        item
    }

    fn from_item(item: &Item) -> Result<Self> {
        let pk = item.require_s("PK")?;
        let image_id = keys::split_hash(pk, "IMAGE")?.to_string();
        let image_type = match item.get("image_type").and_then(|a| a.as_s().ok()) {
            Some(s) => ImageType::parse(s)?,
            None => ImageType::Scan,
        };
        let receipt_count = match item.get("receipt_count") {
            Some(attr) if !attr.is_null() => Some(attr.as_n_u32()?),
            _ => None,
        };
        Ok(Self {
            image_id,
            width: item.require("width")?.as_n_u32()?,
            height: item.require("height")?.as_n_u32()?,
            timestamp_added: item.require_s("timestamp_added")?.to_string(),
            raw_s3_bucket: item.require_s("raw_s3_bucket")?.to_string(),
            raw_s3_key: item.require_s("raw_s3_key")?.to_string(),
            image_type,
            receipt_count,
            cdn: CdnFields::from_item(item),
        })
    }
}

impl Image {
    pub fn from_item(item: &Item) -> Result<Self> {
        <Self as Entity>::from_item(item)
    }
}
