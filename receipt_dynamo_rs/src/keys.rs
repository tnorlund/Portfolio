//! Single-table key construction matching the Python entity `key` / `gsi*_key` methods.

use std::fmt::Write as _;

use crate::attr::{insert_key, Attr, Item};
use crate::error::{Error, Result};

/// GSI names as created on the receipts table.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash)]
pub enum Gsi {
    Gsi1,
    Gsi2,
    Gsi3,
    Gsi4,
    GsiType,
}

impl Gsi {
    pub const fn index_name(self) -> &'static str {
        match self {
            Self::Gsi1 => "GSI1",
            Self::Gsi2 => "GSI2",
            Self::Gsi3 => "GSI3",
            Self::Gsi4 => "GSI4",
            Self::GsiType => "GSITYPE",
        }
    }

    pub const fn pk_attr(self) -> &'static str {
        match self {
            Self::Gsi1 => "GSI1PK",
            Self::Gsi2 => "GSI2PK",
            Self::Gsi3 => "GSI3PK",
            Self::Gsi4 => "GSI4PK",
            Self::GsiType => "TYPE",
        }
    }

    pub const fn sk_attr(self) -> Option<&'static str> {
        match self {
            Self::Gsi1 => Some("GSI1SK"),
            Self::Gsi2 => Some("GSI2SK"),
            Self::Gsi3 => Some("GSI3SK"),
            Self::Gsi4 => Some("GSI4SK"),
            Self::GsiType => None,
        }
    }
}

/// GSI4 sort-key prefixes used by the receipt-details access pattern.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Gsi4Prefix {
    Receipt = 0,
    Place = 1,
    Line = 2,
    Word = 3,
    Label = 4,
    Summary = 5,
    Barcode = 6,
}

impl Gsi4Prefix {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Receipt => "0_RECEIPT",
            Self::Place => "1_PLACE",
            Self::Line => "2_LINE",
            Self::Word => "3_WORD",
            Self::Label => "4_LABEL",
            Self::Summary => "5_SUMMARY",
            Self::Barcode => "6_BARCODE",
        }
    }
}

/// DynamoDB TYPE attribute values for the entities this crate serializes.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum EntityType {
    Image,
    Line,
    Word,
    Letter,
    Receipt,
    ReceiptLine,
    ReceiptWord,
    ReceiptLetter,
    ReceiptWordLabel,
    ReceiptBarcode,
    Job,
    OcrJob,
}

impl EntityType {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Image => "IMAGE",
            Self::Line => "LINE",
            Self::Word => "WORD",
            Self::Letter => "LETTER",
            Self::Receipt => "RECEIPT",
            Self::ReceiptLine => "RECEIPT_LINE",
            Self::ReceiptWord => "RECEIPT_WORD",
            Self::ReceiptLetter => "RECEIPT_LETTER",
            Self::ReceiptWordLabel => "RECEIPT_WORD_LABEL",
            Self::ReceiptBarcode => "RECEIPT_BARCODE",
            Self::Job => "JOB",
            Self::OcrJob => "OCR_JOB",
        }
    }

    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "IMAGE" => Some(Self::Image),
            "LINE" => Some(Self::Line),
            "WORD" => Some(Self::Word),
            "LETTER" => Some(Self::Letter),
            "RECEIPT" => Some(Self::Receipt),
            "RECEIPT_LINE" => Some(Self::ReceiptLine),
            "RECEIPT_WORD" => Some(Self::ReceiptWord),
            "RECEIPT_LETTER" => Some(Self::ReceiptLetter),
            "RECEIPT_WORD_LABEL" => Some(Self::ReceiptWordLabel),
            "RECEIPT_BARCODE" => Some(Self::ReceiptBarcode),
            "JOB" => Some(Self::Job),
            "OCR_JOB" => Some(Self::OcrJob),
            _ => None,
        }
    }
}

/// Zero-pad an id the way Python `:05d` does (expands past 5 digits).
#[inline]
pub fn pad5(id: u32) -> String {
    if id < 100_000 {
        let mut tmp = [b'0'; 5];
        let mut n = id;
        for i in (0..5).rev() {
            tmp[i] = b'0' + (n % 10) as u8;
            n /= 10;
        }
        // ASCII digits are valid UTF-8.
        unsafe { String::from_utf8_unchecked(tmp.to_vec()) }
    } else {
        format!("{id:05}")
    }
}

