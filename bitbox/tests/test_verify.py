import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bitbox_backend", ROOT / "bitbox-updater.py")
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)


def test_verify_blob_rejects_missing_digest():
    with pytest.raises(RuntimeError, match="SHA-256"):
        backend.verify_blob(b"firmware", {}, "bitbox02", "btconly", lambda *_: None)


def test_verify_blob_rejects_wrong_digest():
    digest = hashlib.sha256(b"other").hexdigest()
    asset = {"digest": f"sha256:{digest}"}
    with pytest.raises(RuntimeError, match="NO coincide"):
        backend.verify_blob(b"firmware", asset, "bitbox02", "btconly", lambda *_: None)
