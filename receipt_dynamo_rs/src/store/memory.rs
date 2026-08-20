use std::collections::{BTreeMap, HashMap};
use std::sync::RwLock;

use crate::attr::Item;
use crate::entities::PrimaryKey;
use crate::error::{Error, Result};
use crate::keys::Gsi;

use super::{BoxFuture, QueryInput, QueryPage, Store, WriteRequest};

type GsiEntries = HashMap<String, BTreeMap<String, Vec<(String, String)>>>;

#[derive(Clone, Debug, Default)]
struct Indexes {
    /// index name → pk → gsi_sk → table (pk, sk) list (GSI keys are not unique)
    gsi: HashMap<&'static str, GsiEntries>,
}

/// Thread-safe in-memory table used by tests and local development.
#[derive(Debug, Default)]
pub struct InMemoryStore {
    items: RwLock<HashMap<(String, String), Item>>,
    indexes: RwLock<Indexes>,
}

impl InMemoryStore {
    pub fn new() -> Self {
        Self::default()
    }

    fn index_pk_sk(item: &Item, gsi: Gsi) -> Option<(String, String)> {
        let pk = item.get(gsi.pk_attr()).and_then(|a| a.as_s().ok())?;
        let sk = match gsi.sk_attr() {
            Some(name) => item.get(name).and_then(|a| a.as_s().ok())?,
            None => "",
        };
        Some((pk.to_string(), sk.to_string()))
    }

    fn upsert_indexes(indexes: &mut Indexes, table_pk: &str, table_sk: &str, item: &Item) {
        for gsi in [Gsi::Gsi1, Gsi::Gsi2, Gsi::Gsi3, Gsi::Gsi4, Gsi::GsiType] {
            if let Some((pk, sk)) = Self::index_pk_sk(item, gsi) {
                let entries = indexes
                    .gsi
                    .entry(gsi.index_name())
                    .or_default()
                    .entry(pk)
                    .or_default()
                    .entry(sk)
                    .or_default();
                let tuple = (table_pk.to_string(), table_sk.to_string());
                if !entries.contains(&tuple) {
                    entries.push(tuple);
                }
            }
        }
    }

    fn remove_indexes(indexes: &mut Indexes, table_pk: &str, table_sk: &str, item: &Item) {
        for gsi in [Gsi::Gsi1, Gsi::Gsi2, Gsi::Gsi3, Gsi::Gsi4, Gsi::GsiType] {
            if let Some((pk, sk)) = Self::index_pk_sk(item, gsi) {
                if let Some(by_pk) = indexes.gsi.get_mut(gsi.index_name()) {
                    if let Some(by_sk) = by_pk.get_mut(&pk) {
                        if let Some(entries) = by_sk.get_mut(&sk) {
                            entries.retain(|(p, s)| p != table_pk || s != table_sk);
                            if entries.is_empty() {
                                by_sk.remove(&sk);
                            }
                        }
                    }
                }
            }
        }
    }

    fn item_pk_sk(item: &Item) -> Result<(String, String)> {
        let pk = item
            .get("PK")
            .and_then(|a| a.as_s().ok())
            .ok_or_else(|| Error::validation("item missing PK"))?
            .to_string();
        let sk = item
            .get("SK")
            .and_then(|a| a.as_s().ok())
            .ok_or_else(|| Error::validation("item missing SK"))?
            .to_string();
        Ok((pk, sk))
    }
}

