import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ledger_updater", ROOT / "ledger-updater.py")
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)


def test_parse_apdu_script_ignores_comments_and_spaces():
    apdus = backend.parse_apdu_script("E0 01 00 00 # comment\n\nE0 02 00 00\n")
    assert apdus == [bytes.fromhex("E0010000"), bytes.fromhex("E0020000")]


def test_parse_apdu_script_rejects_empty_script():
    with pytest.raises(RuntimeError):
        backend.parse_apdu_script("  # only a comment\n")


def test_parse_apdu_script_rejects_invalid_hex():
    with pytest.raises(ValueError):
        backend.parse_apdu_script("ZZ")


@pytest.mark.parametrize("size", list(range(0, 24)))
def test_parse_firmware_info_never_raises_index_error(size):
    data = bytes(range(size))
    try:
        backend.parse_firmware_info(data)
    except RuntimeError:
        pass