#[inline]
pub fn push_pad5(buf: &mut String, id: u32) {
    if id < 100_000 {
        let mut tmp = [b'0'; 5];
        let mut n = id;
        for i in (0..5).rev() {
            tmp[i] = b'0' + (n % 10) as u8;
            n /= 10;
        }
        buf.push_str(unsafe { std::str::from_utf8_unchecked(&tmp) });
    } else {
        let _ = write!(buf, "{id:05}");
    }
}

#[inline]
pub fn image_pk(image_id: &str) -> String {
    let mut s = String::with_capacity(6 + image_id.len());
    s.push_str("IMAGE#");
    s.push_str(image_id);
    s
}

#[inline]
pub fn receipt_scope(image_id: &str, receipt_id: u32) -> String {
    let mut s = String::with_capacity(6 + image_id.len() + 9 + 5);
    s.push_str("IMAGE#");
    s.push_str(image_id);
    s.push_str("#RECEIPT#");
    push_pad5(&mut s, receipt_id);
    s
}

#[inline]
pub fn receipt_sk(receipt_id: u32) -> String {
    let mut s = String::with_capacity(8 + 5);
    s.push_str("RECEIPT#");
    push_pad5(&mut s, receipt_id);
    s
}

#[inline]
pub fn line_sk(line_id: u32) -> String {
    let mut s = String::with_capacity(5 + 5);
    s.push_str("LINE#");
    push_pad5(&mut s, line_id);
    s
}

#[inline]
pub fn word_sk(line_id: u32, word_id: u32) -> String {
    let mut s = String::with_capacity(5 + 5 + 6 + 5);
    s.push_str("LINE#");
    push_pad5(&mut s, line_id);
    s.push_str("#WORD#");
    push_pad5(&mut s, word_id);
    s
}

#[inline]
pub fn letter_sk(line_id: u32, word_id: u32, letter_id: u32) -> String {
    let mut s = String::with_capacity(5 + 5 + 6 + 5 + 8 + 5);
    s.push_str("LINE#");
    push_pad5(&mut s, line_id);
    s.push_str("#WORD#");
    push_pad5(&mut s, word_id);
    s.push_str("#LETTER#");
    push_pad5(&mut s, letter_id);
    s
}

#[inline]
pub fn receipt_line_sk(receipt_id: u32, line_id: u32) -> String {
    let mut s = String::with_capacity(8 + 5 + 6 + 5);
    s.push_str("RECEIPT#");
    push_pad5(&mut s, receipt_id);
    s.push_str("#LINE#");
    push_pad5(&mut s, line_id);
    s
}

#[inline]
pub fn receipt_word_sk(receipt_id: u32, line_id: u32, word_id: u32) -> String {
    let mut s = String::with_capacity(8 + 5 + 6 + 5 + 6 + 5);
    s.push_str("RECEIPT#");
    push_pad5(&mut s, receipt_id);
    s.push_str("#LINE#");
    push_pad5(&mut s, line_id);
    s.push_str("#WORD#");
    push_pad5(&mut s, word_id);
    s
}

#[inline]
pub fn receipt_letter_sk(receipt_id: u32, line_id: u32, word_id: u32, letter_id: u32) -> String {
    let mut s = String::with_capacity(8 + 5 + 6 + 5 + 6 + 5 + 8 + 5);
    s.push_str("RECEIPT#");
    push_pad5(&mut s, receipt_id);
    s.push_str("#LINE#");
    push_pad5(&mut s, line_id);
    s.push_str("#WORD#");
    push_pad5(&mut s, word_id);
    s.push_str("#LETTER#");
    push_pad5(&mut s, letter_id);
    s
}

#[inline]
pub fn receipt_word_label_sk(receipt_id: u32, line_id: u32, word_id: u32, label: &str) -> String {
    let mut s = String::with_capacity(8 + 5 + 6 + 5 + 6 + 5 + 7 + label.len());
    s.push_str("RECEIPT#");
    push_pad5(&mut s, receipt_id);
    s.push_str("#LINE#");
    push_pad5(&mut s, line_id);
    s.push_str("#WORD#");
    push_pad5(&mut s, word_id);
    s.push_str("#LABEL#");
    s.push_str(label);
    s
}

#[inline]
pub fn receipt_barcode_sk(receipt_id: u32, barcode_id: u32) -> String {
    let mut s = String::with_capacity(8 + 5 + 9 + 5);
    s.push_str("RECEIPT#");
    push_pad5(&mut s, receipt_id);
    s.push_str("#BARCODE#");
    push_pad5(&mut s, barcode_id);
    s
}