impl Store for InMemoryStore {
    fn put(&self, item: Item) -> BoxFuture<'_, Result<()>> {
        Box::pin(async move {
            let (pk, sk) = Self::item_pk_sk(&item)?;
            let mut items = self.items.write().expect("store lock");
            let mut indexes = self.indexes.write().expect("index lock");
            if let Some(old) = items.get(&(pk.clone(), sk.clone())) {
                Self::remove_indexes(&mut indexes, &pk, &sk, old);
            }
            Self::upsert_indexes(&mut indexes, &pk, &sk, &item);
            items.insert((pk, sk), item);
            Ok(())
        })
    }

    fn put_if_not_exists(&self, item: Item) -> BoxFuture<'_, Result<()>> {
        Box::pin(async move {
            let (pk, sk) = Self::item_pk_sk(&item)?;
            let mut items = self.items.write().expect("store lock");
            if items.contains_key(&(pk.clone(), sk.clone())) {
                return Err(Error::EntityAlreadyExists);
            }
            let mut indexes = self.indexes.write().expect("index lock");
            Self::upsert_indexes(&mut indexes, &pk, &sk, &item);
            items.insert((pk, sk), item);
            Ok(())
        })
    }

    fn get(&self, key: PrimaryKey) -> BoxFuture<'_, Result<Option<Item>>> {
        Box::pin(async move {
            let items = self.items.read().expect("store lock");
            Ok(items.get(&(key.pk, key.sk)).cloned())
        })
    }

    fn delete(&self, key: PrimaryKey) -> BoxFuture<'_, Result<bool>> {
        Box::pin(async move {
            let mut items = self.items.write().expect("store lock");
            if let Some(old) = items.remove(&(key.pk.clone(), key.sk.clone())) {
                let mut indexes = self.indexes.write().expect("index lock");
                Self::remove_indexes(&mut indexes, &key.pk, &key.sk, &old);
                Ok(true)
            } else {
                Ok(false)
            }
        })
    }

    fn query(&self, input: QueryInput) -> BoxFuture<'_, Result<QueryPage>> {
        Box::pin(async move {
            let items = self.items.read().expect("store lock");
            let mut collected: Vec<(String, Item)> = if let Some(index) = input.index {
                let indexes = self.indexes.read().expect("index lock");
                let mut out = Vec::new();
                if let Some(by_pk) = indexes
                    .gsi
                    .get(index.index_name())
                    .and_then(|m| m.get(&input.pk))
                {
                    for (sk, entries) in by_pk {
                        if let Some(prefix) = &input.sk_prefix {
                            if !sk.starts_with(prefix) {
                                continue;
                            }
                        }
                        for (pk, table_sk) in entries {
                            if let Some(item) = items.get(&(pk.clone(), table_sk.clone())) {
                                out.push((sk.clone(), item.clone()));
                            }
                        }
                    }
                }
                out
            } else {
                let mut out = Vec::new();
                for ((pk, sk), item) in items.iter() {
                    if pk != &input.pk {
                        continue;
                    }
                    if let Some(prefix) = &input.sk_prefix {
                        if !sk.starts_with(prefix) {
                            continue;
                        }
                    }
                    out.push((sk.clone(), item.clone()));
                }
                out
            };

            collected.sort_by(|a, b| a.0.cmp(&b.0));
            if !input.scan_forward {
                collected.reverse();
            }
            if let Some(start) = &input.exclusive_start_sk {
                collected.retain(|(sk, _)| {
                    if input.scan_forward {
                        sk.as_str() > start.as_str()
                    } else {
                        sk.as_str() < start.as_str()
                    }
                });
            }
            let limit = input.limit.unwrap_or(collected.len());
            let truncated = collected.len() > limit;
            collected.truncate(limit);
            let last = if truncated {
                collected.last().map(|(sk, _)| sk.clone())
            } else {
                None
            };
            Ok(QueryPage {
                items: collected.into_iter().map(|(_, item)| item).collect(),
                last_evaluated_sk: last,
            })
        })
    }

    fn batch_write(&self, requests: Vec<WriteRequest>) -> BoxFuture<'_, Result<Vec<WriteRequest>>> {
        Box::pin(async move {
            if requests.len() > crate::attr::BATCH_WRITE_LIMIT {
                return Err(Error::Batch(format!(
                    "batch write exceeds {} items",
                    crate::attr::BATCH_WRITE_LIMIT
                )));
            }
            for req in requests {
                if let Some(item) = req.put {
                    Store::put(self, item).await?;
                } else if let Some(key) = req.delete {
                    Store::delete(self, key).await?;
                }
            }
            Ok(Vec::new())
        })
    }

    fn batch_get(&self, keys: Vec<PrimaryKey>) -> BoxFuture<'_, Result<Vec<Item>>> {
        Box::pin(async move {
            if keys.len() > crate::attr::BATCH_GET_LIMIT {
                return Err(Error::Batch(format!(
                    "batch get exceeds {} keys",
                    crate::attr::BATCH_GET_LIMIT
                )));
            }
            let mut out = Vec::with_capacity(keys.len());
            for key in keys {
                if let Some(item) = Store::get(self, key).await? {
                    out.push(item);
                }
            }
            Ok(out)
        })
    }
}
