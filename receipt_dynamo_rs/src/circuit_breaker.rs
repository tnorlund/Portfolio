//! Circuit breaker matching `receipt_dynamo.utils.circuit_breaker`.

use std::sync::Mutex;
use std::time::{Duration, Instant};

use crate::error::{Error, Result};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CircuitState {
    Closed,
    Open,
    HalfOpen,
}

#[derive(Debug)]
struct Inner {
    state: CircuitState,
    failure_count: u32,
    last_failure: Option<Instant>,
    consecutive_successes: u32,
}

/// Circuit breaker that trips after consecutive retryable failures.
#[derive(Debug)]
pub struct CircuitBreaker {
    failure_threshold: u32,
    timeout: Duration,
    inner: Mutex<Inner>,
}

impl CircuitBreaker {
    pub fn new(failure_threshold: u32, timeout: Duration) -> Self {
        Self {
            failure_threshold,
            timeout,
            inner: Mutex::new(Inner {
                state: CircuitState::Closed,
                failure_count: 0,
                last_failure: None,
                consecutive_successes: 0,
            }),
        }
    }

    pub fn state(&self) -> CircuitState {
        self.inner.lock().expect("circuit lock").state
    }

    pub fn before_call(&self) -> Result<()> {
        let mut inner = self.inner.lock().expect("circuit lock");
        match inner.state {
            CircuitState::Closed | CircuitState::HalfOpen => Ok(()),
            CircuitState::Open => {
                if let Some(last) = inner.last_failure {
                    if last.elapsed() >= self.timeout {
                        inner.state = CircuitState::HalfOpen;
                        return Ok(());
                    }
                }
                Err(Error::CircuitOpen)
            }
        }
    }

    pub fn record_success(&self) {
        let mut inner = self.inner.lock().expect("circuit lock");
        inner.failure_count = 0;
        inner.consecutive_successes += 1;
        if inner.state == CircuitState::HalfOpen {
            inner.state = CircuitState::Closed;
        }
    }

    pub fn record_failure(&self) {
        let mut inner = self.inner.lock().expect("circuit lock");
        inner.failure_count += 1;
        inner.consecutive_successes = 0;
        inner.last_failure = Some(Instant::now());
        if inner.failure_count >= self.failure_threshold {
            inner.state = CircuitState::Open;
        }
    }

    pub async fn call<F, Fut, T>(&self, op: F) -> Result<T>
    where
        F: FnOnce() -> Fut,
        Fut: std::future::Future<Output = Result<T>>,
    {
        self.before_call()?;
        match op().await {
            Ok(value) => {
                self.record_success();
                Ok(value)
            }
            Err(err) => {
                if err.is_retryable() {
                    self.record_failure();
                }
                Err(err)
            }
        }
    }
}

impl Default for CircuitBreaker {
    fn default() -> Self {
        Self::new(5, Duration::from_secs(30))
    }
}
