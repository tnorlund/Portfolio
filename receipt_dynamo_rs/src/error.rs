use thiserror::Error;

/// Crate-wide result type.
pub type Result<T> = std::result::Result<T, Error>;

/// Errors raised by entity validation, DynamoDB access, and resilience helpers.
#[derive(Debug, Error)]
pub enum Error {
    #[error("{0}")]
    Validation(String),

    #[error("entity not found")]
    EntityNotFound,

    #[error("entity already exists")]
    EntityAlreadyExists,

    #[error("batch operation failed: {0}")]
    Batch(String),

    #[error("retry exhausted after {attempts} attempts: {source}")]
    RetryExhausted {
        attempts: u32,
        #[source]
        source: Box<Error>,
    },

    #[error("circuit breaker is open")]
    CircuitOpen,

    #[error("DynamoDB throughput exceeded: {0}")]
    Throughput(String),

    #[error("DynamoDB server error: {0}")]
    Server(String),

    #[error("DynamoDB resource not found: {0}")]
    ResourceNotFound(String),

    #[error("DynamoDB access error: {0}")]
    Access(String),

    #[error("DynamoDB error: {0}")]
    Dynamo(String),

    #[error("operation error: {0}")]
    Operation(String),

    #[error("{0}")]
    Other(String),
}

impl Error {
    pub fn validation(msg: impl Into<String>) -> Self {
        Self::Validation(msg.into())
    }

    pub fn is_retryable(&self) -> bool {
        matches!(self, Self::Throughput(_) | Self::Server(_))
    }
}

impl From<String> for Error {
    fn from(value: String) -> Self {
        Self::Other(value)
    }
}
