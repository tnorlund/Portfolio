//! DynamoDB AttributeValue encoding that matches the Python low-level client.

use std::collections::HashMap;
use std::fmt::Write as _;
use std::str::FromStr;

use rust_decimal::Decimal;
use rust_decimal::RoundingStrategy;
use serde::{Deserialize, Serialize};

use crate::error::{Error, Result};

/// A DynamoDB item: attribute name → AttributeValue.
pub type Item = HashMap<String, Attr>;

/// Low-level DynamoDB AttributeValue, serialized like boto3's client format.
#[allow(non_snake_case)]
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum Attr {
    #[serde(rename = "S")]
    S { S: String },
    #[serde(rename = "N")]
    N { N: String },
    #[serde(rename = "BOOL")]
    Bool { BOOL: bool },
    #[serde(rename = "NULL")]
    Null { NULL: bool },
    #[serde(rename = "M")]
    M { M: HashMap<String, Attr> },
    #[serde(rename = "L")]
    L { L: Vec<Attr> },
}

impl Attr {
    #[inline]
    pub fn s(value: impl Into<String>) -> Self {
        Self::S { S: value.into() }
    }

    #[inline]
    pub fn n_str(value: impl Into<String>) -> Self {
        Self::N { N: value.into() }
    }

    #[inline]
    pub fn n_int(value: i64) -> Self {
        Self::N {
            N: value.to_string(),
        }
    }

    #[inline]
    pub fn n_uint(value: u32) -> Self {
        Self::N {
            N: value.to_string(),
        }
    }

    #[inline]
    pub fn n_float(value: f64, decimal_places: u32) -> Self {
        Self::N {
            N: format_float(value, decimal_places),
        }
    }

    #[inline]
    pub fn bool(value: bool) -> Self {
        Self::Bool { BOOL: value }
    }

    #[inline]
    pub fn null() -> Self {
        Self::Null { NULL: true }
    }

    #[inline]
    pub fn map(value: HashMap<String, Attr>) -> Self {
        Self::M { M: value }
    }

    #[inline]
    pub fn optional_s(value: Option<&str>) -> Self {
        match value {
            Some(s) if !s.is_empty() => Self::s(s),
            _ => Self::null(),
        }
    }

    pub fn as_s(&self) -> Result<&str> {
        match self {
            Self::S { S } => Ok(S),
            _ => Err(Error::validation("expected string attribute")),
        }
    }

    pub fn as_n_f64(&self) -> Result<f64> {
        match self {
            Self::N { N } => N
                .parse()
                .map_err(|_| Error::validation(format!("invalid number {N}"))),
            _ => Err(Error::validation("expected number attribute")),
        }
    }

    pub fn as_n_i64(&self) -> Result<i64> {
        match self {
            Self::N { N } => N
                .parse()
                .map_err(|_| Error::validation(format!("invalid integer {N}"))),
            _ => Err(Error::validation("expected number attribute")),
        }
    }

    pub fn as_n_u32(&self) -> Result<u32> {
        match self {
            Self::N { N } => N
                .parse()
                .map_err(|_| Error::validation(format!("invalid unsigned integer {N}"))),
            _ => Err(Error::validation("expected number attribute")),
        }
    }

    pub fn as_bool(&self) -> Result<bool> {
        match self {
            Self::Bool { BOOL } => Ok(*BOOL),
            _ => Err(Error::validation("expected bool attribute")),
        }
    }

    pub fn as_map(&self) -> Result<&HashMap<String, Attr>> {
        match self {
            Self::M { M } => Ok(M),
            _ => Err(Error::validation("expected map attribute")),
        }
    }

    pub fn is_null(&self) -> bool {
        matches!(self, Self::Null { NULL: true })
    }

