use crate::attr::{
    deserialize_bbox, deserialize_point, serialize_bounding_box, serialize_confidence,
    serialize_point20, Attr, Item, ItemExt,
};
use crate::error::{Error, Result};
use crate::geometry::{BoundingBox, Corners, Geometry};
use crate::keys::assert_valid_uuid;

use super::{validate_confidence, Point};

/// Geometry + text payload shared by Line/Word/Letter and receipt variants.
#[derive(Clone, Debug, PartialEq)]
pub struct TextGeometry {
    pub image_id: String,
    pub text: String,
    pub geometry: Geometry,
    pub confidence: f64,
}

impl TextGeometry {
    pub fn new(
        image_id: impl Into<String>,
        text: impl Into<String>,
        bounding_box: BoundingBox,
        corners: Corners,
        angle_degrees: f64,
        angle_radians: f64,
        confidence: f64,
    ) -> Result<Self> {
        let image_id = image_id.into();
        assert_valid_uuid(&image_id)?;
        let confidence = validate_confidence(confidence)?;
        let geometry = Geometry {
            bounding_box,
            corners,
            angle_degrees,
            angle_radians,
        };
        geometry.validate()?;
        Ok(Self {
            image_id,
            text: text.into(),
            geometry,
            confidence,
        })
    }

    pub fn write_fields(&self, item: &mut Item) {
        item.insert("text".into(), Attr::s(self.text.clone()));
        let bb = self.geometry.bounding_box;
        item.insert(
            "bounding_box".into(),
            serialize_bounding_box(bb.x, bb.y, bb.width, bb.height),
        );
        item.insert(
            "top_right".into(),
            serialize_point20(
                self.geometry.corners.top_right.x,
                self.geometry.corners.top_right.y,
            ),
        );
        item.insert(
            "top_left".into(),
            serialize_point20(
                self.geometry.corners.top_left.x,
                self.geometry.corners.top_left.y,
            ),
        );
        item.insert(
            "bottom_right".into(),
            serialize_point20(
                self.geometry.corners.bottom_right.x,
                self.geometry.corners.bottom_right.y,
            ),
        );
        item.insert(
            "bottom_left".into(),
            serialize_point20(
                self.geometry.corners.bottom_left.x,
                self.geometry.corners.bottom_left.y,
            ),
        );
        item.insert(
            "angle_degrees".into(),
            Attr::n_float(self.geometry.angle_degrees, 18),
        );
        item.insert(
            "angle_radians".into(),
            Attr::n_float(self.geometry.angle_radians, 18),
        );
        item.insert("confidence".into(), serialize_confidence(self.confidence));
    }

    pub fn from_item(item: &Item) -> Result<Self> {
        let pk = item.require_s("PK")?;
        let image_id = crate::keys::split_hash(pk, "IMAGE")?.to_string();
        let text = item.require_s("text")?.to_string();
        let bounding_box = deserialize_bbox(item.require("bounding_box")?)?;
        let corners = Corners {
            top_left: deserialize_point(item.require("top_left")?)?,
            top_right: deserialize_point(item.require("top_right")?)?,
            bottom_left: deserialize_point(item.require("bottom_left")?)?,
            bottom_right: deserialize_point(item.require("bottom_right")?)?,
        };
        let angle_degrees = item.require("angle_degrees")?.as_n_f64()?;
        let angle_radians = item.require("angle_radians")?.as_n_f64()?;
        let confidence = item.require("confidence")?.as_n_f64()?;
        Self::new(
            image_id,
            text,
            bounding_box,
            corners,
            angle_degrees,
            angle_radians,
            confidence,
        )
    }

    pub fn unit_box(image_id: impl Into<String>, text: impl Into<String>) -> Result<Self> {
        Self::new(
            image_id,
            text,
            BoundingBox::new(0.1, 0.2, 0.3, 0.4)?,
            Corners {
                top_left: Point { x: 0.1, y: 0.2 },
                top_right: Point { x: 0.4, y: 0.2 },
                bottom_left: Point { x: 0.1, y: 0.6 },
                bottom_right: Point { x: 0.4, y: 0.6 },
            },
            0.0,
            0.0,
            0.95,
        )
    }
}

pub fn embedding_status_of(item: &Item) -> Result<crate::constants::EmbeddingStatus> {
    match item.get("embedding_status") {
        Some(attr) if !attr.is_null() => crate::constants::EmbeddingStatus::parse(attr.as_s()?),
        _ => Ok(crate::constants::EmbeddingStatus::None),
    }
}

pub fn is_noise_of(item: &Item) -> Result<bool> {
    match item.get("is_noise") {
        Some(attr) if !attr.is_null() => attr.as_bool(),
        _ => Ok(false),
    }
}

pub fn write_receipt_fields(
    item: &mut Item,
    embedding_status: crate::constants::EmbeddingStatus,
    is_noise: bool,
) {
    item.insert(
        "embedding_status".into(),
        Attr::s(embedding_status.as_str()),
    );
    item.insert("is_noise".into(), Attr::bool(is_noise));
}

pub fn parse_receipt_line_word_sk(sk: &str) -> Result<(u32, u32, u32)> {
    // RECEIPT#{id}#LINE#{id}#WORD#{id}
    let parts: Vec<&str> = sk.split('#').collect();
    if parts.len() < 6 || parts[0] != "RECEIPT" || parts[2] != "LINE" || parts[4] != "WORD" {
        return Err(Error::validation(format!(
            "Invalid SK format for receipt word: {sk}"
        )));
    }
    Ok((
        crate::keys::parse_padded_u32(parts[1])?,
        crate::keys::parse_padded_u32(parts[3])?,
        crate::keys::parse_padded_u32(parts[5])?,
    ))
}
