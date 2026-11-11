import importlib.util
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ledger_updater", ROOT / "ledger-updater.py")
lu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lu)


def lv(value: bytes) -> bytes:
    return bytes([len(value)]) + value


def firmware_info(target=0x33100004, version=b"1.7.0", mcu=b"1.7", bootloader=None, se_target=0x33100004):
    data = target.to_bytes(4, "big") + lv(version) + lv(b"\x8c\x00\x00\x00")
    if bootloader is None:
        data += lv(mcu)
    else:
        data += lv(b"1.6.1") + lv(se_target.to_bytes(4, "big"))
    return data


def app_entry(name="Bitcoin", blocks=5, flags=0):
    body = (
        blocks.to_bytes(2, "big")
        + flags.to_bytes(2, "big")
        + bytes(range(32))
        + bytes(range(32, 64))
        + bytes([len(name)])
        + name.encode()
    )
    return bytes([1 + len(body)]) + body


def test_norm_version_and_semver():
    assert lu.norm_version("1.7.0") == (1, 7, 0)
    assert lu.norm_version("1.7") == (1, 7, 0)
    assert lu.norm_version("v1.7.0") is None
    assert lu.semver_gt("1.7.0", "1.6.9")
    assert not lu.semver_gt("1.6.0", "1.7.0")


def test_parse_firmware_info_normal():
    info = lu.parse_firmware_info(firmware_info())
    assert info["target_id"] == 0x33100004
    assert info["se_version"] == "1.7.0"
    assert info["mcu_version"] == "1.7"
    assert info["is_bootloader"] is False
    assert lu.manager_allowed(info) is True


def test_parse_firmware_info_bootloader():
    data = firmware_info(target=0x01000000, version=b"1.7.0", bootloader=b"0.11")
    info = lu.parse_firmware_info(data)
    assert info["is_bootloader"] is True
    assert info["mcu_bl_version"] == "1.7.0"
    assert info["se_target_id"] == 0x33100004


def test_parse_list_apps_real_format():
    data = b"\x01" + app_entry("Bitcoin Test") + app_entry("Ethereum", 9)
    apps = lu.parse_list_apps(data)
    assert [a["name"] for a in apps] == ["Bitcoin Test", "Ethereum"]
    assert apps[1]["blocks"] == 9
    assert len(apps[0]["hash"]) == 64


def test_parse_list_apps_truncated():
    data = b"\x01" + app_entry("Bitcoin")
    with pytest.raises(RuntimeError):
        lu.parse_list_apps(data[:-1])


def test_parse_list_apps_wrong_format():
    with pytest.raises(RuntimeError):
        lu.parse_list_apps(b"\x02" + app_entry("Bitcoin"))


def test_parse_language_pack():
    pack = lu.parse_language_pack(bytes([1, 9, 1, 2, 2, 1, 0]))
    assert pack == {"id": 2, "size": 256}
    assert lu.parse_language_pack(b"") is None


def test_certificate_roundtrip():
    cert = lu._certificate(b"\x04" + b"\x01" * 64, b"\x30\x01")
    pub, i = lu._lv(cert, 0)
    sig, i = lu._lv(cert, i)
    assert pub == b"\x04" + b"\x01" * 64
    assert sig == b"\x30\x01"
    assert i == len(cert)


def test_ecdsa_verify():
    key = ec.generate_private_key(ec.SECP256K1())
    pub = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    msg = b"attestation"
    sig = lu._sign_der(key, msg)
    assert lu._verify_der(pub, msg, sig)
    assert not lu._verify_der(pub, b"other", sig)


def test_find_update_path_explicit_and_wildcard():
    edges = {
        "1.0.0": [("1.1.0", {}, {"name": "1.0.0-to-1.1.0"})],
        None: [("2.0.0", {}, {"name": "2.0.0-osu"})],
    }
    path = lu.find_update_path(edges, "1.0.0", "1.1.0")
    assert [step[0] for step in path] == ["1.1.0"]
    path = lu.find_update_path(edges, "1.0.0", "2.0.0")
    assert [step[0] for step in path] == ["2.0.0"]


def test_build_edges_legacy_and_explicit():
    finals = [
        {
            "version": "1.1.0",
            "device_versions": [16],
            "osu_versions": [{"name": "1.0.0-to-1.1.0"}],
        },
        {
            "version": "2.0.0",
            "device_versions": [16],
            "osu_versions": [{"name": "2.0.0-osu"}, {"name": "2.0.0-rc1-osu"}],
        },
    ]
    edges = lu.build_edges(finals, 16)
    assert edges["1.0.0"][0][0] == "1.1.0"
    assert [step[0] for step in edges[None]] == ["2.0.0"]
    assert edges[None][0][2]["name"] == "2.0.0-osu"


def test_notes_for_final():
    final = {
        "version": "1.7.0",
        "notes": None,
        "osu_versions": [
            {"name": "1.6.1-to-1.7.0", "notes": "notas"},
            {"name": "1.7.0-rc1-to-1.7.0", "notes": "rc"},
        ],
    }
    assert lu.notes_for_final(final) == "notas"


def test_changelog_up_to_date_includes_notes():
    latest = {
        "version": "1.7.0",
        "notes": None,
        "osu_versions": [{"name": "1.7.0-osu", "notes": "## Novedades"}],
    }
    plan = {
        "info": {"se_version": "1.7.0"},
        "target_id": 0x33100004,
        "latest": latest,
        "current": "1.7.0",
        "route": [],
    }
    text = lu.changelog_text(plan)
    assert "Ya tienes la última versión" in text
    assert "## Novedades" in text


def test_offline_blocks_network():
    lu.set_offline(True)
    try:
        with pytest.raises(RuntimeError):
            lu.http_json(lu.MANAGER_API + "/get_device_version")
        with pytest.raises(RuntimeError):
            lu.http_post_json(lu.MANAGER_API + "/v2/apps/hash", [])
        with pytest.raises(RuntimeError):
            lu.fetch_text("https://download.languages.ledger.com/x.apdu")
        with pytest.raises(lu.SocketError):
            lu.run_socket(object(), "genuine", {}, lambda *a: None, lambda *a: None)
    finally:
        lu.set_offline(False)


def test_host_allowlist():
    with pytest.raises(RuntimeError):
        lu.http_json("https://evil.example/api")
    with pytest.raises(RuntimeError):
        lu.fetch_text("https://evil.example/x.apdu")