/// GSI1PK for labels is padded with underscores to exactly 40 characters.
pub fn label_gsi1_pk(label: &str) -> String {
    let mut s = String::with_capacity(40);
    s.push_str("LABEL#");
    s.push_str(label);
    while s.len() < 40 {
        s.push('_');
    }
    s
}

pub fn gsi4_line_sk(line_id: u32) -> String {
    let mut s = String::with_capacity(7 + 5);
    s.push_str("2_LINE#");
    push_pad5(&mut s, line_id);
    s
}

pub fn gsi4_word_sk(line_id: u32, word_id: u32) -> String {
    let mut s = String::with_capacity(7 + 5 + 1 + 5);
    s.push_str("3_WORD#");
    push_pad5(&mut s, line_id);
    s.push('#');
    push_pad5(&mut s, word_id);
    s
}

pub fn gsi4_label_sk(line_id: u32, word_id: u32, label: &str) -> String {
    let mut s = String::with_capacity(8 + 5 + 1 + 5 + 1 + label.len());
    s.push_str("4_LABEL#");
    push_pad5(&mut s, line_id);
    s.push('#');
    push_pad5(&mut s, word_id);
    s.push('#');
    s.push_str(label);
    s
}

pub fn gsi4_barcode_sk(barcode_id: u32) -> String {
    let mut s = String::with_capacity(10 + 5);
    s.push_str("6_BARCODE#");
    push_pad5(&mut s, barcode_id);
    s
}

pub fn job_pk(job_id: &str) -> String {
    let mut s = String::with_capacity(4 + job_id.len());
    s.push_str("JOB#");
    s.push_str(job_id);
    s
}

pub fn ocr_job_sk(job_id: &str) -> String {
    let mut s = String::with_capacity(8 + job_id.len());
    s.push_str("OCR_JOB#");
    s.push_str(job_id);
    s
}

/// RFC-4122 UUID v4 or v5 with variant `[89ab]`, matching Python `UUID_V4_REGEX`.
pub fn assert_valid_uuid(value: &str) -> Result<()> {
    if is_valid_uuid(value) {
        Ok(())
    } else {
        Err(Error::validation("uuid must be a valid UUIDv4"))
    }
}

pub fn is_valid_uuid(value: &str) -> bool {
    let b = value.as_bytes();
    if b.len() != 36 {
        return false;
    }
    // 8-4-4-4-12 with hyphens at 8, 13, 18, 23
    if b[8] != b'-' || b[13] != b'-' || b[18] != b'-' || b[23] != b'-' {
        return false;
    }
    // version nibble: 4 or 5
    if b[14] != b'4' && b[14] != b'5' {
        return false;
    }
    // variant nibble: 8, 9, a, b (either case)
    match b[19] {
        b'8' | b'9' | b'a' | b'b' | b'A' | b'B' => {}
        _ => return false,
    }
    hex_group(&b[0..8])
        && hex_group(&b[9..13])
        && hex_group(&b[14..18])
        && hex_group(&b[19..23])
        && hex_group(&b[24..36])
}

fn hex_group(bytes: &[u8]) -> bool {
    bytes.iter().all(|c| c.is_ascii_hexdigit())
}

pub fn put_pk_sk(item: &mut Item, pk: String, sk: String) {
    insert_key(item, "PK", pk);
    insert_key(item, "SK", sk);
}

pub fn put_gsi(item: &mut Item, pk_name: &str, pk: String, sk_name: &str, sk: String) {
    item.insert(pk_name.to_string(), Attr::s(pk));
    item.insert(sk_name.to_string(), Attr::s(sk));
}

pub fn split_hash<'a>(value: &'a str, expected_prefix: &str) -> Result<&'a str> {
    let mut parts = value.splitn(2, '#');
    let prefix = parts.next().unwrap_or("");
    let rest = parts.next();
    if prefix != expected_prefix {
        return Err(Error::validation(format!(
            "expected {expected_prefix}#..., got {value}"
        )));
    }
    rest.ok_or_else(|| Error::validation(format!("missing id in {value}")))
}

pub fn parse_padded_u32(value: &str) -> Result<u32> {
    value
        .parse()
        .map_err(|_| Error::validation(format!("invalid padded id {value}")))
}
