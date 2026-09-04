//! Typed DynamoDB client with batching, retries, and circuit breaking.

use std::sync::Arc;
use std::time::Duration;

use crate::attr::{Item, BATCH_GET_LIMIT, BATCH_WRITE_LIMIT};
use crate::circuit_breaker::CircuitBreaker;
use crate::entities::{
    AnyEntity, Entity, Image, PrimaryKey, Receipt, ReceiptLine, ReceiptWord, ReceiptWordLabel,
};
use crate::error::{Error, Result};
use crate::keys::{self, Gsi};
use crate::retry::retry_with_backoff;
use crate::store::{InMemoryStore, QueryInput, Store, WriteRequest};

/// High-level client. Generic over the storage backend so tests use memory
/// and production uses the AWS SDK without changing call sites.
pub struct ReceiptDynamo<S: Store = InMemoryStore> {
    store: Arc<S>,
    breaker: CircuitBreaker,
    max_attempts: u32,
    base_delay: Duration,
    max_delay: Duration,
}

impl ReceiptDynamo<InMemoryStore> {
    pub fn memory() -> Self {
        Self::new(InMemoryStore::new())
    }
}

#[cfg(feature = "aws")]
impl ReceiptDynamo<crate::store::AwsStore> {
    pub async fn from_env(table_name: impl Into<String>) -> Result<Self> {
        let endpoint = std::env::var("DYNAMODB_ENDPOINT_URL").ok();
        let store = crate::store::AwsStore::from_env(table_name, endpoint.as_deref()).await?;
        Ok(Self::new(store))
    }

    pub fn aws(store: crate::store::AwsStore) -> Self {
        Self::new(store)
    }
}

impl<S: Store> ReceiptDynamo<S> {
    pub fn new(store: S) -> Self {
        Self {
            store: Arc::new(store),
            breaker: CircuitBreaker::default(),
            max_attempts: 3,
            base_delay: Duration::from_millis(50),
            max_delay: Duration::from_secs(2),
        }
    }

    pub fn with_retry(
        mut self,
        max_attempts: u32,
        base_delay: Duration,
        max_delay: Duration,
    ) -> Self {
        self.max_attempts = max_attempts;
        self.base_delay = base_delay;
        self.max_delay = max_delay;
        self
    }

    async fn resilient<T, F, Fut>(&self, op: F) -> Result<T>
    where
        F: Fn() -> Fut,
        Fut: std::future::Future<Output = Result<T>>,
    {
        self.breaker
            .call(|| {
                retry_with_backoff(self.max_attempts, self.base_delay, self.max_delay, true, op)
            })
            .await
    }

    pub async fn put_item(&self, item: Item) -> Result<()> {
        self.resilient(|| self.store.put(item.clone())).await
    }

    pub async fn add_item(&self, item: Item) -> Result<()> {
        self.resilient(|| self.store.put_if_not_exists(item.clone()))
            .await
    }

    pub async fn get_item(&self, key: PrimaryKey) -> Result<Option<Item>> {
        self.resilient(|| self.store.get(key.clone())).await
    }

    pub async fn delete_item(&self, key: PrimaryKey) -> Result<bool> {
        self.resilient(|| self.store.delete(key.clone())).await
    }

    pub async fn put_entity<E: Entity>(&self, entity: &E) -> Result<()> {
        self.put_item(entity.to_item()).await
    }

    pub async fn add_entity<E: Entity>(&self, entity: &E) -> Result<()> {
        self.add_item(entity.to_item()).await
    }

    pub async fn get_entity<E: Entity>(&self, key: PrimaryKey) -> Result<Option<E>> {
        match self.get_item(key).await? {
            Some(item) => Ok(Some(E::from_item(&item)?)),
            None => Ok(None),
        }
    }

    pub async fn batch_put_items(&self, items: Vec<Item>) -> Result<()> {
        for chunk in items.chunks(BATCH_WRITE_LIMIT) {
            let mut pending: Vec<WriteRequest> = chunk
                .iter()
                .cloned()
                .map(|item| WriteRequest {
                    put: Some(item),
                    delete: None,
                })
                .collect();
            let mut attempts = 0;
            while !pending.is_empty() {
                attempts += 1;
                if attempts > self.max_attempts {
                    return Err(Error::Batch(format!(
                        "{} unprocessed items after {attempts} attempts",
                        pending.len()
                    )));
                }
                let leftover = pending.clone();
                pending = self
                    .resilient(|| self.store.batch_write(leftover.clone()))
                    .await?;
            }
        }
        Ok(())
    }

