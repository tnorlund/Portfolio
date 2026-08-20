use crate::attr::{Attr, Item, ItemExt};
use crate::constants::EmbeddingStatus;
use crate::error::{Error, Result};
use crate::keys::{self, EntityType};

use super::geometry_entity::{
    embedding_status_of, is_noise_of, write_receipt_fields, TextGeometry,
};
use super::{Entity, PrimaryKey};

#[derive(Clone, Debug, PartialEq)]
pub struct ReceiptLine {
    pub receipt_id: u32,
    pub line_id: u32,
    pub geom: TextGeometry,
    pub embedding_status: EmbeddingStatus,
    pub is_noise: bool,
}

impl ReceiptLine {
    pub fn new(receipt_id: u32, line_id: u32, geom: TextGeometry) -> Result<Self> {
        if receipt_id == 0 {
            return Err(Error::validation("receipt_id must be positive"));
        }
        Ok(Self {
            receipt_id,
            line_id,
            geom,
            embedding_status: EmbeddingStatus::None,
            is_noise: false,
        })
    }

    pub fn from_item(item: &Item) -> Result<Self> {
        <Self as Entity>::from_item(item)
    }
}

impl Entity for ReceiptLine {
    const TYPE: EntityType = EntityType::ReceiptLine;

    fn primary_key(&self) -> PrimaryKey {
        PrimaryKey {
            pk: keys::image_pk(&self.geom.image_id),
            sk: keys::receipt_line_sk(self.receipt_id, self.line_id),
        }
    }

    fn to_item(&self) -> Item {
        let mut item = Item::with_capacity(24);
        let pk = keys::image_pk(&self.geom.image_id);
        let sk = keys::receipt_line_sk(self.receipt_id, self.line_id);
        keys::put_pk_sk(&mut item, pk, sk);
        item.insert("TYPE".into(), Attr::s(Self::TYPE.as_str()));
        let mut gsi1sk = String::with_capacity(5 + 6 + self.geom.image_id.len() + 9 + 5 + 6 + 5);
        gsi1sk.push_str("LINE#IMAGE#");
        gsi1sk.push_str(&self.geom.image_id);
        gsi1sk.push_str("#RECEIPT#");
        keys::push_pad5(&mut gsi1sk, self.receipt_id);
        gsi1sk.push_str("#LINE#");
        keys::push_pad5(&mut gsi1sk, self.line_id);
        keys::put_gsi(
            &mut item,
            "GSI1PK",
            format!("EMBEDDING_STATUS#{}", self.embedding_status.as_str()),
            "GSI1SK",
            gsi1sk,
        );
        keys::put_gsi(
            &mut item,
            "GSI3PK",
            keys::receipt_scope(&self.geom.image_id, self.receipt_id),
            "GSI3SK",
            "LINE".into(),
        );
        keys::put_gsi(
            &mut item,
            "GSI4PK",
            keys::receipt_scope(&self.geom.image_id, self.receipt_id),
            "GSI4SK",
            keys::gsi4_line_sk(self.line_id),
        );
        self.geom.write_fields(&mut item);
        write_receipt_fields(&mut item, self.embedding_status, self.is_noise);
        item
    }

    fn from_item(item: &Item) -> Result<Self> {
        let geom = TextGeometry::from_item(item)?;
        let sk = item.require_s("SK")?;
        let parts: Vec<&str> = sk.split('#').collect();
        if parts.len() < 4 || parts[0] != "RECEIPT" || parts[2] != "LINE" {
            return Err(Error::validation(format!(
                "Invalid SK format for ReceiptLine: {sk}"
            )));
        }
        Ok(Self {
            receipt_id: keys::parse_padded_u32(parts[1])?,
            line_id: keys::parse_padded_u32(parts[3])?,
            geom,
            embedding_status: embedding_status_of(item)?,
            is_noise: is_noise_of(item)?,
        })
    }
}
