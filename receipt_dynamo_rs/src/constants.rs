//! Status enums matching `receipt_dynamo.constants`.

use serde::{Deserialize, Serialize};

macro_rules! str_enum {
    ($name:ident, $($variant:ident => $value:expr),+ $(,)?) => {
        #[derive(Clone, Copy, Debug, Eq, PartialEq, Hash, Serialize, Deserialize)]
        pub enum $name {
            $($variant),+
        }

        impl $name {
            pub const fn as_str(self) -> &'static str {
                match self {
                    $(Self::$variant => $value),+
                }
            }

            pub fn parse(value: &str) -> crate::error::Result<Self> {
                match value {
                    $($value => Ok(Self::$variant),)+
                    other => Err(crate::error::Error::validation(format!(
                        "{} must be one of: {}\nGot: {other}",
                        stringify!($name),
                        [$($value),+].join(", ")
                    ))),
                }
            }
        }

        impl AsRef<str> for $name {
            fn as_ref(&self) -> &str {
                self.as_str()
            }
        }

        impl std::fmt::Display for $name {
            fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
                f.write_str(self.as_str())
            }
        }
    };
}

str_enum!(
    ValidationStatus,
    None => "NONE",
    Pending => "PENDING",
    Valid => "VALID",
    Invalid => "INVALID",
    NeedsReview => "NEEDS_REVIEW",
);

str_enum!(
    BatchStatus,
    Pending => "PENDING",
    Validating => "VALIDATING",
    InProgress => "IN_PROGRESS",
    Finalizing => "FINALIZING",
    Completed => "COMPLETED",
    Failed => "FAILED",
    Expired => "EXPIRED",
    Canceling => "CANCELING",
    Cancelled => "CANCELLED",
);

str_enum!(
    BatchType,
    Completion => "COMPLETION",
    Embedding => "EMBEDDING",
    WordEmbedding => "WORD_EMBEDDING",
    LineEmbedding => "LINE_EMBEDDING",
);

str_enum!(
    JobStatus,
    Pending => "pending",
    Running => "running",
    Succeeded => "succeeded",
    Failed => "failed",
    Cancelled => "cancelled",
    Interrupted => "interrupted",
);

str_enum!(
    LabelStatus,
    Active => "ACTIVE",
    Deprecated => "DEPRECATED",
);

str_enum!(
    EmbeddingStatus,
    None => "NONE",
    Pending => "PENDING",
    Success => "SUCCESS",
    Failed => "FAILED",
    Noise => "NOISE",
);

str_enum!(
    SectionType,
    Header => "HEADER",
    ItemsValue => "ITEMS_VALUE",
    ItemsDescription => "ITEMS_DESCRIPTION",
    Storefront => "STOREFRONT",
    Address => "ADDRESS",
    Items => "ITEMS",
    SectionHeader => "SECTION_HEADER",
    Summary => "SUMMARY",
    TotalLine => "TOTAL_LINE",
    Payment => "PAYMENT",
    Survey => "SURVEY",
    Footer => "FOOTER",
    Barcode => "BARCODE",
    TransactionInfo => "TRANSACTION_INFO",
);

str_enum!(
    MerchantValidationStatus,
    Matched => "MATCHED",
    NoMatch => "NO_MATCH",
    Unsure => "UNSURE",
);

str_enum!(
    ValidationMethod,
    PhoneLookup => "PHONE_LOOKUP",
    AddressLookup => "ADDRESS_LOOKUP",
    NearbyLookup => "NEARBY_LOOKUP",
    TextSearch => "TEXT_SEARCH",
    Inference => "INFERENCE",
);

str_enum!(
    PassNumber,
    First => "FIRST_PASS",
    Second => "SECOND_PASS",
);

str_enum!(
    OcrStatus,
    Pending => "PENDING",
    Completed => "COMPLETED",
    Failed => "FAILED",
);

str_enum!(
    OcrJobType,
    Refinement => "REFINEMENT",
    FirstPass => "FIRST_PASS",
    RegionalReocr => "REGIONAL_REOCR",
    LineItemRefine => "LINE_ITEM_REFINE",
);

str_enum!(
    ImageType,
    Scan => "SCAN",
    Photo => "PHOTO",
    Native => "NATIVE",
);

str_enum!(
    CompactionState,
    Pending => "PENDING",
    Processing => "PROCESSING",
    Completed => "COMPLETED",
    Failed => "FAILED",
);

str_enum!(
    CoreMlExportStatus,
    Pending => "PENDING",
    Running => "RUNNING",
    Succeeded => "SUCCEEDED",
    Failed => "FAILED",
);

str_enum!(
    JobPriority,
    Low => "low",
    Medium => "medium",
    High => "high",
    Critical => "critical",
);

/// SMART re-OCR strategies shared with the Swift worker.
pub const VALID_REOCR_STRATEGIES: &[&str] = &["plain", "invert", "deskew", "upscale2x"];
