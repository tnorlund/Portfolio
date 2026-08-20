use crate::attr::{Attr, Item, ItemExt};
use crate::error::{Error, Result};
use crate::keys::{self, EntityType};

use super::geometry_entity::TextGeometry;
use super::{require_positive, Entity, PrimaryKey};

#[derive(Clone, Debug, PartialEq)]
pub struct Line {
    pub line_id: u32,
    pub geom: TextGeometry,
}

impl Line {
    pub fn new(line_id: u32, geom: TextGeometry) -> Result<Self> {
        require_positive("line_id", line_id)?;
        Ok(Self { line_id, geom })
    }

    pub fn from_item(item: &Item) -> Result<Self> {
        <Self as Entity>::from_item(item)
    }
}

impl Entity for Line {
    const TYPE: EntityType = EntityType::Line;

    fn primary_key(&self) -> PrimaryKey {
        PrimaryKey {
            pk: keys::image_pk(&self.geom.image_id),
            sk: keys::line_sk(self.line_id),
        }
    }

    fn to_item(&self) -> Item {
        let mut item = Item::with_capacity(16);
        let pk = keys::image_pk(&self.geom.image_id);
        let sk = keys::line_sk(self.line_id);
        keys::put_pk_sk(&mut item, pk.clone(), sk.clone());
        item.insert("TYPE".into(), Attr::s(Self::TYPE.as_str()));
        keys::put_gsi(&mut item, "GSI1PK", pk, "GSI1SK", sk);
        self.geom.write_fields(&mut item);
        item
    }

    fn from_item(item: &Item) -> Result<Self> {
        let geom = TextGeometry::from_item(item)?;
        let sk = item.require_s("SK")?;
        let parts: Vec<&str> = sk.split('#').collect();
        if parts.len() < 2 || parts[0] != "LINE" {
            return Err(Error::validation(format!(
                "Invalid SK format for Line: {sk}"
            )));
        }
        Ok(Self {
            line_id: keys::parse_padded_u32(parts[1])?,
            geom,
        })
    }
}
