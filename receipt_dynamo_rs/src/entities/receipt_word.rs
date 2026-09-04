use std::collections::HashMap;

use crate::attr::{Attr, Item, ItemExt};
use crate::constants::EmbeddingStatus;
use crate::error::{Error, Result};
use crate::keys::{self, EntityType};

use super::geometry_entity::{
    embedding_status_of, is_noise_of, parse_receipt_line_word_sk, write_receipt_fields,
    TextGeometry,
};
use super::{Entity, PrimaryKey};

#[derive(Clone, Debug, PartialEq)]
pub struct ReceiptWord {
    pub receipt_id: u32,
    pub line_id: u32,
    pub word_id: u32,
    pub geom: TextGeometry,
    pub embedding_status: EmbeddingStatus,
    pub is_noise: bool,
    pub extracted_data: Option<HashMap<String, String>>,
}

impl ReceiptWord {
    pub fn new(receipt_id: u32, line_id: u32, word_id: u32, geom: TextGeometry) -> Result<Self> {
        if receipt_id == 0 {
            return Err(Error::validation("receipt_id must be positive"));
        }
        Ok(Self {
            receipt_id,
            line_id,
            word_id,
            geom,
            embedding_status: EmbeddingStatus::None,
            is_noise: false,
            extracted_data: None,
        })
    }

    pub fn centroid(&self) -> (f64, f64) {
        let p = self.geom.geometry.corners.centroid();
        (p.x, p.y)
    }

    pub fn distance_and_angle_from(&self, other: &Self) -> (f64, f64) {
        let (x1, y1) = self.centroid();
        let (x2, y2) = other.centroid();
        let distance = (x2 - x1).hypot(y2 - y1);
        let angle = (y2 - y1).atan2(x2 - x1);
        (distance, angle)
    }

    pub fn from_item(item: &Item) -> Result<Self> {
        <Self as Entity>::from_item(item)
    }
}

impl Entity for ReceiptWord {
    const TYPE: EntityType = EntityType::ReceiptWord;

    fn primary_key(&self) -> PrimaryKey {
        PrimaryKey {
            pk: keys::image_pk(&self.geom.image_id),
            sk: keys::receipt_word_sk(self.receipt_id, self.line_id, self.word_id),
        }
    }

    fn to_item(&self) -> Item {
        let mut item = Item::with_capacity(28);
        let pk = keys::image_pk(&self.geom.image_id);
        let sk = keys::receipt_word_sk(self.receipt_id, self.line_id, self.word_id);
        keys::put_pk_sk(&mut item, pk, sk);
        item.insert("TYPE".into(), Attr::s(Self::TYPE.as_str()));

        let mut gsi1sk = String::with_capacity(64 + self.geom.image_id.len());
        gsi1sk.push_str("WORD#IMAGE#");
        gsi1sk.push_str(&self.geom.image_id);
        gsi1sk.push_str("#RECEIPT#");
        keys::push_pad5(&mut gsi1sk, self.receipt_id);
        gsi1sk.push_str("#LINE#");
        keys::push_pad5(&mut gsi1sk, self.line_id);
        gsi1sk.push_str("#WORD#");
        keys::push_pad5(&mut gsi1sk, self.word_id);
        keys::put_gsi(
            &mut item,
            "GSI1PK",
            format!("EMBEDDING_STATUS#{}", self.embedding_status.as_str()),
            "GSI1SK",
            gsi1sk,
        );

        let mut gsi2sk = String::with_capacity(64 + self.geom.image_id.len());
        gsi2sk.push_str("IMAGE#");
        gsi2sk.push_str(&self.geom.image_id);
        gsi2sk.push_str("#RECEIPT#");
        keys::push_pad5(&mut gsi2sk, self.receipt_id);
        gsi2sk.push_str("#LINE#");
        keys::push_pad5(&mut gsi2sk, self.line_id);
        gsi2sk.push_str("#WORD#");
        keys::push_pad5(&mut gsi2sk, self.word_id);
        keys::put_gsi(&mut item, "GSI2PK", "RECEIPT".into(), "GSI2SK", gsi2sk);

        keys::put_gsi(
            &mut item,
            "GSI3PK",
            keys::receipt_scope(&self.geom.image_id, self.receipt_id),
            "GSI3SK",
            "WORD".into(),
        );
        keys::put_gsi(
            &mut item,
            "GSI4PK",
            keys::receipt_scope(&self.geom.image_id, self.receipt_id),
            "GSI4SK",
            keys::gsi4_word_sk(self.line_id, self.word_id),
        );
        self.geom.write_fields(&mut item);
        write_receipt_fields(&mut item, self.embedding_status, self.is_noise);
        item.insert(
            "extracted_data".into(),
            match &self.extracted_data {
                Some(map) => {
                    let m = map
                        .iter()
                        .map(|(k, v)| (k.clone(), Attr::s(v.clone())))
                        .collect();
                    Attr::map(m)
                }
                None => Attr::null(),
            },
        );
        item
    }

    fn from_item(item: &Item) -> Result<Self> {
        let geom = TextGeometry::from_item(item)?;
        let sk = item.require_s("SK")?;
        let (receipt_id, line_id, word_id) = parse_receipt_line_word_sk(sk)?;
        let extracted_data = match item.get("extracted_data") {
            Some(attr) if !attr.is_null() => {
                let m = attr.as_map()?;
                let mut out = HashMap::with_capacity(m.len());
                for (k, v) in m {
                    out.insert(k.clone(), v.as_s()?.to_string());
                }
                Some(out)
            }
            _ => None,
        };
        Ok(Self {
            receipt_id,
            line_id,
            word_id,
            geom,
            embedding_status: embedding_status_of(item)?,
            is_noise: is_noise_of(item)?,
            extracted_data,
        })
    }
}
