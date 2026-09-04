use crate::attr::{deserialize_point, serialize_point18, Attr, Item, ItemExt};
use crate::error::{Error, Result};
use crate::keys::{self, assert_valid_uuid, EntityType};

use super::{validate_positive_dimensions, CdnFields, Entity, Point, PrimaryKey};

#[derive(Clone, Debug, PartialEq)]
pub struct Receipt {
    pub image_id: String,
    pub receipt_id: u32,
    pub width: u32,
    pub height: u32,
    pub timestamp_added: String,
    pub raw_s3_bucket: String,
    pub raw_s3_key: String,
    pub top_left: Point,
    pub top_right: Point,
    pub bottom_left: Point,
    pub bottom_right: Point,
    pub cdn: CdnFields,
}

impl Receipt {
    pub fn new(
        image_id: impl Into<String>,
        receipt_id: u32,
        width: u32,
        height: u32,
        timestamp_added: impl Into<String>,
        raw_s3_bucket: impl Into<String>,
        raw_s3_key: impl Into<String>,
        top_left: Point,
        top_right: Point,
        bottom_left: Point,
        bottom_right: Point,
    ) -> Result<Self> {
        let image_id = image_id.into();
        assert_valid_uuid(&image_id)?;
        if receipt_id == 0 {
            return Err(Error::validation("receipt_id must be positive"));
        }
        validate_positive_dimensions(width, height)?;
        Ok(Self {
            image_id,
            receipt_id,
            width,
            height,
            timestamp_added: timestamp_added.into(),
            raw_s3_bucket: raw_s3_bucket.into(),
            raw_s3_key: raw_s3_key.into(),
            top_left,
            top_right,
            bottom_left,
            bottom_right,
            cdn: CdnFields::default(),
        })
    }
}

impl Entity for Receipt {
    const TYPE: EntityType = EntityType::Receipt;

    fn primary_key(&self) -> PrimaryKey {
        PrimaryKey {
            pk: keys::image_pk(&self.image_id),
            sk: keys::receipt_sk(self.receipt_id),
        }
    }

    fn to_item(&self) -> Item {
        let mut item = Item::with_capacity(28);
        let pk = keys::image_pk(&self.image_id);
        let sk = keys::receipt_sk(self.receipt_id);
        keys::put_pk_sk(&mut item, pk.clone(), sk.clone());
        keys::put_gsi(&mut item, "GSI1PK", pk, "GSI1SK", sk);
        keys::put_gsi(
            &mut item,
            "GSI2PK",
            "RECEIPT".into(),
            "GSI2SK",
            keys::receipt_scope(&self.image_id, self.receipt_id),
        );
        keys::put_gsi(
            &mut item,
            "GSI4PK",
            keys::receipt_scope(&self.image_id, self.receipt_id),
            "GSI4SK",
            "0_RECEIPT".into(),
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
            "top_left".into(),
            serialize_point18(self.top_left.x, self.top_left.y),
        );
        item.insert(
            "top_right".into(),
            serialize_point18(self.top_right.x, self.top_right.y),
        );
        item.insert(
            "bottom_left".into(),
            serialize_point18(self.bottom_left.x, self.bottom_left.y),
        );
        item.insert(
            "bottom_right".into(),
            serialize_point18(self.bottom_right.x, self.bottom_right.y),
        );
        item.insert(
            "sha256".into(),
            Attr::optional_s(self.cdn.sha256.as_deref()),
        );
        self.cdn.write_to_item(&mut item);
        item
    }

    fn from_item(item: &Item) -> Result<Self> {
        let pk = item.require_s("PK")?;
        let image_id = keys::split_hash(pk, "IMAGE")?.to_string();
        let sk = item.require_s("SK")?;
        let receipt_id = keys::parse_padded_u32(keys::split_hash(sk, "RECEIPT")?)?;
        Ok(Self {
            image_id,
            receipt_id,
            width: item.require("width")?.as_n_u32()?,
            height: item.require("height")?.as_n_u32()?,
            timestamp_added: item.require_s("timestamp_added")?.to_string(),
            raw_s3_bucket: item.require_s("raw_s3_bucket")?.to_string(),
            raw_s3_key: item.require_s("raw_s3_key")?.to_string(),
            top_left: deserialize_point(item.require("top_left")?)?,
            top_right: deserialize_point(item.require("top_right")?)?,
            bottom_left: deserialize_point(item.require("bottom_left")?)?,
            bottom_right: deserialize_point(item.require("bottom_right")?)?,
            cdn: CdnFields::from_item(item),
        })
    }
}

impl Receipt {
    pub fn from_item(item: &Item) -> Result<Self> {
        <Self as Entity>::from_item(item)
    }
}
