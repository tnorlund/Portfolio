use receipt_dynamo::attr::{format_float, serialize_bounding_box, serialize_confidence, ItemExt};
use receipt_dynamo::entities::{Entity, Point, ReceiptWord, ReceiptWordLabel, TextGeometry};
use receipt_dynamo::geometry::{BoundingBox, Corners};
use receipt_dynamo::{Image, Line, Receipt, Word};

const IMAGE_ID: &str = "f47ac10b-58cc-4372-a567-0e02b2c3d479";

fn geom(text: &str) -> TextGeometry {
    TextGeometry::unit_box(IMAGE_ID, text).unwrap()
}

#[test]
fn format_float_matches_python_fixture_values() {
    assert_eq!(format_float(0.1, 20), "0.10000000000000000000");
    assert_eq!(format_float(0.2, 20), "0.20000000000000000000");
    assert_eq!(format_float(0.0, 20), "0.00000000000000000000");
    assert_eq!(format_float(999.999, 20), "999.99900000000000000000");
    assert_eq!(format_float(0.95, 2), "0.95");
}

#[test]
fn bounding_box_wire_shape() {
    let attr = serialize_bounding_box(0.1, 0.2, 0.3, 0.4);
    let json = attr.to_wire_json();
    assert_eq!(json["M"]["x"]["N"], "0.10000000000000000000");
    assert_eq!(json["M"]["height"]["N"], "0.40000000000000000000");
    let conf = serialize_confidence(0.95).to_wire_json();
    assert_eq!(conf["N"], "0.95");
}

#[test]
fn receipt_word_roundtrip() {
    let word = ReceiptWord::new(1, 3, 4, geom("MILK")).unwrap();
    let item = word.to_item();
    let json = item.to_wire_json();
    assert_eq!(json["PK"]["S"], format!("IMAGE#{IMAGE_ID}"));
    assert_eq!(json["SK"]["S"], "RECEIPT#00001#LINE#00003#WORD#00004");
    assert_eq!(json["TYPE"]["S"], "RECEIPT_WORD");
    assert_eq!(json["GSI4SK"]["S"], "3_WORD#00003#00004");
    assert_eq!(json["GSI3SK"]["S"], "WORD");
    let back = ReceiptWord::from_item(&item).unwrap();
    assert_eq!(back.geom.text, "MILK");
    assert_eq!(back.line_id, 3);
    assert_eq!(back.word_id, 4);
}

#[test]
fn receipt_word_label_roundtrip_and_gsi1_padding() {
    let label = ReceiptWordLabel::new(
        IMAGE_ID,
        1,
        3,
        4,
        "product_name",
        Some("looks like an item".into()),
        "2026-08-20T00:00:00",
    )
    .unwrap();
    assert_eq!(label.label, "PRODUCT_NAME");
    let item = label.to_item();
    let json = item.to_wire_json();
    assert_eq!(json["GSI1PK"]["S"].as_str().unwrap().len(), 40);
    assert!(json["GSI1PK"]["S"]
        .as_str()
        .unwrap()
        .starts_with("LABEL#PRODUCT_NAME"));
    assert_eq!(
        json["SK"]["S"],
        "RECEIPT#00001#LINE#00003#WORD#00004#LABEL#PRODUCT_NAME"
    );
    let back = ReceiptWordLabel::from_item(&item).unwrap();
    assert_eq!(back.label, "PRODUCT_NAME");
    assert_eq!(back.validation_status.as_str(), "NONE");
}

#[test]
fn image_and_receipt_roundtrip() {
    let image = Image::new(
        IMAGE_ID,
        1000,
        2000,
        "2026-08-20T00:00:00",
        "bucket",
        "raw/key.png",
    )
    .unwrap();
    let back = Image::from_item(&image.to_item()).unwrap();
    assert_eq!(back.width, 1000);
    assert_eq!(back.image_type.as_str(), "SCAN");

    let receipt = Receipt::new(
        IMAGE_ID,
        1,
        800,
        1600,
        "2026-08-20T00:00:00",
        "bucket",
        "raw/key.png",
        Point { x: 0.0, y: 0.0 },
        Point { x: 1.0, y: 0.0 },
        Point { x: 0.0, y: 1.0 },
        Point { x: 1.0, y: 1.0 },
    )
    .unwrap();
    let json = receipt.to_item().to_wire_json();
    assert_eq!(json["GSI4SK"]["S"], "0_RECEIPT");
    assert_eq!(json["TYPE"]["S"], "RECEIPT");
    let back = Receipt::from_item(&receipt.to_item()).unwrap();
    assert_eq!(back.receipt_id, 1);
}

#[test]
fn line_and_word_keys() {
    let line = Line::new(2, geom("HELLO WORLD")).unwrap();
    let json = line.to_item().to_wire_json();
    assert_eq!(json["SK"]["S"], "LINE#00002");
    let word = Word::new(2, 1, geom("HELLO")).unwrap();
    let json = word.to_item().to_wire_json();
    assert_eq!(json["SK"]["S"], "LINE#00002#WORD#00001");
    assert_eq!(json["extracted_data"]["NULL"], true);
}

#[test]
fn geometry_translate_updates_bbox() {
    let mut g = geom("X");
    g.geometry.translate(0.1, 0.2);
    assert!((g.geometry.bounding_box.x - 0.2).abs() < 1e-12);
    let box2 = BoundingBox::new(0.0, 0.0, 1.0, 1.0).unwrap();
    assert!(box2.contains(0.5, 0.5));
    let _ = Corners {
        top_left: Point { x: 0.0, y: 0.0 },
        top_right: Point { x: 1.0, y: 0.0 },
        bottom_left: Point { x: 0.0, y: 1.0 },
        bottom_right: Point { x: 1.0, y: 1.0 },
    };
}

#[test]
fn rejects_invalid_uuid_and_confidence() {
    assert!(TextGeometry::unit_box("nope", "x").is_err());
    let mut g = geom("x");
    g.confidence = 0.0;
    assert!(Line::new(1, g.clone()).is_ok()); // Line::new doesn't re-validate confidence
    assert!(TextGeometry::new(
        IMAGE_ID,
        "x",
        BoundingBox::new(0.0, 0.0, 1.0, 1.0).unwrap(),
        Corners {
            top_left: Point { x: 0.0, y: 0.0 },
            top_right: Point { x: 1.0, y: 0.0 },
            bottom_left: Point { x: 0.0, y: 1.0 },
            bottom_right: Point { x: 1.0, y: 1.0 },
        },
        0.0,
        0.0,
        0.0,
    )
    .is_err());
}
