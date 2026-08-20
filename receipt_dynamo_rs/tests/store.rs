use receipt_dynamo::entities::Entity;
use receipt_dynamo::{
    Image, Receipt, ReceiptDynamo, ReceiptLine, ReceiptWord, ReceiptWordLabel, TextGeometry,
};

const IMAGE_ID: &str = "f47ac10b-58cc-4372-a567-0e02b2c3d479";

fn geom(text: &str) -> TextGeometry {
    TextGeometry::unit_box(IMAGE_ID, text).unwrap()
}

#[tokio::test]
async fn put_get_query_receipt_graph() {
    let db = ReceiptDynamo::memory();
    let image = Image::new(IMAGE_ID, 100, 200, "2026-08-20T00:00:00", "b", "k.png").unwrap();
    let receipt = Receipt::new(
        IMAGE_ID,
        1,
        80,
        160,
        "2026-08-20T00:00:00",
        "b",
        "k.png",
        receipt_dynamo::Point { x: 0.0, y: 0.0 },
        receipt_dynamo::Point { x: 1.0, y: 0.0 },
        receipt_dynamo::Point { x: 0.0, y: 1.0 },
        receipt_dynamo::Point { x: 1.0, y: 1.0 },
    )
    .unwrap();
    let line = ReceiptLine::new(1, 1, geom("MILK 3.49")).unwrap();
    let word = ReceiptWord::new(1, 1, 1, geom("MILK")).unwrap();
    let label = ReceiptWordLabel::new(
        IMAGE_ID,
        1,
        1,
        1,
        "PRODUCT_NAME",
        Some("item name".into()),
        "2026-08-20T00:00:00",
    )
    .unwrap();

    db.add_entity(&image).await.unwrap();
    db.add_entity(&receipt).await.unwrap();
    db.add_entity(&line).await.unwrap();
    db.add_entity(&word).await.unwrap();
    db.add_entity(&label).await.unwrap();

    let dup = db.add_entity(&image).await;
    assert!(matches!(
        dup,
        Err(receipt_dynamo::Error::EntityAlreadyExists)
    ));

    let fetched = db.query_image(IMAGE_ID).await.unwrap().unwrap();
    assert_eq!(fetched.width, 100);

    let words = db.query_receipt_words(IMAGE_ID, 1).await.unwrap();
    assert_eq!(words.len(), 1);
    assert_eq!(words[0].geom.text, "MILK");

    let lines = db.query_receipt_lines(IMAGE_ID, 1).await.unwrap();
    assert_eq!(lines.len(), 1);

    let labels = db.query_labels_by_name("PRODUCT_NAME").await.unwrap();
    assert_eq!(labels.len(), 1);

    let details = db.query_receipt_details(IMAGE_ID, 1).await.unwrap();
    assert!(details.len() >= 4);

    let partition = db.query_image_partition(IMAGE_ID).await.unwrap();
    assert!(partition.len() >= 5);
}

#[tokio::test]
async fn batch_put_chunks_and_roundtrips() {
    let db = ReceiptDynamo::memory();
    let mut words = Vec::new();
    for i in 1..=40u32 {
        words.push(ReceiptWord::new(1, 1, i, geom(&format!("W{i}"))).unwrap());
    }
    db.batch_put_entities(&words).await.unwrap();
    let listed = db.query_receipt_words(IMAGE_ID, 1).await.unwrap();
    assert_eq!(listed.len(), 40);

    let keys: Vec<_> = words.iter().map(|w| w.primary_key()).collect();
    let got = db.batch_get_items(keys).await.unwrap();
    assert_eq!(got.len(), 40);
}

#[tokio::test]
async fn circuit_breaker_opens_on_retryable_failures() {
    use receipt_dynamo::circuit_breaker::CircuitBreaker;
    use receipt_dynamo::Error;
    use std::sync::atomic::{AtomicU32, Ordering};
    use std::time::Duration;

    let breaker = CircuitBreaker::new(2, Duration::from_secs(30));
    let hits = AtomicU32::new(0);
    let fail = || async {
        hits.fetch_add(1, Ordering::SeqCst);
        Err::<(), _>(Error::Throughput("slow".into()))
    };
    assert!(breaker.call(fail).await.is_err());
    assert!(breaker.call(fail).await.is_err());
    let third = breaker.call(fail).await;
    assert!(matches!(third, Err(Error::CircuitOpen)));
}
