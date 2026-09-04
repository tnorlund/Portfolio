use std::collections::HashMap;

use aws_sdk_dynamodb::types::{
    AttributeValue, DeleteRequest, KeysAndAttributes, PutRequest, WriteRequest as AwsWrite,
};
use aws_sdk_dynamodb::Client;

use crate::attr::{Attr, Item};
use crate::entities::PrimaryKey;
use crate::error::{Error, Result};

use super::{BoxFuture, QueryInput, QueryPage, Store, WriteRequest};

/// AWS SDK-backed store. Compile with `--features aws` (the default).
#[derive(Clone, Debug)]
pub struct AwsStore {
    client: Client,
    table_name: String,
}

impl AwsStore {
    pub fn new(client: Client, table_name: impl Into<String>) -> Self {
        Self {
            client,
            table_name: table_name.into(),
        }
    }

    pub async fn from_env(
        table_name: impl Into<String>,
        endpoint_url: Option<&str>,
    ) -> Result<Self> {
        let table_name = table_name.into();
        let mut loader = aws_config::from_env();
        if let Some(url) = endpoint_url {
            loader = loader.endpoint_url(url);
        }
        let cfg = loader.load().await;
        let client = Client::new(&cfg);
        client
            .describe_table()
            .table_name(&table_name)
            .send()
            .await
            .map_err(|e| Error::ResourceNotFound(e.to_string()))?;
        Ok(Self { client, table_name })
    }

    fn map_sdk_err(err: impl std::fmt::Display) -> Error {
        let msg = err.to_string();
        if msg.contains("ProvisionedThroughputExceeded") || msg.contains("Throttling") {
            Error::Throughput(msg)
        } else if msg.contains("InternalServerError") || msg.contains("ServiceUnavailable") {
            Error::Server(msg)
        } else if msg.contains("ResourceNotFound") {
            Error::ResourceNotFound(msg)
        } else if msg.contains("ConditionalCheckFailed") {
            Error::EntityAlreadyExists
        } else {
            Error::Dynamo(msg)
        }
    }
}

impl Store for AwsStore {
    fn put(&self, item: Item) -> BoxFuture<'_, Result<()>> {
        Box::pin(async move {
            self.client
                .put_item()
                .table_name(&self.table_name)
                .set_item(Some(item_to_aws(&item)))
                .send()
                .await
                .map_err(Self::map_sdk_err)?;
            Ok(())
        })
    }

    fn put_if_not_exists(&self, item: Item) -> BoxFuture<'_, Result<()>> {
        Box::pin(async move {
            self.client
                .put_item()
                .table_name(&self.table_name)
                .set_item(Some(item_to_aws(&item)))
                .condition_expression("attribute_not_exists(PK) AND attribute_not_exists(SK)")
                .send()
                .await
                .map_err(Self::map_sdk_err)?;
            Ok(())
        })
    }

    fn get(&self, key: PrimaryKey) -> BoxFuture<'_, Result<Option<Item>>> {
        Box::pin(async move {
            let out = self
                .client
                .get_item()
                .table_name(&self.table_name)
                .set_key(Some(key_to_aws(&key)))
                .send()
                .await
                .map_err(Self::map_sdk_err)?;
            Ok(out.item.map(aws_to_item))
        })
    }

    fn delete(&self, key: PrimaryKey) -> BoxFuture<'_, Result<bool>> {
        Box::pin(async move {
            let out = self
                .client
                .delete_item()
                .table_name(&self.table_name)
                .set_key(Some(key_to_aws(&key)))
                .return_values(aws_sdk_dynamodb::types::ReturnValue::AllOld)
                .send()
                .await
                .map_err(Self::map_sdk_err)?;
            Ok(out.attributes.is_some())
        })
    }

    fn query(&self, input: QueryInput) -> BoxFuture<'_, Result<QueryPage>> {
        Box::pin(async move {
            let mut req = self.client.query().table_name(&self.table_name);
            if let Some(index) = input.index {
                req = req.index_name(index.index_name());
                let pk_name = index.pk_attr();
                if let Some(sk_name) = index.sk_attr() {
                    if let Some(prefix) = &input.sk_prefix {
                        req = req
                            .key_condition_expression(format!(
                                "{pk_name} = :pk AND begins_with({sk_name}, :sk)"
                            ))
                            .expression_attribute_values(":pk", AttributeValue::S(input.pk.clone()))
                            .expression_attribute_values(":sk", AttributeValue::S(prefix.clone()));
                    } else {
                        req = req
                            .key_condition_expression(format!("{pk_name} = :pk"))
                            .expression_attribute_values(
                                ":pk",
                                AttributeValue::S(input.pk.clone()),
                            );
                    }
                } else {
                    req = req
                        .key_condition_expression(format!("{pk_name} = :pk"))
                        .expression_attribute_values(":pk", AttributeValue::S(input.pk.clone()));
                }
            } else if let Some(prefix) = &input.sk_prefix {
                req = req
                    .key_condition_expression("PK = :pk AND begins_with(SK, :sk)")
                    .expression_attribute_values(":pk", AttributeValue::S(input.pk.clone()))
                    .expression_attribute_values(":sk", AttributeValue::S(prefix.clone()));
            } else {
                req = req
                    .key_condition_expression("PK = :pk")
                    .expression_attribute_values(":pk", AttributeValue::S(input.pk.clone()));
            }
            if let Some(limit) = input.limit {
                req = req.limit(limit as i32);
            }
            req = req.scan_index_forward(input.scan_forward);
            let out = req.send().await.map_err(Self::map_sdk_err)?;
            let items = out
                .items
                .unwrap_or_default()
                .into_iter()
                .map(aws_to_item)
                .collect();
            let last = out.last_evaluated_key.and_then(|k| {
                k.get("SK")
                    .and_then(|v| v.as_s().ok())
                    .map(|s| s.to_string())
            });
            Ok(QueryPage {
                items,
                last_evaluated_sk: last,
            })
        })
    }

    fn batch_write(&self, requests: Vec<WriteRequest>) -> BoxFuture<'_, Result<Vec<WriteRequest>>> {
        Box::pin(async move {
            let mut writes = Vec::with_capacity(requests.len());
            for req in requests {
                if let Some(item) = req.put {
                    writes.push(
                        AwsWrite::builder()
                            .put_request(
                                PutRequest::builder()
                                    .set_item(Some(item_to_aws(&item)))
                                    .build()
                                    .map_err(|e| Error::Batch(e.to_string()))?,
                            )
                            .build(),
                    );
                } else if let Some(key) = req.delete {
                    writes.push(
                        AwsWrite::builder()
                            .delete_request(
                                DeleteRequest::builder()
                                    .set_key(Some(key_to_aws(&key)))
                                    .build()
                                    .map_err(|e| Error::Batch(e.to_string()))?,
                            )
                            .build(),
                    );
                }
            }
            let out = self
                .client
                .batch_write_item()
                .request_items(&self.table_name, writes)
                .send()
                .await
                .map_err(Self::map_sdk_err)?;
            let unprocessed = out
                .unprocessed_items
                .unwrap_or_default()
                .remove(&self.table_name)
                .unwrap_or_default()
                .into_iter()
                .map(aws_write_to_request)
                .collect();
            Ok(unprocessed)
        })
    }

    fn batch_get(&self, keys: Vec<PrimaryKey>) -> BoxFuture<'_, Result<Vec<Item>>> {
        Box::pin(async move {
            let keys_attr: Vec<_> = keys.iter().map(key_to_aws).collect();
            let kas = KeysAndAttributes::builder()
                .set_keys(Some(keys_attr))
                .build()
                .map_err(|e| Error::Batch(e.to_string()))?;
            let out = self
                .client
                .batch_get_item()
                .request_items(&self.table_name, kas)
                .send()
                .await
                .map_err(Self::map_sdk_err)?;
            let items = out
                .responses
                .unwrap_or_default()
                .remove(&self.table_name)
                .unwrap_or_default()
                .into_iter()
                .map(aws_to_item)
                .collect();
            Ok(items)
        })
    }
}

