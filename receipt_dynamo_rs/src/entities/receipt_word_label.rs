use crate::attr::{Attr, Item, ItemExt};
use crate::constants::ValidationStatus;
use crate::error::{Error, Result};
use crate::keys::{self, assert_valid_uuid, EntityType};

use super::{require_positive, Entity, PrimaryKey};

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ReceiptWordLabel {
    pub image_id: String,
    pub receipt_id: u32,
    pub line_id: u32,
    pub word_id: u32,
    pub label: String,
    pub reasoning: Option<String>,
    pub timestamp_added: String,
    pub validation_status: ValidationStatus,
    pub label_proposed_by: Option<String>,
    pub label_consolidated_from: Option<String>,
}

impl ReceiptWordLabel {
    pub fn new(
        image_id: impl Into<String>,
        receipt_id: u32,
        line_id: u32,
        word_id: u32,
        label: impl Into<String>,
        reasoning: Option<String>,
        timestamp_added: impl Into<String>,
    ) -> Result<Self> {
        let image_id = image_id.into();
        assert_valid_uuid(&image_id)?;
        require_positive("receipt_id", receipt_id)?;
        require_positive("line_id", line_id)?;
        require_positive("word_id", word_id)?;
        let label = label.into().to_ascii_uppercase();
        if label.is_empty() {
            return Err(Error::validation("label cannot be empty"));
        }
        if let Some(r) = reasoning.as_deref() {
            if r.is_empty() {
                return Err(Error::validation("reasoning cannot be empty"));
            }
        }
        Ok(Self {
            image_id,
            receipt_id,
            line_id,
            word_id,
            label,
            reasoning,
            timestamp_added: timestamp_added.into(),
            validation_status: ValidationStatus::None,
            label_proposed_by: None,
            label_consolidated_from: None,
        })
    }

    pub fn receipt_word_key(&self) -> PrimaryKey {
        PrimaryKey {
            pk: keys::image_pk(&self.image_id),
            sk: keys::receipt_word_sk(self.receipt_id, self.line_id, self.word_id),
        }
    }

    pub fn from_item(item: &Item) -> Result<Self> {
        <Self as Entity>::from_item(item)
    }
}

impl Entity for ReceiptWordLabel {
    const TYPE: EntityType = EntityType::ReceiptWordLabel;

    fn primary_key(&self) -> PrimaryKey {
        PrimaryKey {
            pk: keys::image_pk(&self.image_id),
            sk: keys::receipt_word_label_sk(
                self.receipt_id,
                self.line_id,
                self.word_id,
                &self.label,
            ),
        }
    }

    fn to_item(&self) -> Item {
        let mut item = Item::with_capacity(18);
        let pk = keys::image_pk(&self.image_id);
        let sk =
            keys::receipt_word_label_sk(self.receipt_id, self.line_id, self.word_id, &self.label);
        keys::put_pk_sk(&mut item, pk, sk);

        let mut gsi1sk = String::with_capacity(64 + self.image_id.len());
        gsi1sk.push_str("IMAGE#");
        gsi1sk.push_str(&self.image_id);
        gsi1sk.push_str("#RECEIPT#");
        keys::push_pad5(&mut gsi1sk, self.receipt_id);
        gsi1sk.push_str("#LINE#");
        keys::push_pad5(&mut gsi1sk, self.line_id);
        gsi1sk.push_str("#WORD#");
        keys::push_pad5(&mut gsi1sk, self.word_id);
        keys::put_gsi(
            &mut item,
            "GSI1PK",
            keys::label_gsi1_pk(&self.label),
            "GSI1SK",
            gsi1sk.clone(),
        );
        keys::put_gsi(&mut item, "GSI2PK", "RECEIPT".into(), "GSI2SK", gsi1sk);

        let mut gsi3sk = String::with_capacity(80 + self.image_id.len() + self.label.len());
        gsi3sk.push_str("IMAGE#");
        gsi3sk.push_str(&self.image_id);
        gsi3sk.push_str("#RECEIPT#");
        keys::push_pad5(&mut gsi3sk, self.receipt_id);
        gsi3sk.push_str("#LINE#");
        keys::push_pad5(&mut gsi3sk, self.line_id);
        gsi3sk.push_str("#WORD#");
        keys::push_pad5(&mut gsi3sk, self.word_id);
        gsi3sk.push_str("#LABEL#");
        gsi3sk.push_str(&self.label);
        keys::put_gsi(
            &mut item,
            "GSI3PK",
            format!("VALIDATION_STATUS#{}", self.validation_status.as_str()),
            "GSI3SK",
            gsi3sk,
        );
        keys::put_gsi(
            &mut item,
            "GSI4PK",
            keys::receipt_scope(&self.image_id, self.receipt_id),
            "GSI4SK",
            keys::gsi4_label_sk(self.line_id, self.word_id, &self.label),
        );

        item.insert("TYPE".into(), Attr::s(Self::TYPE.as_str()));
        item.insert(
            "reasoning".into(),
            match &self.reasoning {
                Some(s) => Attr::s(s.clone()),
                None => Attr::null(),
            },
        );
        item.insert(
            "timestamp_added".into(),
            Attr::s(self.timestamp_added.clone()),
        );
        item.insert(
            "validation_status".into(),
            Attr::s(self.validation_status.as_str()),
        );
        item.insert(
            "label_consolidated_from".into(),
            match &self.label_consolidated_from {
                Some(s) => Attr::s(s.clone()),
                None => Attr::null(),
            },
        );
        item.insert(
            "label_proposed_by".into(),
            match &self.label_proposed_by {
                Some(s) => Attr::s(s.clone()),
                None => Attr::null(),
            },
        );
        item
    }

    fn from_item(item: &Item) -> Result<Self> {
        let pk = item.require_s("PK")?;
        let image_id = keys::split_hash(pk, "IMAGE")?.to_string();
        let sk = item.require_s("SK")?;
        let parts: Vec<&str> = sk.split('#').collect();
        if parts.len() < 8
            || parts[0] != "RECEIPT"
            || parts[2] != "LINE"
            || parts[4] != "WORD"
            || parts[6] != "LABEL"
        {
            return Err(Error::validation(format!(
                "Invalid SK format for ReceiptWordLabel: {sk}"
            )));
        }
        let reasoning = match item.get("reasoning") {
            Some(attr) if !attr.is_null() => Some(attr.as_s()?.to_string()),
            _ => None,
        };
        let validation_status = match item.get("validation_status") {
            Some(attr) if !attr.is_null() => ValidationStatus::parse(attr.as_s()?)?,
            _ => ValidationStatus::None,
        };
        let opt_s = |key: &str| match item.get(key) {
            Some(attr) if !attr.is_null() => Some(attr.as_s().ok()?.to_string()),
            _ => None,
        };
        Ok(Self {
            image_id,
            receipt_id: keys::parse_padded_u32(parts[1])?,
            line_id: keys::parse_padded_u32(parts[3])?,
            word_id: keys::parse_padded_u32(parts[5])?,
            label: parts[7].to_ascii_uppercase(),
            reasoning,
            timestamp_added: item.require_s("timestamp_added")?.to_string(),
            validation_status,
            label_proposed_by: opt_s("label_proposed_by"),
            label_consolidated_from: opt_s("label_consolidated_from"),
        })
    }
}
