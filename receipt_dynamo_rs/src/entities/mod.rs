//! DynamoDB entities. Serialization matches the Python `to_item` / `from_item` methods.

mod barcode;
mod geometry_entity;
mod image;
mod job;
mod letter;
mod line;
mod ocr_job;
mod receipt;
mod receipt_letter;
mod receipt_line;
mod receipt_word;
mod receipt_word_label;
mod word;

pub use barcode::ReceiptBarcode;
pub use geometry_entity::TextGeometry;
pub use image::Image;
pub use job::Job;
pub use letter::Letter;
pub use line::Line;
pub use ocr_job::OcrJob;
pub use receipt::Receipt;
pub use receipt_letter::ReceiptLetter;
pub use receipt_line::ReceiptLine;
pub use receipt_word::ReceiptWord;
pub use receipt_word_label::ReceiptWordLabel;
pub use word::Word;

use crate::attr::{Attr, Item};
use crate::error::{Error, Result};
use crate::keys::EntityType;

/// Primary key pair.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PrimaryKey {
    pub pk: String,
    pub sk: String,
}

impl PrimaryKey {
    pub fn to_item(&self) -> Item {
        let mut item = Item::with_capacity(2);
        item.insert("PK".into(), Attr::s(self.pk.clone()));
        item.insert("SK".into(), Attr::s(self.sk.clone()));
        item
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Point {
    pub x: f64,
    pub y: f64,
}

impl Point {
    pub fn new(x: f64, y: f64) -> Result<Self> {
        if !x.is_finite() || !y.is_finite() {
            return Err(Error::validation("point x/y must be numbers"));
        }
        Ok(Self { x, y })
    }
}

/// Optional CDN object keys stored on Image and Receipt.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct CdnFields {
    pub sha256: Option<String>,
    pub cdn_s3_bucket: Option<String>,
    pub cdn_s3_key: Option<String>,
    pub cdn_webp_s3_key: Option<String>,
    pub cdn_avif_s3_key: Option<String>,
    pub cdn_thumbnail_s3_key: Option<String>,
    pub cdn_thumbnail_webp_s3_key: Option<String>,
    pub cdn_thumbnail_avif_s3_key: Option<String>,
    pub cdn_small_s3_key: Option<String>,
    pub cdn_small_webp_s3_key: Option<String>,
    pub cdn_small_avif_s3_key: Option<String>,
    pub cdn_medium_s3_key: Option<String>,
    pub cdn_medium_webp_s3_key: Option<String>,
    pub cdn_medium_avif_s3_key: Option<String>,
}

impl CdnFields {
    pub fn write_to_item(&self, item: &mut Item) {
        item.insert("sha256".into(), Attr::optional_s(self.sha256.as_deref()));
        // Image/Receipt write cdn_s3_bucket separately in Python before mixing
        // the remaining CDN fields. We write the full set here; callers that
        // already inserted cdn_s3_bucket will overwrite with the same value.
        for (name, value) in self.iter_fields() {
            item.insert(name.to_string(), Attr::optional_s(value));
        }
    }

    pub fn from_item(item: &Item) -> Self {
        let get = |k: &str| {
            item.get(k)
                .and_then(|a| a.as_s().ok())
                .map(|s| s.to_string())
        };
        Self {
            sha256: get("sha256"),
            cdn_s3_bucket: get("cdn_s3_bucket"),
            cdn_s3_key: get("cdn_s3_key"),
            cdn_webp_s3_key: get("cdn_webp_s3_key"),
            cdn_avif_s3_key: get("cdn_avif_s3_key"),
            cdn_thumbnail_s3_key: get("cdn_thumbnail_s3_key"),
            cdn_thumbnail_webp_s3_key: get("cdn_thumbnail_webp_s3_key"),
            cdn_thumbnail_avif_s3_key: get("cdn_thumbnail_avif_s3_key"),
            cdn_small_s3_key: get("cdn_small_s3_key"),
            cdn_small_webp_s3_key: get("cdn_small_webp_s3_key"),
            cdn_small_avif_s3_key: get("cdn_small_avif_s3_key"),
            cdn_medium_s3_key: get("cdn_medium_s3_key"),
            cdn_medium_webp_s3_key: get("cdn_medium_webp_s3_key"),
            cdn_medium_avif_s3_key: get("cdn_medium_avif_s3_key"),
        }
    }

