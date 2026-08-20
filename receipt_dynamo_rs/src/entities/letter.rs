use crate::attr::{Attr, Item, ItemExt};
use crate::error::{Error, Result};
use crate::keys::{self, EntityType};

use super::geometry_entity::TextGeometry;
use super::{require_positive, Entity, PrimaryKey};

#[derive(Clone, Debug, PartialEq)]
pub struct Letter {
    pub line_id: u32,
    pub word_id: u32,
    pub letter_id: u32,
    pub geom: TextGeometry,
}

impl Letter {
    pub fn new(line_id: u32, word_id: u32, letter_id: u32, geom: TextGeometry) -> Result<Self> {
        require_positive("line_id", line_id)?;
        require_positive("word_id", word_id)?;
        require_positive("letter_id", letter_id)?;
        if geom.text.chars().count() != 1 {
            return Err(Error::validation("text must be exactly one character"));
        }
        Ok(Self {
            line_id,
            word_id,
            letter_id,
            geom,
        })
    }

    pub fn from_item(item: &Item) -> Result<Self> {
        <Self as Entity>::from_item(item)
    }
}

impl Entity for Letter {
    const TYPE: EntityType = EntityType::Letter;

    fn primary_key(&self) -> PrimaryKey {
        PrimaryKey {
            pk: keys::image_pk(&self.geom.image_id),
            sk: keys::letter_sk(self.line_id, self.word_id, self.letter_id),
        }
    }

    fn to_item(&self) -> Item {
        let mut item = Item::with_capacity(16);
        keys::put_pk_sk(
            &mut item,
            keys::image_pk(&self.geom.image_id),
            keys::letter_sk(self.line_id, self.word_id, self.letter_id),
        );
        item.insert("TYPE".into(), Attr::s(Self::TYPE.as_str()));
        self.geom.write_fields(&mut item);
        item
    }

    fn from_item(item: &Item) -> Result<Self> {
        let geom = TextGeometry::from_item(item)?;
        let sk = item.require_s("SK")?;
        let parts: Vec<&str> = sk.split('#').collect();
        if parts.len() < 6 || parts[0] != "LINE" || parts[2] != "WORD" || parts[4] != "LETTER" {
            return Err(Error::validation(format!(
                "Invalid SK format for Letter: {sk}"
            )));
        }
        Self::new(
            keys::parse_padded_u32(parts[1])?,
            keys::parse_padded_u32(parts[3])?,
            keys::parse_padded_u32(parts[5])?,
            geom,
        )
    }
}
