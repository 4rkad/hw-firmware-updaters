import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("trezor_backend", ROOT / "trezor-updater.py")
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)


def release(version, minimum, bitcoin_only=False):
    item = {
        "version": list(version),
        "min_firmware_version": list(minimum),
        "url": "data/firmware/t2b1/trezor.bin",
        "fingerprint": "00" * 32,
    }
    if bitcoin_only:
        item["url_bitcoinonly"] = "data/firmware/t2b1/trezor-btc.bin"
        item["fingerprint_bitcoinonly"] = "11" * 32
    return item


RELEASES = [
    release((2, 0, 0), (1, 0, 0)),
    release((2, 5, 0), (2, 0, 0)),
    release((2, 12, 5), (2, 5, 0)),
]


def test_latest_is_used_when_directly_reachable():
    chosen = backend.best_release(RELEASES, (2, 11, 0), False, False)
    assert chosen["version"] == [2, 12, 5]


def test_intermediate_release_is_selected_when_jump_is_too_large():
    chosen = backend.best_release(RELEASES, (2, 1, 0), False, False)
    assert chosen["version"] == [2, 5, 0]


def test_route_steps_down_to_the_first_compatible_release():
    chosen = backend.best_release(RELEASES, (1, 8, 0), False, False)
    assert chosen["version"] == [2, 0, 0]


def test_bitcoin_only_requires_a_bitcoin_only_asset():
    with pytest.raises(RuntimeError):
        backend.best_release(RELEASES, (2, 11, 0), False, True)


def test_bitcoin_only_release_is_selected_when_present():
    releases = [release((2, 12, 5), (2, 5, 0), bitcoin_only=True)]
    chosen = backend.best_release(releases, (2, 11, 0), False, True)
    assert chosen["version"] == [2, 12, 5]
    assert backend.release_fingerprint(chosen, True) == "11" * 32