    fn iter_fields(&self) -> [(&'static str, Option<&str>); 13] {
        [
            ("cdn_s3_bucket", self.cdn_s3_bucket.as_deref()),
            ("cdn_s3_key", self.cdn_s3_key.as_deref()),
            ("cdn_webp_s3_key", self.cdn_webp_s3_key.as_deref()),
            ("cdn_avif_s3_key", self.cdn_avif_s3_key.as_deref()),
            ("cdn_thumbnail_s3_key", self.cdn_thumbnail_s3_key.as_deref()),
            (
                "cdn_thumbnail_webp_s3_key",
                self.cdn_thumbnail_webp_s3_key.as_deref(),
            ),
            (
                "cdn_thumbnail_avif_s3_key",
                self.cdn_thumbnail_avif_s3_key.as_deref(),
            ),
            ("cdn_small_s3_key", self.cdn_small_s3_key.as_deref()),
            (
                "cdn_small_webp_s3_key",
                self.cdn_small_webp_s3_key.as_deref(),
            ),
            (
                "cdn_small_avif_s3_key",
                self.cdn_small_avif_s3_key.as_deref(),
            ),
            ("cdn_medium_s3_key", self.cdn_medium_s3_key.as_deref()),
            (
                "cdn_medium_webp_s3_key",
                self.cdn_medium_webp_s3_key.as_deref(),
            ),
            (
                "cdn_medium_avif_s3_key",
                self.cdn_medium_avif_s3_key.as_deref(),
            ),
        ]
    }
}

/// Trait implemented by every typed entity.
pub trait Entity: Sized {
    const TYPE: EntityType;
    fn primary_key(&self) -> PrimaryKey;
    fn to_item(&self) -> Item;
    fn from_item(item: &Item) -> Result<Self>;
}

/// Decode a stored item by its TYPE attribute.
#[derive(Clone, Debug)]
pub enum AnyEntity {
    Image(Image),
    Line(Line),
    Word(Word),
    Letter(Letter),
    Receipt(Receipt),
    ReceiptLine(ReceiptLine),
    ReceiptWord(ReceiptWord),
    ReceiptLetter(ReceiptLetter),
    ReceiptWordLabel(ReceiptWordLabel),
    ReceiptBarcode(ReceiptBarcode),
    Job(Job),
    OcrJob(OcrJob),
    Unknown(Item),
}

impl AnyEntity {
    pub fn from_item(item: &Item) -> Result<Self> {
        let ty = match item.get("TYPE") {
            Some(attr) => attr.as_s()?,
            None => return Ok(Self::Unknown(item.clone())),
        };
        Ok(match EntityType::parse(ty) {
            Some(EntityType::Image) => Self::Image(Image::from_item(item)?),
            Some(EntityType::Line) => Self::Line(Line::from_item(item)?),
            Some(EntityType::Word) => Self::Word(Word::from_item(item)?),
            Some(EntityType::Letter) => Self::Letter(Letter::from_item(item)?),
            Some(EntityType::Receipt) => Self::Receipt(Receipt::from_item(item)?),
            Some(EntityType::ReceiptLine) => Self::ReceiptLine(ReceiptLine::from_item(item)?),
            Some(EntityType::ReceiptWord) => Self::ReceiptWord(ReceiptWord::from_item(item)?),
            Some(EntityType::ReceiptLetter) => Self::ReceiptLetter(ReceiptLetter::from_item(item)?),
            Some(EntityType::ReceiptWordLabel) => {
                Self::ReceiptWordLabel(ReceiptWordLabel::from_item(item)?)
            }
            Some(EntityType::ReceiptBarcode) => {
                Self::ReceiptBarcode(ReceiptBarcode::from_item(item)?)
            }
            Some(EntityType::Job) => Self::Job(Job::from_item(item)?),
            Some(EntityType::OcrJob) => Self::OcrJob(OcrJob::from_item(item)?),
            None => Self::Unknown(item.clone()),
        })
    }
}

pub(crate) fn require_positive(name: &str, value: u32) -> Result<u32> {
    if value == 0 {
        Err(Error::validation(format!("{name} must be positive")))
    } else {
        Ok(value)
    }
}

pub(crate) fn validate_confidence(confidence: f64) -> Result<f64> {
    if !confidence.is_finite() {
        return Err(Error::validation("confidence must be a float"));
    }
    if confidence <= 0.0 || confidence > 1.0 {
        return Err(Error::validation("confidence must be between 0 and 1"));
    }
    Ok(confidence)
}

pub(crate) fn validate_positive_dimensions(width: u32, height: u32) -> Result<()> {
    if width == 0 || height == 0 {
        return Err(Error::validation(
            "width and height must be positive integers",
        ));
    }
    Ok(())
}
