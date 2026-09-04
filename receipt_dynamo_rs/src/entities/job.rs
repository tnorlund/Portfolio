use std::collections::HashMap;

use crate::attr::{Attr, Item, ItemExt};
use crate::constants::{JobPriority, JobStatus};
use crate::error::{Error, Result};
use crate::keys::{self, assert_valid_uuid, EntityType};

use super::{Entity, PrimaryKey};

#[derive(Clone, Debug, PartialEq)]
pub struct Job {
    pub job_id: String,
    pub name: String,
    pub description: String,
    pub created_at: String,
    pub created_by: String,
    pub status: JobStatus,
    pub priority: JobPriority,
    pub job_config: HashMap<String, String>,
    pub estimated_duration: Option<u32>,
    pub tags: Option<HashMap<String, String>>,
}

impl Job {
    pub fn new(
        job_id: impl Into<String>,
        name: impl Into<String>,
        description: impl Into<String>,
        created_at: impl Into<String>,
        created_by: impl Into<String>,
        status: JobStatus,
        priority: JobPriority,
    ) -> Result<Self> {
        let job_id = job_id.into();
        assert_valid_uuid(&job_id)?;
        let name = name.into();
        if name.is_empty() {
            return Err(Error::validation("name must be a non-empty string"));
        }
        let created_by = created_by.into();
        if created_by.is_empty() {
            return Err(Error::validation("created_by must be a non-empty string"));
        }
        Ok(Self {
            job_id,
            name,
            description: description.into(),
            created_at: created_at.into(),
            created_by,
            status,
            priority,
            job_config: HashMap::new(),
            estimated_duration: None,
            tags: None,
        })
    }

    pub fn from_item(item: &Item) -> Result<Self> {
        <Self as Entity>::from_item(item)
    }
}

fn string_map_attr(map: &HashMap<String, String>) -> Attr {
    Attr::map(
        map.iter()
            .map(|(k, v)| (k.clone(), Attr::s(v.clone())))
            .collect(),
    )
}

fn read_string_map(attr: &Attr) -> Result<HashMap<String, String>> {
    let m = attr.as_map()?;
    let mut out = HashMap::with_capacity(m.len());
    for (k, v) in m {
        out.insert(k.clone(), v.as_s()?.to_string());
    }
    Ok(out)
}

impl Entity for Job {
    const TYPE: EntityType = EntityType::Job;

    fn primary_key(&self) -> PrimaryKey {
        PrimaryKey {
            pk: keys::job_pk(&self.job_id),
            sk: "JOB".to_string(),
        }
    }

    fn to_item(&self) -> Item {
        let mut item = Item::with_capacity(16);
        keys::put_pk_sk(&mut item, keys::job_pk(&self.job_id), "JOB".into());
        keys::put_gsi(
            &mut item,
            "GSI1PK",
            format!("STATUS#{}", self.status.as_str()),
            "GSI1SK",
            format!("CREATED#{}", self.created_at),
        );
        item.insert("TYPE".into(), Attr::s(Self::TYPE.as_str()));
        item.insert("name".into(), Attr::s(self.name.clone()));
        item.insert("description".into(), Attr::s(self.description.clone()));
        item.insert("created_at".into(), Attr::s(self.created_at.clone()));
        item.insert("created_by".into(), Attr::s(self.created_by.clone()));
        item.insert("status".into(), Attr::s(self.status.as_str()));
        item.insert("priority".into(), Attr::s(self.priority.as_str()));
        item.insert("job_config".into(), string_map_attr(&self.job_config));
        item.insert(
            "estimated_duration".into(),
            match self.estimated_duration {
                Some(n) => Attr::n_uint(n),
                None => Attr::null(),
            },
        );
        item.insert(
            "tags".into(),
            match &self.tags {
                Some(tags) => string_map_attr(tags),
                None => Attr::null(),
            },
        );
        item
    }

    fn from_item(item: &Item) -> Result<Self> {
        let pk = item.require_s("PK")?;
        let job_id = keys::split_hash(pk, "JOB")?.to_string();
        let job_config = match item.get("job_config") {
            Some(attr) if !attr.is_null() => read_string_map(attr)?,
            _ => HashMap::new(),
        };
        let tags = match item.get("tags") {
            Some(attr) if !attr.is_null() => Some(read_string_map(attr)?),
            _ => None,
        };
        let estimated_duration = match item.get("estimated_duration") {
            Some(attr) if !attr.is_null() => Some(attr.as_n_u32()?),
            _ => None,
        };
        Ok(Self {
            job_id,
            name: item.require_s("name")?.to_string(),
            description: item.require_s("description")?.to_string(),
            created_at: item.require_s("created_at")?.to_string(),
            created_by: item.require_s("created_by")?.to_string(),
            status: JobStatus::parse(item.require_s("status")?)?,
            priority: JobPriority::parse(item.require_s("priority")?)?,
            job_config,
            estimated_duration,
            tags,
        })
    }
}
