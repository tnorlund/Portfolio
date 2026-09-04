use crate::attr::{Attr, Item, ItemExt};
use crate::constants::EmbeddingStatus;
use crate::error::{Error, Result};
use crate::keys::{self, EntityType};

use super::geometry_entity::{
    embedding_status_of, is_noise_of, write_receipt_fields, TextGeometry,
};
use super::{require_positive, Entity, PrimaryKey};

#[derive(Clone, Debug, PartialEq)]
pub struct ReceiptLetter {
    pub receipt_id: u32,
    pub line_id: u32,
    pub word_id: u32,
    pub letter_id: u32,
    pub geom: TextGeometry,
    pub embedding_status: EmbeddingStatus,
    pub is_noise: bool,
}

impl ReceiptLetter {
    pub fn new(
        receipt_id: u32,
        line_id: u32,
        word_id: u32,
        letter_id: u32,
        geom: TextGeometry,
    ) -> Result<Self> {
        require_positive("receipt_id", receipt_id)?;
        if geom.text.chars().count() != 1 {
            return Err(Error::validation("text must be exactly one character"));
        }
        Ok(Self {
            receipt_id,
            line_id,
            word_id,
            letter_id,
            geom,
            embedding_status: EmbeddingStatus::None,
            is_noise: false,
        })
    }

    pub fn from_item(item: &Item) -> Result<Self> {
        <Self as Entity>::from_item(item)
    }
}

impl Entity for ReceiptLetter {
    const TYPE: EntityType = EntityType::ReceiptLetter;

    fn primary_key(&self) -> PrimaryKey {
        PrimaryKey {
            pk: keys::image_pk(&self.geom.image_id),
            sk: keys::receipt_letter_sk(
                self.receipt_id,
                self.line_id,
                self.word_id,
                self.letter_id,
            ),
        }
    }

    fn to_item(&self) -> Item {
        let mut item = Item::with_capacity(20);
        keys::put_pk_sk(
            &mut item,
            keys::image_pk(&self.geom.image_id),
            keys::receipt_letter_sk(self.receipt_id, self.line_id, self.word_id, self.letter_id),
        );
        item.insert("TYPE".into(), Attr::s(Self::TYPE.as_str()));
        self.geom.write_fields(&mut item);
        write_receipt_fields(&mut item, self.embedding_status, self.is_noise);
        item
    }

    fn from_item(item: &Item) -> Result<Self> {
        let geom = TextGeometry::from_item(item)?;
        let sk = item.require_s("SK")?;
        let parts: Vec<&str> = sk.split('#').collect();
        if parts.len() < 8
            || parts[0] != "RECEIPT"
            || parts[2] != "LINE"
            || parts[4] != "WORD"
            || parts[6] != "LETTER"
        {
            return Err(Error::validation(format!(
                "Invalid SK format for ReceiptLetter: {sk}"
            )));
        }
        Ok(Self {
            receipt_id: keys::parse_padded_u32(parts[1])?,
            line_id: keys::parse_padded_u32(parts[3])?,
            word_id: keys::parse_padded_u32(parts[5])?,
            letter_id: keys::parse_padded_u32(parts[7])?,
            geom,
            embedding_status: embedding_status_of(item)?,
            is_noise: is_noise_of(item)?,
        })
    }
}
