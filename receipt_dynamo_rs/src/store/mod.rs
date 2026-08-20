//! Storage backends: in-memory (tests) and optional AWS SDK.

use std::future::Future;
use std::pin::Pin;

use crate::attr::Item;
use crate::entities::PrimaryKey;
use crate::error::Result;
use crate::keys::Gsi;

mod memory;

pub use memory::InMemoryStore;

#[cfg(feature = "aws")]
mod aws;
#[cfg(feature = "aws")]
pub use aws::AwsStore;

pub type BoxFuture<'a, T> = Pin<Box<dyn Future<Output = T> + Send + 'a>>;

#[derive(Clone, Debug)]
pub struct QueryInput {
    pub pk: String,
    pub sk_prefix: Option<String>,
    pub index: Option<Gsi>,
    pub limit: Option<usize>,
    pub exclusive_start_sk: Option<String>,
    pub scan_forward: bool,
}

impl Default for QueryInput {
    fn default() -> Self {
        Self {
            pk: String::new(),
            sk_prefix: None,
            index: None,
            limit: None,
            exclusive_start_sk: None,
            scan_forward: true,
        }
    }
}

#[derive(Clone, Debug)]
pub struct QueryPage {
    pub items: Vec<Item>,
    pub last_evaluated_sk: Option<String>,
}

#[derive(Clone, Debug)]
pub struct WriteRequest {
    pub put: Option<Item>,
    pub delete: Option<PrimaryKey>,
}

/// Backend used by [`crate::ReceiptDynamo`].
pub trait Store: Send + Sync {
    fn put(&self, item: Item) -> BoxFuture<'_, Result<()>>;
    fn put_if_not_exists(&self, item: Item) -> BoxFuture<'_, Result<()>>;
    fn get(&self, key: PrimaryKey) -> BoxFuture<'_, Result<Option<Item>>>;
    fn delete(&self, key: PrimaryKey) -> BoxFuture<'_, Result<bool>>;
    fn query(&self, input: QueryInput) -> BoxFuture<'_, Result<QueryPage>>;
    fn batch_write(&self, requests: Vec<WriteRequest>) -> BoxFuture<'_, Result<Vec<WriteRequest>>>;
    fn batch_get(&self, keys: Vec<PrimaryKey>) -> BoxFuture<'_, Result<Vec<Item>>>;
}