    /// Convert to the tagged JSON object Python's boto3 client uses.
    pub fn to_wire_json(&self) -> serde_json::Value {
        match self {
            Self::S { S } => serde_json::json!({ "S": S }),
            Self::N { N } => serde_json::json!({ "N": N }),
            Self::Bool { BOOL } => serde_json::json!({ "BOOL": BOOL }),
            Self::Null { NULL } => serde_json::json!({ "NULL": NULL }),
            Self::M { M } => {
                let mut map = serde_json::Map::new();
                for (k, v) in M {
                    map.insert(k.clone(), v.to_wire_json());
                }
                serde_json::json!({ "M": map })
            }
            Self::L { L } => {
                serde_json::json!({ "L": L.iter().map(Self::to_wire_json).collect::<Vec<_>>() })
            }
        }
    }
}

impl ItemExt for Item {
    fn insert_s(&mut self, key: impl Into<String>, value: impl Into<String>) {
        self.insert(key.into(), Attr::s(value));
    }

    fn require<'a>(&'a self, key: &str) -> Result<&'a Attr> {
        self.get(key)
            .ok_or_else(|| Error::validation(format!("missing required key {key}")))
    }

    fn require_s(&self, key: &str) -> Result<&str> {
        self.require(key)?.as_s()
    }

    fn to_wire_json(&self) -> serde_json::Value {
        let mut map = serde_json::Map::new();
        for (k, v) in self {
            map.insert(k.clone(), v.to_wire_json());
        }
        serde_json::Value::Object(map)
    }
}

pub trait ItemExt {
    fn insert_s(&mut self, key: impl Into<String>, value: impl Into<String>);
    fn require(&self, key: &str) -> Result<&Attr>;
    fn require_s(&self, key: &str) -> Result<&str>;
    fn to_wire_json(&self) -> serde_json::Value;
}

/// Format a float the way Python `receipt_dynamo.entities.util._format_float` does:
/// `Decimal(str(value)).quantize(10^-places, ROUND_HALF_UP)` then fixed-point.
pub fn format_float(value: f64, decimal_places: u32) -> String {
    if !value.is_finite() {
        return if value.is_nan() {
            "NaN".to_string()
        } else if value.is_sign_negative() {
            format!("-{}.{}", "inf", "0".repeat(decimal_places as usize))
        } else {
            format!("{}.{}", "inf", "0".repeat(decimal_places as usize))
        };
    }

    // Python `str(float)` uses shortest round-trip. Rust Display is the same
    // for finite values that are not huge scientific-notation edge cases.
    let shortest = shortest_decimal_str(value);
    let decimal = Decimal::from_str(&shortest)
        .or_else(|_| Decimal::from_str(&format!("{value}")))
        .unwrap_or_else(|_| Decimal::from_f64_retain(value).unwrap_or(Decimal::ZERO));
    let quantized =
        decimal.round_dp_with_strategy(decimal_places, RoundingStrategy::MidpointAwayFromZero);
    format_decimal_fixed(quantized, decimal_places)
}

fn shortest_decimal_str(value: f64) -> String {
    // Match CPython's `str(float)` for ordinary magnitudes: no scientific
    // notation between 1e-4 (exclusive) and 1e16 (exclusive).
    if value == 0.0 {
        return if value.is_sign_negative() {
            "-0.0".to_string()
        } else {
            "0.0".to_string()
        };
    }
    let abs = value.abs();
    if abs >= 1e16 || abs < 1e-4 {
        // Python uses scientific notation here. rust_decimal can parse `1e-5`.
        format!("{value}")
    } else {
        // `format!("{}", f64)` already uses shortest round-trip.
        let mut s = format!("{value}");
        if !s.contains('.') && !s.contains('e') && !s.contains('E') {
            s.push_str(".0");
        }
        s
    }
}

