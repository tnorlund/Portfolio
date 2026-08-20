use std::collections::HashMap;

use crate::attr::{Attr, Item, ItemExt};
use crate::error::{Error, Result};
use crate::keys::{self, EntityType};

use super::geometry_entity::TextGeometry;
use super::{Entity, PrimaryKey};

#[derive(Clone, Debug, PartialEq)]
pub struct Word {
    pub line_id: u32,
    pub word_id: u32,
    pub geom: TextGeometry,
    pub extracted_data: Option<(String, String)>,
}

impl Word {
    pub fn new(line_id: u32, word_id: u32, geom: TextGeometry) -> Result<Self> {
        Ok(Self {
            line_id,
            word_id,
            geom,
            extracted_data: None,
        })
    }

    pub fn from_item(item: &Item) -> Result<Self> {
        <Self as Entity>::from_item(item)
    }
}

impl Entity for Word {
    const TYPE: EntityType = EntityType::Word;

    fn primary_key(&self) -> PrimaryKey {
        PrimaryKey {
            pk: keys::image_pk(&self.geom.image_id),
            sk: keys::word_sk(self.line_id, self.word_id),
        }
    }

    fn to_item(&self) -> Item {
        let mut item = Item::with_capacity(18);
        let pk = keys::image_pk(&self.geom.image_id);
        let sk = keys::word_sk(self.line_id, self.word_id);
        keys::put_pk_sk(&mut item, pk.clone(), sk.clone());
        item.insert("TYPE".into(), Attr::s(Self::TYPE.as_str()));
        keys::put_gsi(&mut item, "GSI2PK", pk, "GSI2SK", sk);
        self.geom.write_fields(&mut item);
        item.insert(
            "extracted_data".into(),
            match &self.extracted_data {
                Some((ty, value)) => {
                    let mut m = HashMap::with_capacity(2);
                    m.insert("type".into(), Attr::s(ty.clone()));
                    m.insert("value".into(), Attr::s(value.clone()));
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
        let parts: Vec<&str> = sk.split('#').collect();
        if parts.len() < 4 || parts[0] != "LINE" || parts[2] != "WORD" {
            return Err(Error::validation(format!(
                "Invalid SK format for Word: {sk}"
            )));
        }
        let extracted_data = match item.get("extracted_data") {
            Some(attr) if !attr.is_null() => {
                let m = attr.as_map()?;
                Some((
                    m.get("type")
                        .ok_or_else(|| Error::validation("extracted_data missing type"))?
                        .as_s()?
                        .to_string(),
                    m.get("value")
                        .ok_or_else(|| Error::validation("extracted_data missing value"))?
                        .as_s()?
                        .to_string(),
                ))
            }
            _ => None,
        };
        Ok(Self {
            line_id: keys::parse_padded_u32(parts[1])?,
            word_id: keys::parse_padded_u32(parts[3])?,
            geom,
            extracted_data,
        })
    }
}
