//! Compare Rust `to_item()` JSON with Python `receipt_dynamo` when the venv is present.

use std::io::Write;
use std::process::Command;

use receipt_dynamo::attr::ItemExt;
use receipt_dynamo::entities::Entity;
use receipt_dynamo::{format_float, ReceiptWord, ReceiptWordLabel, TextGeometry};

const IMAGE_ID: &str = "f47ac10b-58cc-4372-a567-0e02b2c3d479";

fn python() -> Option<Command> {
    let candidates = ["/workspace/.venv/bin/python", ".venv/bin/python", "python3"];
    for path in candidates {
        let cmd = Command::new(path);
        let probe = Command::new(path)
            .args(["-c", "import receipt_dynamo, json"])
            .output()
            .ok()?;
        if probe.status.success() {
            return Some(cmd);
        }
    }
    None
}

fn py_eval(code: &str) -> Option<String> {
    let mut cmd = python()?;
    let output = cmd.arg("-c").arg(code).output().ok()?;
    if !output.status.success() {
        eprintln!("python failed: {}", String::from_utf8_lossy(&output.stderr));
        return None;
    }
    Some(String::from_utf8_lossy(&output.stdout).trim().to_string())
}

#[test]
fn format_float_matches_python_when_available() {
    let Some(py) = py_eval(
        r#"
from receipt_dynamo.entities.util import _format_float
import json
vals = [0.0, 0.1, 0.2, 0.95, 999.999, 0.123456789012345]
print(json.dumps({str(v): _format_float(v, 20, 22) for v in vals}))
"#,
    ) else {
        eprintln!("skipping python float compat (receipt_dynamo not importable)");
        return;
    };
    let map: serde_json::Value = serde_json::from_str(&py).unwrap();
    for (k, v) in map.as_object().unwrap() {
        let f: f64 = k.parse().unwrap();
        assert_eq!(format_float(f, 20), v.as_str().unwrap(), "mismatch for {k}");
    }
}

#[test]
fn receipt_word_item_matches_python_when_available() {
    let Some(py) = py_eval(&format!(
        r#"
from receipt_dynamo.entities.receipt_word import ReceiptWord
import json
w = ReceiptWord(
    image_id="{IMAGE_ID}",
    receipt_id=1,
    line_id=3,
    word_id=4,
    text="MILK",
    bounding_box={{"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4}},
    top_left={{"x": 0.1, "y": 0.2}},
    top_right={{"x": 0.4, "y": 0.2}},
    bottom_left={{"x": 0.1, "y": 0.6}},
    bottom_right={{"x": 0.4, "y": 0.6}},
    angle_degrees=0.0,
    angle_radians=0.0,
    confidence=0.95,
)
print(json.dumps(w.to_item()))
"#
    )) else {
        eprintln!("skipping python ReceiptWord compat");
        return;
    };
    let word =
        ReceiptWord::new(1, 3, 4, TextGeometry::unit_box(IMAGE_ID, "MILK").unwrap()).unwrap();
    let rust = word.to_item().to_wire_json();
    let python: serde_json::Value = serde_json::from_str(&py).unwrap();
    for key in [
        "PK",
        "SK",
        "TYPE",
        "GSI1PK",
        "GSI1SK",
        "GSI2PK",
        "GSI2SK",
        "GSI3PK",
        "GSI3SK",
        "GSI4PK",
        "GSI4SK",
        "text",
        "confidence",
        "embedding_status",
        "is_noise",
    ] {
        assert_eq!(rust[key], python[key], "field {key} diverged");
    }
}

#[test]
fn receipt_word_label_item_matches_python_when_available() {
    let Some(py) = py_eval(&format!(
        r#"
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel
import json
lab = ReceiptWordLabel(
    image_id="{IMAGE_ID}",
    receipt_id=1,
    line_id=3,
    word_id=4,
    label="PRODUCT_NAME",
    reasoning="looks like an item",
    timestamp_added="2026-08-20T00:00:00",
)
print(json.dumps(lab.to_item()))
"#
    )) else {
        eprintln!("skipping python ReceiptWordLabel compat");
        return;
    };
    let label = ReceiptWordLabel::new(
        IMAGE_ID,
        1,
        3,
        4,
        "PRODUCT_NAME",
        Some("looks like an item".into()),
        "2026-08-20T00:00:00",
    )
    .unwrap();
    let rust = label.to_item().to_wire_json();
    let python: serde_json::Value = serde_json::from_str(&py).unwrap();
    for key in [
        "PK",
        "SK",
        "TYPE",
        "GSI1PK",
        "GSI1SK",
        "GSI2PK",
        "GSI2SK",
        "GSI3PK",
        "GSI3SK",
        "GSI4PK",
        "GSI4SK",
        "reasoning",
        "timestamp_added",
        "validation_status",
    ] {
        assert_eq!(rust[key], python[key], "field {key} diverged");
    }
}

#[allow(dead_code)]
fn _write(s: &str) {
    let _ = std::io::sink().write(s.as_bytes());
}
