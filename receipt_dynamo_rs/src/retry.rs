//! Exponential backoff with jitter, matching `receipt_dynamo.utils.retry_with_backoff`.

use std::future::Future;
use std::time::Duration;

use crate::error::{Error, Result};

/// Calculate exponential backoff delay with optional 0-25% jitter.
pub fn exponential_backoff_with_jitter(
    attempt: u32,
    base_delay: Duration,
    max_delay: Duration,
    jitter: bool,
) -> Duration {
    let exp = base_delay.saturating_mul(1u32.checked_shl(attempt).unwrap_or(u32::MAX));
    let delay = exp.min(max_delay);
    if jitter {
        let nanos = delay.as_nanos() as f64;
        let factor = 1.0 + fastrand_frac() * 0.25;
        Duration::from_nanos((nanos * factor) as u64)
    } else {
        delay
    }
}

/// Tiny jitter source that avoids a rand dependency.
fn fastrand_frac() -> f64 {
    use std::cell::Cell;
    thread_local! {
        static STATE: Cell<u64> = Cell::new({
            let nanos = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos() as u64)
                .unwrap_or(0x9E3779B97F4A7C15);
            nanos ^ 0xA0761D6478BD642F
        });
    }
    STATE.with(|s| {
        let mut x = s.get();
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        s.set(x);
        (x as f64) / (u64::MAX as f64)
    })
}

pub async fn retry_with_backoff<F, Fut, T>(
    max_attempts: u32,
    base_delay: Duration,
    max_delay: Duration,
    jitter: bool,
    mut op: F,
) -> Result<T>
where
    F: FnMut() -> Fut,
    Fut: Future<Output = Result<T>>,
{
    let mut last_err: Option<Error> = None;
    for attempt in 0..max_attempts {
        match op().await {
            Ok(value) => return Ok(value),
            Err(err) => {
                if !err.is_retryable() || attempt + 1 == max_attempts {
                    if attempt + 1 == max_attempts && err.is_retryable() {
                        return Err(Error::RetryExhausted {
                            attempts: max_attempts,
                            source: Box::new(err),
                        });
                    }
                    return Err(err);
                }
                last_err = Some(err);
                let delay = exponential_backoff_with_jitter(attempt, base_delay, max_delay, jitter);
                tokio::time::sleep(delay).await;
            }
        }
    }
    Err(Error::RetryExhausted {
        attempts: max_attempts,
        source: Box::new(last_err.unwrap_or_else(|| Error::Other("retry failed".into()))),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn backoff_caps_at_max() {
        let delay = exponential_backoff_with_jitter(
            10,
            Duration::from_secs(1),
            Duration::from_secs(8),
            false,
        );
        assert_eq!(delay, Duration::from_secs(8));
    }
}