fn attr_to_aws(attr: &Attr) -> AttributeValue {
    match attr {
        Attr::S { S } => AttributeValue::S(S.clone()),
        Attr::N { N } => AttributeValue::N(N.clone()),
        Attr::Bool { BOOL } => AttributeValue::Bool(*BOOL),
        Attr::Null { NULL } => AttributeValue::Null(*NULL),
        Attr::M { M } => {
            AttributeValue::M(M.iter().map(|(k, v)| (k.clone(), attr_to_aws(v))).collect())
        }
        Attr::L { L } => AttributeValue::L(L.iter().map(attr_to_aws).collect()),
    }
}

fn aws_to_attr(value: AttributeValue) -> Attr {
    match value {
        AttributeValue::S(s) => Attr::s(s),
        AttributeValue::N(n) => Attr::n_str(n),
        AttributeValue::Bool(b) => Attr::bool(b),
        AttributeValue::Null(_) => Attr::null(),
        AttributeValue::M(m) => {
            Attr::map(m.into_iter().map(|(k, v)| (k, aws_to_attr(v))).collect())
        }
        AttributeValue::L(l) => Attr::L {
            L: l.into_iter().map(aws_to_attr).collect(),
        },
        other => Attr::s(format!("unsupported:{other:?}")),
    }
}

fn item_to_aws(item: &Item) -> HashMap<String, AttributeValue> {
    item.iter()
        .map(|(k, v)| (k.clone(), attr_to_aws(v)))
        .collect()
}

fn aws_to_item(item: HashMap<String, AttributeValue>) -> Item {
    item.into_iter().map(|(k, v)| (k, aws_to_attr(v))).collect()
}

fn key_to_aws(key: &PrimaryKey) -> HashMap<String, AttributeValue> {
    HashMap::from([
        ("PK".into(), AttributeValue::S(key.pk.clone())),
        ("SK".into(), AttributeValue::S(key.sk.clone())),
    ])
}

fn aws_write_to_request(write: AwsWrite) -> WriteRequest {
    if let Some(put) = write.put_request {
        WriteRequest {
            put: Some(aws_to_item(put.item)),
            delete: None,
        }
    } else if let Some(del) = write.delete_request {
        let pk = del
            .key
            .get("PK")
            .and_then(|v| v.as_s().ok())
            .cloned()
            .unwrap_or_default();
        let sk = del
            .key
            .get("SK")
            .and_then(|v| v.as_s().ok())
            .cloned()
            .unwrap_or_default();
        WriteRequest {
            put: None,
            delete: Some(PrimaryKey { pk, sk }),
        }
    } else {
        WriteRequest {
            put: None,
            delete: None,
        }
    }
}