    pub async fn batch_put_entities<E: Entity>(&self, entities: &[E]) -> Result<()> {
        let items = entities.iter().map(E::to_item).collect();
        self.batch_put_items(items).await
    }

    pub async fn batch_get_items(&self, keys: Vec<PrimaryKey>) -> Result<Vec<Item>> {
        let mut out = Vec::with_capacity(keys.len());
        for chunk in keys.chunks(BATCH_GET_LIMIT) {
            let chunk = chunk.to_vec();
            let mut page = self
                .resilient(|| self.store.batch_get(chunk.clone()))
                .await?;
            out.append(&mut page);
        }
        Ok(out)
    }

    pub async fn query_pk(
        &self,
        pk: impl Into<String>,
        sk_prefix: Option<&str>,
    ) -> Result<Vec<Item>> {
        let mut items = Vec::new();
        let mut start = None;
        let pk = pk.into();
        loop {
            let input = QueryInput {
                pk: pk.clone(),
                sk_prefix: sk_prefix.map(str::to_string),
                exclusive_start_sk: start.clone(),
                ..QueryInput::default()
            };
            let page = self.resilient(|| self.store.query(input.clone())).await?;
            let exhausted = page.last_evaluated_sk.is_none();
            start = page.last_evaluated_sk;
            items.extend(page.items);
            if exhausted {
                break;
            }
        }
        Ok(items)
    }

    pub async fn query_gsi(
        &self,
        index: Gsi,
        pk: impl Into<String>,
        sk_prefix: Option<&str>,
    ) -> Result<Vec<Item>> {
        let mut items = Vec::new();
        let mut start = None;
        let pk = pk.into();
        loop {
            let input = QueryInput {
                pk: pk.clone(),
                sk_prefix: sk_prefix.map(str::to_string),
                index: Some(index),
                exclusive_start_sk: start.clone(),
                ..QueryInput::default()
            };
            let page = self.resilient(|| self.store.query(input.clone())).await?;
            let exhausted = page.last_evaluated_sk.is_none();
            start = page.last_evaluated_sk;
            items.extend(page.items);
            if exhausted {
                break;
            }
        }
        Ok(items)
    }

    /// Single-query receipt details via GSI4 (receipt, place, lines, words, labels, summary, barcodes).
    pub async fn query_receipt_details(
        &self,
        image_id: &str,
        receipt_id: u32,
    ) -> Result<Vec<AnyEntity>> {
        let pk = keys::receipt_scope(image_id, receipt_id);
        let items = self.query_gsi(Gsi::Gsi4, pk, None).await?;
        items.iter().map(AnyEntity::from_item).collect()
    }

    pub async fn query_image(&self, image_id: &str) -> Result<Option<Image>> {
        self.get_entity(PrimaryKey {
            pk: keys::image_pk(image_id),
            sk: "IMAGE".into(),
        })
        .await
    }

    pub async fn query_receipt(&self, image_id: &str, receipt_id: u32) -> Result<Option<Receipt>> {
        self.get_entity(PrimaryKey {
            pk: keys::image_pk(image_id),
            sk: keys::receipt_sk(receipt_id),
        })
        .await
    }

    pub async fn query_receipt_words(
        &self,
        image_id: &str,
        receipt_id: u32,
    ) -> Result<Vec<ReceiptWord>> {
        let items = self
            .query_gsi(
                Gsi::Gsi3,
                keys::receipt_scope(image_id, receipt_id),
                Some("WORD"),
            )
            .await?;
        items.iter().map(ReceiptWord::from_item).collect()
    }

    pub async fn query_receipt_lines(
        &self,
        image_id: &str,
        receipt_id: u32,
    ) -> Result<Vec<ReceiptLine>> {
        let items = self
            .query_gsi(
                Gsi::Gsi3,
                keys::receipt_scope(image_id, receipt_id),
                Some("LINE"),
            )
            .await?;
        items.iter().map(ReceiptLine::from_item).collect()
    }

    pub async fn query_labels_by_name(&self, label: &str) -> Result<Vec<ReceiptWordLabel>> {
        let pk = keys::label_gsi1_pk(&label.to_ascii_uppercase());
        let items = self.query_gsi(Gsi::Gsi1, pk, None).await?;
        items.iter().map(ReceiptWordLabel::from_item).collect()
    }

    pub async fn query_image_partition(&self, image_id: &str) -> Result<Vec<AnyEntity>> {
        let items = self.query_pk(keys::image_pk(image_id), None).await?;
        items.iter().map(AnyEntity::from_item).collect()
    }
}
