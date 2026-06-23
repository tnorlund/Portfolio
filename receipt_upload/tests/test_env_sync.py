"""Unit tests for cross-env migration helpers (pure functions)."""

from receipt_upload.env_sync.apply import _remap_item_buckets
from receipt_upload.env_sync.plan import remap_bucket


def test_remap_bucket_dev_prod_roundtrip():
    assert (
        remap_bucket("raw-image-bucket-c779c32") == "raw-image-bucket-0facc78"
    )
    assert (
        remap_bucket("raw-image-bucket-0facc78") == "raw-image-bucket-c779c32"
    )
    assert remap_bucket("sitebucket-ad92f1f") == "sitebucket-778abc9"


def test_remap_bucket_shared_unchanged():
    # the shared upload bucket and any unknown bucket map to themselves
    assert (
        remap_bucket("upload-images-image-bucket-4bcea7e")
        == "upload-images-image-bucket-4bcea7e"
    )
    assert remap_bucket("some-other-bucket") == "some-other-bucket"


def test_remap_item_buckets_rewrites_only_bucket_fields():
    item = {
        "PK": {"S": "IMAGE#abc"},
        "raw_s3_bucket": {"S": "raw-image-bucket-c779c32"},
        "raw_s3_key": {"S": "raw/abc.png"},
        "cdn_s3_bucket": {"S": "sitebucket-ad92f1f"},
        "cdn_s3_key": {"S": "assets/abc.webp"},
        "upload_bucket": {"S": "upload-images-image-bucket-4bcea7e"},
    }
    out = _remap_item_buckets(dict(item))
    assert out["raw_s3_bucket"]["S"] == "raw-image-bucket-0facc78"
    assert out["cdn_s3_bucket"]["S"] == "sitebucket-778abc9"
    # keys are untouched; non-*_s3_bucket attrs untouched
    assert out["raw_s3_key"]["S"] == "raw/abc.png"
    assert out["upload_bucket"]["S"] == "upload-images-image-bucket-4bcea7e"
    assert out["PK"]["S"] == "IMAGE#abc"
