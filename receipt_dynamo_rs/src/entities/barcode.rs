use crate::attr::{Attr, Item, ItemExt};
use crate::error::{Error, Result};
use crate::keys::{self, EntityType};

use super::geometry_entity::TextGeometry;
use super::{require_positive, Entity, PrimaryKey};

#[derive(Clone, Debug, PartialEq)]
pub struct ReceiptBarcode {
    pub receipt_id: u32,
    pub barcode_id: u32,
    pub symbology: String,
    pub geom: TextGeometry,
}

impl ReceiptBarcode {
    pub fn new(
        receipt_id: u32,
        barcode_id: u32,
        symbology: impl Into<String>,
        geom: TextGeometry,
    ) -> Result<Self> {
        require_positive("receipt_id", receipt_id)?;
        let symbology = symbology.into();
        if symbology.is_empty() {
            return Err(Error::validation("symbology must be a non-empty string"));
        }
        Ok(Self {
            receipt_id,
            barcode_id,
            symbology,
            geom,
        })
    }

    pub fn payload(&self) -> Option<&str> {
        if self.geom.text.is_empty() {
            None
        } else {
            Some(self.geom.text.as_str())
        }
    }

    pub fn from_item(item: &Item) -> Result<Self> {
        <Self as Entity>::from_item(item)
    }
}

impl Entity for ReceiptBarcode {
    const TYPE: EntityType = EntityType::ReceiptBarcode;

    fn primary_key(&self) -> PrimaryKey {
        PrimaryKey {
            pk: keys::image_pk(&self.geom.image_id),
            sk: keys::receipt_barcode_sk(self.receipt_id, self.barcode_id),
        }
    }

    fn to_item(&self) -> Item {
        let mut item = Item::with_capacity(20);
        keys::put_pk_sk(
            &mut item,
            keys::image_pk(&self.geom.image_id),
            keys::receipt_barcode_sk(self.receipt_id, self.barcode_id),
        );
        item.insert("TYPE".into(), Attr::s(Self::TYPE.as_str()));
        keys::put_gsi(
            &mut item,
            "GSI3PK",
            keys::receipt_scope(&self.geom.image_id, self.receipt_id),
            "GSI3SK",
            "BARCODE".into(),
        );
        keys::put_gsi(
            &mut item,
            "GSI4PK",
            keys::receipt_scope(&self.geom.image_id, self.receipt_id),
            "GSI4SK",
            keys::gsi4_barcode_sk(self.barcode_id),
        );
        self.geom.write_fields(&mut item);
        item.insert("symbology".into(), Attr::s(self.symbology.clone()));
        item
    }

    fn from_item(item: &Item) -> Result<Self> {
        let geom = TextGeometry::from_item(item)?;
        let sk = item.require_s("SK")?;
        let parts: Vec<&str> = sk.split('#').collect();
        if parts.len() < 4 || parts[0] != "RECEIPT" || parts[2] != "BARCODE" {
            return Err(Error::validation(format!(
                "Invalid SK format for ReceiptBarcode: {sk}"
            )));
        }
        Self::new(
            keys::parse_padded_u32(parts[1])?,
            keys::parse_padded_u32(parts[3])?,
            item.require_s("symbology")?,
            geom,
        )
    }
}