fn format_decimal_fixed(value: Decimal, decimal_places: u32) -> String {
    let mut out = String::with_capacity(24 + decimal_places as usize);
    let formatted = value.to_string();
    // rust_decimal may omit the decimal point for integers.
    if let Some(dot) = formatted.find('.') {
        out.push_str(&formatted[..dot]);
        out.push('.');
        let frac = &formatted[dot + 1..];
        out.push_str(frac);
        for _ in frac.len()..(decimal_places as usize) {
            out.push('0');
        }
        if frac.len() > decimal_places as usize {
            out.truncate(dot + 1 + decimal_places as usize);
        }
    } else {
        out.push_str(&formatted);
        out.push('.');
        for _ in 0..decimal_places {
            out.push('0');
        }
    }
    // Handle sign of zero: Python `Decimal('0').quantize` is `0.000...`.
    if out.starts_with("-0.") && out[2..].bytes().all(|b| b == b'0' || b == b'.') {
        out.remove(0);
    }
    out
}

/// Serialize a bounding box with 20 decimal places (geometry entities).
pub fn serialize_bounding_box(x: f64, y: f64, width: f64, height: f64) -> Attr {
    let mut m = HashMap::with_capacity(4);
    m.insert("x".into(), Attr::n_float(x, 20));
    m.insert("y".into(), Attr::n_float(y, 20));
    m.insert("width".into(), Attr::n_float(width, 20));
    m.insert("height".into(), Attr::n_float(height, 20));
    Attr::map(m)
}

/// Serialize an (x, y) point with 20 decimal places.
pub fn serialize_point20(x: f64, y: f64) -> Attr {
    let mut m = HashMap::with_capacity(2);
    m.insert("x".into(), Attr::n_float(x, 20));
    m.insert("y".into(), Attr::n_float(y, 20));
    Attr::map(m)
}

/// Serialize an (x, y) point with 18 decimal places (Receipt corners).
pub fn serialize_point18(x: f64, y: f64) -> Attr {
    let mut m = HashMap::with_capacity(2);
    m.insert("x".into(), Attr::n_float(x, 18));
    m.insert("y".into(), Attr::n_float(y, 18));
    Attr::map(m)
}

pub fn serialize_confidence(confidence: f64) -> Attr {
    Attr::n_float(confidence, 2)
}

pub fn deserialize_point(attr: &Attr) -> Result<crate::entities::Point> {
    let m = attr.as_map()?;
    let x = m
        .get("x")
        .ok_or_else(|| Error::validation("point missing x"))?
        .as_n_f64()?;
    let y = m
        .get("y")
        .ok_or_else(|| Error::validation("point missing y"))?
        .as_n_f64()?;
    Ok(crate::entities::Point { x, y })
}

pub fn deserialize_bbox(attr: &Attr) -> Result<crate::geometry::BoundingBox> {
    let m = attr.as_map()?;
    Ok(crate::geometry::BoundingBox {
        x: m.get("x")
            .ok_or_else(|| Error::validation("bounding_box missing x"))?
            .as_n_f64()?,
        y: m.get("y")
            .ok_or_else(|| Error::validation("bounding_box missing y"))?
            .as_n_f64()?,
        width: m
            .get("width")
            .ok_or_else(|| Error::validation("bounding_box missing width"))?
            .as_n_f64()?,
        height: m
            .get("height")
            .ok_or_else(|| Error::validation("bounding_box missing height"))?
            .as_n_f64()?,
    })
}

/// DynamoDB batch-write limit.
pub const BATCH_WRITE_LIMIT: usize = 25;
/// DynamoDB batch-get limit.
pub const BATCH_GET_LIMIT: usize = 100;

/// Pre-size an item map for primary key + GSIs + payload.
#[inline]
pub fn item_with_capacity(n: usize) -> Item {
    HashMap::with_capacity(n)
}

pub fn insert_key(item: &mut Item, name: &str, value: String) {
    item.insert(name.to_string(), Attr::s(value));
}

/// Helper used by `format_decimal_fixed` tests; keep `Write` imported.
#[allow(dead_code)]
fn _keep_write(buf: &mut String, n: u32) {
    let _ = write!(buf, "{n:05}");
}
