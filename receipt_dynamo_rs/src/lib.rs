//! High-performance DynamoDB entity layer for receipt OCR data.
//!
//! Wire format and key schema match the Python `receipt_dynamo` package so
//! both implementations can share one table.

#![deny(clippy::all)]
#![allow(clippy::too_many_arguments)]
#![allow(clippy::type_complexity)]

pub mod amounts;
pub mod attr;
pub mod circuit_breaker;
pub mod client;
pub mod constants;
pub mod entities;
pub mod error;
pub mod geometry;
pub mod keys;
pub mod labels;
pub mod retry;
pub mod store;

pub use amounts::{is_grand_total_line, looks_like_receipt_amount, parse_receipt_amount};
pub use attr::{format_float, Attr, Item};
pub use circuit_breaker::{CircuitBreaker, CircuitState};
pub use client::ReceiptDynamo;
pub use constants::*;
pub use entities::{
    AnyEntity, CdnFields, Entity, Image, Job, Letter, Line, OcrJob, Point, PrimaryKey, Receipt,
    ReceiptBarcode, ReceiptLetter, ReceiptLine, ReceiptWord, ReceiptWordLabel, TextGeometry, Word,
};
pub use error::{Error, Result};
pub use geometry::Geometry;
pub use keys::{pad5, EntityType, Gsi, Gsi4Prefix};
pub use labels::{
    canonical_label_name, invalid_label_message, is_core_label, normalize_core_label,
    normalize_label_alias, CORE_LABELS, CORE_LABEL_NAMES, NON_CORE_LABEL_ALIASES,
};
pub use retry::retry_with_backoff;
pub use store::{InMemoryStore, Store};

#[cfg(feature = "aws")]
pub use store::AwsStore;
