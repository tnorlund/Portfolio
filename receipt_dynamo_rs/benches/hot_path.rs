use criterion::{black_box, criterion_group, criterion_main, BatchSize, Criterion};
use receipt_dynamo::entities::Entity;
use receipt_dynamo::{
    format_float, pad5, parse_receipt_amount, ReceiptDynamo, ReceiptWord, ReceiptWordLabel,
    TextGeometry,
};

const IMAGE_ID: &str = "f47ac10b-58cc-4372-a567-0e02b2c3d479";

fn geom(text: &str) -> TextGeometry {
    TextGeometry::unit_box(IMAGE_ID, text).unwrap()
}

fn benches(c: &mut Criterion) {
    c.bench_function("format_float_20dp", |b| {
        b.iter(|| format_float(black_box(0.123456789012345), 20))
    });

    c.bench_function("pad5", |b| b.iter(|| pad5(black_box(42))));

    c.bench_function("parse_receipt_amount", |b| {
        b.iter(|| parse_receipt_amount(black_box("$1,234.56")))
    });

    let word = ReceiptWord::new(1, 3, 4, geom("MILK")).unwrap();
    c.bench_function("receipt_word_to_item", |b| {
        b.iter(|| black_box(word.to_item()))
    });

    let item = word.to_item();
    c.bench_function("receipt_word_from_item", |b| {
        b.iter(|| ReceiptWord::from_item(black_box(&item)).unwrap())
    });

    c.bench_function("serialize_1000_words", |b| {
        b.iter_batched(
            || {
                (1..=1000u32)
                    .map(|i| ReceiptWord::new(1, 1, i, geom("MILK")).unwrap())
                    .collect::<Vec<_>>()
            },
            |words| {
                words.iter().map(ReceiptWord::to_item).count();
            },
            BatchSize::SmallInput,
        )
    });

    c.bench_function("label_to_item", |b| {
        let label = ReceiptWordLabel::new(
            IMAGE_ID,
            1,
            3,
            4,
            "PRODUCT_NAME",
            Some("item".into()),
            "2026-08-20T00:00:00",
        )
        .unwrap();
        b.iter(|| black_box(label.to_item()))
    });

    let rt = tokio::runtime::Runtime::new().unwrap();
    c.bench_function("memory_batch_put_100_words", |b| {
        b.iter_batched(
            || {
                let db = ReceiptDynamo::memory();
                let words: Vec<_> = (1..=100u32)
                    .map(|i| ReceiptWord::new(1, 1, i, geom("MILK")).unwrap())
                    .collect();
                (db, words)
            },
            |(db, words)| {
                rt.block_on(async move {
                    db.batch_put_entities(&words).await.unwrap();
                });
            },
            BatchSize::SmallInput,
        )
    });
}

criterion_group!(hot_path, benches);
criterion_main!(hot_path);
