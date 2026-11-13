#!/usr/bin/env python3
"""Ledger Firmware Updater — actualiza el firmware de tu Ledger sin Ledger Wallet (ex Ledger Live).

A diferencia de Trezor o BitBox, Ledger no publica binarios de firmware: el flasheo lo
orquesta un HSM de Ledger por WebSocket. Este programa hace de puente: consulta la API
pública de Ledger, elige la ruta de actualización (OSU) y retransmite los APDU al
dispositivo. Los APDU firmados se retransmiten por el host; no se pueden forjar ni descifrar.

Incluye: detección de modelo y versión, consulta de la última versión estable, ruta de
actualización por pasos, flasheo de OSU, MCU y bootloader, verificación de genuinidad
online (HSM) y offline (attestation), reinstalación automática de apps y del pack de
idioma, y un gestor para instalar y desinstalar apps del catálogo oficial.

La lista de apps se lee con LIST_APPS (E0 DE / E0 DF) y se reinstala con el mismo HSM,
usando el catálogo /v2/apps/by-target del firmware nuevo (ideas tomadas de LSdk).

Uso:
    ./arrancar.sh                          (abre la interfaz gráfica)
    python ledger-updater.py --selftest    (consulta la API sin dispositivo)
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import queue
import re
import secrets
import socket
import ssl
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
from collections import defaultdict

try:
    import hid
except ImportError:
    print("Falta hidapi. Instala con:  pip install hidapi")
    sys.exit(1)

try:
    import websocket
except ImportError:
    print("Falta websocket-client. Instala con:  pip install websocket-client")
    sys.exit(1)

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
except ImportError:
    print("Falta cryptography. Instala con:  pip install cryptography")
    sys.exit(1)

LEDGER_VID = 0x2C97
MANAGER_API = "https://manager.api.live.ledger.com/api"
SOCKET_BASE = "wss://scriptrunner.api.live.ledger.com/update"
LIVE_COMMON_VERSION = os.environ.get("LIVE_COMMON_VERSION", "38.6.0")
USER_AGENT = "ledger-updater"

# targetId -> nombre del modelo
MODELS = {
    0x31100004: "Ledger Nano S",
    0x33000004: "Ledger Nano X",
    0x33100004: "Ledger Nano S Plus",
    0x33200004: "Ledger Stax",
    0x33300004: "Ledger Flex",
    0x33400004: "Ledger Nano Gen5",
}
MODEL_MASK = {tid & 0xFFFF0000: name for tid, name in MODELS.items()}
# PID legacy (modo bootloader) -> PID normal (productIdMM << 8 | 0x15)
LEGACY_PID = {0x0001: 0x1015, 0x0004: 0x4015, 0x0005: 0x5015, 0x0006: 0x6015, 0x0007: 0x7015, 0x0008: 0x8015}
PID_TARGET = {
    0x1015: 0x31100004,
    0x4015: 0x33000004,
    0x5015: 0x33100004,
    0x6015: 0x33200004,
    0x7015: 0x33300004,
    0x8015: 0x33400004,
}
PROVIDERS = {"default": 1, "das": 2, "club": 3, "shitcoins": 4, "ee": 5}

# Modo offline: bloquea toda conexión de red (API y HSM). El chequeo de
# genuinidad offline y el listado local de apps siguen funcionando.
OFFLINE_ONLY = False


def set_offline(value: bool):
    global OFFLINE_ONLY
    OFFLINE_ONLY = bool(value)

SECURE_CHANNEL_CLA = b"\xe0\x51"
GET_CERTIFICATE_CLA = b"\xe0\x52"

# Clave pública de issuer que usa Ledger en su loader (checkGenuine.py) para
# verificar el certificado de fábrica del secure element, sin red.
ISSUER_KEYS = [
    (
        "vendor issuer key #1",
        "0490f5c9d15a0134bb019d2afd0bf297149738459706e7ac5be4abc350a1f818057224fce12ec9a65de18ec34d6e8c24db927835ea1692b14c32e9836a75dad609",
    )
]

# Dashboard (BOLOS): listado de apps instaladas (LSdk: E0 DE / E0 DF)
LIST_APPS = b"\xe0\xde\x00\x00\x00"
LIST_APPS_CONTINUE = b"\xe0\xdf\x00\x00\x00"
# Packs de idioma (BOLOS): E0 34 listar, E0 33 borrar
LIST_LANG_FIRST = b"\xe0\x34\x00\x00\x00"
LIST_LANG_NEXT = b"\xe0\x34\x01\x00\x00"
LANGUAGE_HOST = "download.languages.ledger.com"
LANGUAGE_NAMES = {
    0: "english",
    1: "french",
    2: "spanish",
    3: "brazilian",
    4: "german",
    5: "russian",
    6: "turkish",
}
LANGUAGE_NAME_TO_ID = {v: k for k, v in LANGUAGE_NAMES.items()}
LANGUAGE_NAME_TO_ID["portuguese"] = 3

# Apps cuyo hash cambia en cada arranque; no sirven para detectar actualizaciones.
DYNAMIC_HASHES = {"fido u2f", "security key"}


# --------------------------------------------------------------------------- utilidades


def _tls_context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def _spki_pin(cert_der: bytes) -> str:
    cert = x509.load_der_x509_certificate(cert_der)
    spki = cert.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return hashlib.sha256(spki).hexdigest()


def _check_spki_pin(cert_der: bytes | None) -> None:
    pins = {p.strip().lower() for p in os.environ.get("LEDGER_SPKI_PINS", "").split(",") if p.strip()}
    if not pins or not cert_der:
        return
    if _spki_pin(cert_der) not in pins:
        raise RuntimeError(
            "El certificado TLS de Ledger no coincide con LEDGER_SPKI_PINS. "
            "Posible interceptación; no se continúa."
        )


def tls_pin_report(hosts=("manager.api.live.ledger.com", "scriptrunner.api.live.ledger.com")) -> int:
    for host in hosts:
        try:
            ctx = _tls_context()
            with socket.create_connection((host, 443), timeout=15) as raw:
                with ctx.wrap_socket(raw, server_hostname=host) as tls:
                    der = tls.getpeercert(binary_form=True)
            cert = x509.load_der_x509_certificate(der)
            expires = getattr(cert, "not_valid_after_utc", None)
            if expires is None:
                expires = cert.not_valid_after
            print(f"{host}  spki-sha256={_spki_pin(der)}  expires={expires:%Y-%m-%d}")
        except Exception as exc:
            print(f"{host}  ERROR: {exc}")
            return 1
    return 0


def _https_request(url: str, method: str = "GET", body: bytes | None = None,
                   headers: dict | None = None, timeout: float = 30.0) -> bytes:
    parsed = urllib.parse.urlparse(url)
    conn = http.client.HTTPSConnection(
        parsed.hostname, parsed.port or 443, timeout=timeout, context=_tls_context()
    )
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query
    try:
        conn.request(method, target, body=body, headers=headers or {})
        sock = getattr(conn, "sock", None)
        if sock is not None and hasattr(sock, "getpeercert"):
            _check_spki_pin(sock.getpeercert(binary_form=True))
        resp = conn.getresponse()
        data = resp.read()
        if resp.status >= 400:
            exc = RuntimeError(f"HTTP {resp.status} desde {parsed.hostname}")
            exc.code = resp.status
            raise exc
        return data
    finally:
        conn.close()


def http_json(url: str, timeout: float = 30.0):
    if OFFLINE_ONLY:
        raise RuntimeError("Modo offline: esta acción necesita conexión con Ledger")
    parsed = urllib.parse.urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "manager.api.live.ledger.com"
        or not url.startswith(MANAGER_API + "/")
    ):
        raise RuntimeError(f"Destino HTTP no permitido: {url}")
    data = _https_request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=timeout,
    )
    return json.loads(data.decode())


def http_post_json(url: str, payload, timeout: float = 30.0):
    if OFFLINE_ONLY:
        raise RuntimeError("Modo offline: esta acción necesita conexión con Ledger")
    parsed = urllib.parse.urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "manager.api.live.ledger.com"
        or not url.startswith(MANAGER_API + "/")
    ):
        raise RuntimeError(f"Destino HTTP no permitido: {url}")
    body = json.dumps(payload).encode()
    data = _https_request(
        url,
        method="POST",
        body=body,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        timeout=timeout,
    )
    return json.loads(data.decode())


def api_get(path: str, **params):
    params.setdefault("livecommonversion", LIVE_COMMON_VERSION)
    url = MANAGER_API + path + "?" + urllib.parse.urlencode(params)
    try:
        return http_json(url)
    except Exception as exc:
        code = getattr(exc, "code", None)
        if code in (400, 404, 410, 422):
            raise RuntimeError(
                f"La API de Ledger rechazó {path} (HTTP {code}). "
                "Puede haber cambiado; prueba LIVE_COMMON_VERSION=<versión> "
                "o usa Ledger Live temporalmente (ver docs/LEDGER-API.md)."
            ) from exc
        raise


def norm_version(value: str) -> tuple[int, ...] | None:
    """'1.7.0' -> (1,7,0). Devuelve None si no es una versión numérica."""
    if not value:
        return None
    m = re.fullmatch(r"(\d+)\.(\d+)(?:\.(\d+))?", value.strip())
    if not m:
        return None
    return tuple(int(x) for x in m.groups(default="0"))


def is_stable(value: str) -> bool:
    return re.fullmatch(r"\d+\.\d+\.\d+", value or "") is not None


def semver_gt(a: str, b: str) -> bool:
    va = norm_version(a)
    vb = norm_version(b)
    return bool(va and vb and va > vb)


def model_for_target(target_id: int) -> str | None:
    return MODELS.get(target_id) or MODEL_MASK.get(target_id & 0xFFFF0000)


def model_for_pid(pid: int) -> str | None:
    normal = LEGACY_PID.get(pid, pid)
    return MODELS.get(PID_TARGET.get(normal, 0))


def provider_from_version(version: str) -> int:
    if "-" in version:
        suffix = version.rsplit("-", 1)[-1]
        if suffix in PROVIDERS:
            return PROVIDERS[suffix]
    return 1


def clean_version(version: str) -> str:
    return (version or "").replace("-osu", "").split("-")[0]


# --------------------------------------------------------------------------- API Ledger

_finals_cache: list[dict] | None = None


def fetch_all_finals() -> list[dict]:
    global _finals_cache
    if _finals_cache is None:
        data = api_get("/firmware_final_versions")
        if not isinstance(data, list):
            raise RuntimeError("Respuesta inesperada de /firmware_final_versions")
        _finals_cache = data
    return _finals_cache


def latest_final(finals: list[dict], device_version_id: int) -> dict | None:
    candidates = [
        f
        for f in finals
        if device_version_id in (f.get("device_versions") or [])
        and is_stable(f.get("version", ""))
        and not f.get("exclude_by_default")
    ]
    if not candidates:
        return None
    # La versión más alta no siempre es la más reciente (hay líneas antiguas de prototipos),
    # así que ordenamos por fecha de publicación y desempatamos por versión.
    return max(
        candidates,
        key=lambda f: (f.get("date_last_modified") or "", norm_version(f["version"]) or (0,)),
    )


def build_edges(finals: list[dict], device_version_id: int) -> dict[str | None, list[tuple[str, dict, dict]]]:
    edges: dict[str | None, list[tuple[str, dict, dict]]] = defaultdict(list)
    legacy: dict[str, tuple[dict, dict]] = {}
    for final in finals:
        if device_version_id not in (final.get("device_versions") or []):
            continue
        target = final.get("version", "")
        if not is_stable(target):
            continue
        for osu in final.get("osu_versions") or []:
            name = osu.get("name") or ""
            if "hidden" in name:
                continue
            m = re.fullmatch(r"(.+?)-to-(.+)", name)
            if m:
                src, dst = m.group(1), m.group(2)
                if dst == target:
                    edges[src].append((dst, final, osu))
                continue
            # Esquema antiguo: "2.1.0-osu" (sin versión de origen). Se aplica como comodín.
            if re.fullmatch(rf"{re.escape(target)}(?:-[A-Za-z0-9._]+)?-osu", name) and (
                target not in legacy or name == f"{target}-osu"
            ):
                # Preferimos siempre la variante genérica "X-osu" a las de una rc concreta.
                legacy[target] = (final, osu)
    for target, (final, osu) in legacy.items():
        edges[None].append((target, final, osu))
    return edges


def find_update_path(edges, current: str, target: str, max_depth: int = 6):
    """BFS de versiones origen -> destino usando los OSU publicados por Ledger."""
    if current == target:
        return []
    current_nv = norm_version(current) or (0,)
    queue_ = [(current, [])]
    seen = {current}
    best = None
    while queue_:
        node, path = queue_.pop(0)
        if len(path) >= max_depth:
            continue
        candidates = list(edges.get(node, [])) + list(edges.get(None, []))
        for dst, final, osu in candidates:
            if dst == target:
                candidate = [*path, (dst, final, osu)]
                if best is None or len(candidate) < len(best):
                    best = candidate
                continue
            nv = norm_version(dst)
            if dst not in seen and nv and nv > (norm_version(node) or current_nv):
                seen.add(dst)
                queue_.append((dst, [*path, (dst, final, osu)]))
    return best


def best_mcu(mcus: list[dict], final: dict, provider: int) -> dict | None:
    allowed = set(final.get("mcu_versions") or [])
    candidates = [
        m
        for m in mcus
        if m.get("id") in allowed
        and provider in (m.get("providers") or [])
        and norm_version(m.get("from_bootloader_version", "")) is not None
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda m: norm_version(m.get("name", "")) or (0,))


# --------------------------------------------------------------------------- transporte HID


class NoDevice(Exception):
    pass


class LedgerHID:
    """Transporte HID mínimo para Ledger (framing canal 0x0101, tag 0x05)."""

    def __init__(self, path: bytes | str):
        self.path = path
        self.dev = hid.device()
        self.dev.open_path(path)
        self.dev.set_nonblocking(False)

    @staticmethod
    def enumerate() -> list[dict]:
        found = []
        for d in hid.enumerate(LEDGER_VID, 0):
            if d.get("interface_number") not in (0, -1):
                continue
            found.append(d)
        # Un Ledger puede exponer más de una interfaz; nos quedamos con una por path.
        unique = {}
        for d in found:
            unique[str(d["path"])] = d
        return list(unique.values())

    def exchange(self, apdu: bytes, timeout_ms: int = 60000) -> tuple[int, bytes]:
        if not apdu:
            raise ValueError("APDU vacío")
        payload = len(apdu).to_bytes(2, "big") + apdu
        offset = 0
        seq = 0
        while offset < len(payload):
            header = b"\x01\x01\x05" + seq.to_bytes(2, "big")
            chunk = header + payload[offset : offset + 59]
            self.dev.write(b"\x00" + chunk)
            offset += 59
            seq += 1

        first = bytes(self.dev.read(65, timeout_ms=timeout_ms))
        if len(first) < 7 or first[0:2] != b"\x01\x01" or first[2] != 0x05:
            raise RuntimeError(f"Respuesta HID inesperada: {first[:8].hex()}")
        if first[3:5] != b"\x00\x00":
            raise RuntimeError("Índice de secuencia inesperado")
        length = int.from_bytes(first[5:7], "big")
        data = first[7:]
        while len(data) < length:
            chunk = bytes(self.dev.read(65, timeout_ms=timeout_ms))
            if not chunk:
                raise RuntimeError("Timeout leyendo del dispositivo")
            data += chunk[5:]
        sw = int.from_bytes(data[length - 2 : length], "big")
        return sw, data[: length - 2]

    def close(self):
        try:
            self.dev.close()
        except Exception:
            pass


def parse_firmware_info(data: bytes) -> dict:
    info = {
        "target_id": None,
        "raw_version": "",
        "flags": b"",
        "is_bootloader": False,
        "se_version": None,
        "se_target_id": None,
        "mcu_version": "",
        "mcu_bl_version": None,
        "mcu_target_id": None,
    }
    if len(data) < 5:
        raise RuntimeError("Respuesta de versión demasiado corta")
    info["target_id"] = int.from_bytes(data[0:4], "big")
    i = 4
    raw_len = data[i]
    i += 1
    if i + raw_len > len(data):
        raise RuntimeError("Respuesta de versión truncada (versión)")
    info["raw_version"] = data[i : i + raw_len].decode(errors="replace")
    i += raw_len
    if i >= len(data):
        raise RuntimeError("Respuesta de versión truncada (flags)")
    flags_len = data[i]
    i += 1
    if i + flags_len > len(data):
        raise RuntimeError("Respuesta de versión truncada (flags)")
    info["flags"] = data[i : i + flags_len]
    i += flags_len

    if raw_len == 0:
        info["raw_version"] = "0.0.0"
        info["flags"] = b""

    target = info["target_id"]
    info["is_bootloader"] = (target & 0xF0000000) != 0x30000000

    if info["is_bootloader"]:
        info["mcu_bl_version"] = info["raw_version"]
        info["mcu_target_id"] = target
        if i < len(data):
            part1_len = data[i]
            i += 1
            if i + part1_len > len(data):
                raise RuntimeError("Respuesta de versión truncada (bootloader)")
            part1 = data[i : i + part1_len]
            i += part1_len
            if part1_len >= 5:
                info["se_version"] = part1.decode(errors="replace")
                if i < len(data):
                    part2_len = data[i]
                    i += 1
                    if i + part2_len > len(data):
                        raise RuntimeError("Respuesta de versión truncada (target)")
                    part2 = data[i : i + part2_len]
                    if len(part2) >= 4:
                        info["se_target_id"] = int.from_bytes(part2[0:4], "big")
            elif len(part1) >= 4:
                info["se_target_id"] = int.from_bytes(part1[0:4], "big")
    else:
        info["se_version"] = info["raw_version"]
        info["se_target_id"] = target
        if i < len(data):
            mcu_len = data[i]
            i += 1
            if i + mcu_len > len(data):
                raise RuntimeError("Respuesta de versión truncada (MCU)")
            mcu = data[i : i + mcu_len]
            i += mcu_len
            info["mcu_version"] = mcu.decode(errors="replace").rstrip("\x00")
    return info


def get_firmware_info(dev: LedgerHID) -> dict:
    sw, data = dev.exchange(b"\xe0\x01\x00\x00\x00")
    if sw != 0x9000:
        if sw == 0x5515:
            raise RuntimeError("El dispositivo está bloqueado. Desbloquéalo con el PIN.")
        raise RuntimeError(f"Error leyendo la versión (SW {sw:04x})")
    info = parse_firmware_info(data)
    if info["se_version"]:
        info["se_version"] = info["se_version"].replace("\x00", "")
    return info


def _ledger_apdu(ins: int, p1: int = 0, data: bytes = b"") -> bytes:
    if data:
        if len(data) > 255:
            raise ValueError("APDU demasiado larga")
        return bytes([0xE0, ins, p1, 0x00, len(data)]) + data
    return bytes([0xE0, ins, p1, 0x00, 0x00])


def _lv(data: bytes, i: int) -> tuple[bytes, int]:
    if i >= len(data):
        raise RuntimeError("Certificado truncado")
    n = data[i]
    i += 1
    if i + n > len(data):
        raise RuntimeError("Certificado truncado")
    return data[i : i + n], i + n


def _sign_der(key, message: bytes) -> bytes:
    return key.sign(message, ec.ECDSA(hashes.SHA256()))


def _verify_der(pubkey: bytes, message: bytes, signature: bytes) -> bool:
    try:
        vk = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256K1(), pubkey)
        vk.verify(signature, message, ec.ECDSA(hashes.SHA256()))
        return True
    except Exception:
        return False


def _certificate(pubkey: bytes, signature: bytes) -> bytes:
    if len(pubkey) > 255 or len(signature) > 255:
        raise ValueError("Certificado demasiado grande")
    return bytes([len(pubkey)]) + pubkey + bytes([len(signature)]) + signature


def offline_attestation(dev: LedgerHID, log) -> dict:
    """Attestation local: verifica el certificado del SE contra la clave de issuer.

    No contacta con ningún servidor. El dispositivo pedirá permitir una conexión
    insegura con nuestra clave temporal; no se completa el canal seguro.
    """
    info = get_firmware_info(dev)
    if info["is_bootloader"]:
        raise RuntimeError("Termina primero la actualización de firmware (modo bootloader)")

    target_id = info.get("se_target_id") or info["target_id"]
    sw, _ = dev.exchange(_ledger_apdu(0x04, 0x00, target_id.to_bytes(4, "big")))
    if sw != 0x9000:
        raise RuntimeError(f"El dispositivo rechazó la identificación (SW {sw:04x})")

    host_nonce = secrets.token_bytes(8)
    sw, auth = dev.exchange(_ledger_apdu(0x50, 0x00, host_nonce))
    if sw != 0x9000:
        raise RuntimeError(f"El dispositivo rechazó el nonce (SW {sw:04x})")
    if len(auth) < 12:
        raise RuntimeError("Respuesta de autenticación demasiado corta")
    signer_serial = auth[:4].hex()
    device_nonce = auth[4:12]

    root = ec.generate_private_key(ec.SECP256K1())
    root_pub = root.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    if manager_allowed(info):
        log("El dispositivo ya tiene el canal seguro permitido en este arranque; puede que no muestre aviso.", "dim")
    else:
        log("Confirma en el dispositivo permitir la conexión insegura para la comprobación offline…", "accent")
    sig = _sign_der(root, b"\x01" + root_pub)
    sw, _ = dev.exchange(_ledger_apdu(0x51, 0x00, _certificate(root_pub, sig)), timeout_ms=120000)
    if sw != 0x9000:
        if sw in (0x6985, 0x5501):
            raise RuntimeError("Permiso denegado en el dispositivo")
        raise RuntimeError(f"El dispositivo rechazó el certificado de host (SW {sw:04x})")
    log("Permiso concedido. Verificando certificados…", "dim")

    eph = ec.generate_private_key(ec.SECP256K1())
    eph_pub = eph.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    sig = _sign_der(root, b"\x11" + host_nonce + device_nonce + eph_pub)
    sw, _ = dev.exchange(_ledger_apdu(0x51, 0x80, _certificate(eph_pub, sig)))
    if sw != 0x9000:
        raise RuntimeError(f"El dispositivo rechazó la clave efímera (SW {sw:04x})")

    sw, cert = dev.exchange(_ledger_apdu(0x52, 0x00))
    if sw != 0x9000:
        raise RuntimeError(f"No se pudo leer el certificado del dispositivo (SW {sw:04x})")
    header, i = _lv(cert, 0)
    device_pub, i = _lv(cert, i)
    cert_sig, i = _lv(cert, i)
    signed = b"\x02" + header + device_pub
    issuer = next(
        (name for name, key in ISSUER_KEYS if _verify_der(bytes.fromhex(key), signed, cert_sig)),
        None,
    )

    sw, eph_cert = dev.exchange(_ledger_apdu(0x52, 0x80))
    if sw != 0x9000:
        raise RuntimeError(f"No se pudo leer el certificado efímero (SW {sw:04x})")
    _h, i = _lv(eph_cert, 0)
    device_eph_pub, i = _lv(eph_cert, i)
    device_eph_sig, i = _lv(eph_cert, i)
    signed_eph = b"\x12" + device_nonce + host_nonce + device_eph_pub
    proof = _verify_der(device_pub, signed_eph, device_eph_sig)

    return {
        "genuine": issuer is not None and proof,
        "issuer": issuer,
        "proof_of_possession": proof,
        "device_public_key": device_pub.hex(),
        "signer_serial": signer_serial,
    }


def offline_genuine_check(path, log) -> bool:
    dev, _info = open_path(path, log)
    try:
        quit_app(dev)
        time.sleep(0.8)
        get_firmware_info(dev)
        log("Verificando el certificado contra la clave de issuer de Ledger (0490f5c9…dad609)…", "dim")
        result = offline_attestation(dev, log)
        if result["genuine"]:
            log(f"Dispositivo genuino (offline) · issuer: {result['issuer']}", "ok")
            log(f"Clave pública del dispositivo: {result['device_public_key']}", "dim")
            log(f"Signer serial: {result['signer_serial']}", "dim")
        elif result["issuer"] is None:
            log(
                "No se pudo verificar el certificado con las claves de issuer conocidas "
                "(posible modelo nuevo). Esto no implica que el dispositivo no sea genuino.",
                "warn",
            )
        else:
            log("Certificado válido, pero falló la prueba de posesión de la clave.", "error")
        return result["genuine"]
    finally:
        try:
            dev.close()
        except Exception:
            pass


def manager_allowed(info: dict) -> bool:
    flags = info.get("flags") or b""
    return bool(flags and (flags[0] & 0x08))


def pin_validated(info: dict) -> bool:
    flags = info.get("flags") or b""
    return bool(flags and (flags[0] & 0x80))


def open_first(log=None) -> tuple[LedgerHID, dict]:
    devices = LedgerHID.enumerate()
    if not devices:
        raise NoDevice("No hay ningún Ledger conectado")
    errors = []
    for d in devices:
        try:
            dev = LedgerHID(d["path"])
            info = get_firmware_info(dev)
            return dev, info
        except Exception as exc:  # probamos la siguiente interfaz
            errors.append(str(exc))
    message = "Ledger encontrado pero no responde: " + "; ".join(errors)
    if any("permission" in e.lower() or "access" in e.lower() for e in errors):
        message += " · Revisa las reglas udev de Ledger (99-ledger.rules) o los permisos USB."
    raise NoDevice(message)


def open_path(path, log=None) -> tuple[LedgerHID, dict]:
    if path:
        try:
            dev = LedgerHID(path)
        except Exception as exc:
            try:
                count = len(LedgerHID.enumerate())
            except Exception:
                count = 0
            if count == 1:
                if log:
                    log(
                        f"No se pudo abrir el dispositivo seleccionado ({exc}); "
                        "usando el único dispositivo conectado.",
                        "warn",
                    )
            elif count == 0:
                raise RuntimeError("No hay ningún Ledger conectado") from exc
            else:
                raise RuntimeError(
                    f"No se pudo abrir el dispositivo seleccionado y hay {count} "
                    "conectados; vuelve a seleccionarlo."
                ) from exc
        try:
            return dev, get_firmware_info(dev)
        except Exception:
            dev.close()
            raise
    return open_first(log)


def parse_list_apps(data: bytes) -> list[dict]:
    """Parsea una página de LIST_APPS.

    Formato de cada entrada (el byte de longitud se cuenta a sí mismo):
    len, blocks(2), flags(2), hash_code_data(32), hash(32), name_len(1), name.
    """
    if not data:
        return []
    if data[0] != 1:
        raise RuntimeError("Formato de LIST_APPS desconocido")
    apps = []
    i = 1
    while i < len(data):
        entry_len = data[i]
        i += 1
        # bloques + flags + dos hashes + longitud del nombre = 69 bytes
        if i + 69 > len(data):
            raise RuntimeError("Entrada de LIST_APPS truncada")
        blocks = int.from_bytes(data[i : i + 2], "big")
        flags = int.from_bytes(data[i + 2 : i + 4], "big")
        hash_code_data = data[i + 4 : i + 36].hex()
        app_hash = data[i + 36 : i + 68].hex()
        name_len = data[i + 68]
        i += 69
        if i + name_len > len(data):
            raise RuntimeError("Entrada de LIST_APPS truncada")
        name = data[i : i + name_len].decode(errors="replace").strip()
        i += name_len
        apps.append(
            {
                "name": name,
                "blocks": blocks,
                "flags": flags,
                "hash_code_data": hash_code_data,
                "hash": app_hash,
            }
        )
        # entry_len incluye el byte de longitud; lo usamos solo si cuadra.
        _ = entry_len
    return apps


def list_installed_apps(dev: LedgerHID) -> list[dict]:
    apps = []
    sw, data = dev.exchange(LIST_APPS)
    while True:
        if sw != 0x9000:
            if sw in (0x6985, 0x6d00):
                raise RuntimeError("El dispositivo no permite listar las apps (¿bloqueado?)")
            raise RuntimeError(f"LIST_APPS falló (SW {sw:04x})")
        if not data:
            return apps
        apps.extend(parse_list_apps(data))
        sw, data = dev.exchange(LIST_APPS_CONTINUE)


def parse_language_pack(data: bytes) -> dict | None:
    """Parsea una respuesta de LIST_LANGUAGE_PACKS (mismo formato que LSdk)."""
    if not data:
        return None
    i = 0
    i += 1  # versión del formato
    i += 1  # longitud
    id_len = data[i]
    i += 1
    lang_id = int.from_bytes(data[i : i + id_len], "big")
    i += id_len
    size_len = data[i]
    i += 1
    size = int.from_bytes(data[i : i + size_len], "big")
    return {"id": lang_id, "size": size}


def list_language_packs(dev: LedgerHID) -> list[dict]:
    packs = []
    sw, data = dev.exchange(LIST_LANG_FIRST)
    while True:
        if sw in (0x6D00, 0x6E00):
            return packs  # firmware sin soporte de packs
        if sw != 0x9000:
            if sw in (0x6985, 0x5501):
                raise RuntimeError("El dispositivo no permite leer los packs de idioma")
            raise RuntimeError(f"LIST_LANGUAGE_PACKS falló (SW {sw:04x})")
        pack = parse_language_pack(data)
        if pack is None:
            return packs
        packs.append(pack)
        if len(packs) > 16:
            return packs
        sw, data = dev.exchange(LIST_LANG_NEXT)


def delete_language_pack(dev: LedgerHID, lang_id: int):
    sw, _ = dev.exchange(bytes([0xE0, 0x33, lang_id & 0xFF, 0x00, 0x00]), timeout_ms=120000)
    if sw not in (0x9000, 0x6985, 0x5501):
        raise RuntimeError(f"Borrando pack de idioma falló (SW {sw:04x})")


def fetch_text(url: str) -> str:
    if OFFLINE_ONLY:
        raise RuntimeError("Modo offline: no se pueden descargar packs de idioma")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != LANGUAGE_HOST:
        raise RuntimeError(f"Origen de pack de idioma no permitido: {url}")
    data = _https_request(url, headers={"User-Agent": USER_AGENT}, timeout=60)
    return data.decode()


def parse_apdu_script(script: str) -> list[bytes]:
    apdus = []
    for line in script.splitlines():
        line = line.split("#", 1)[0].strip().replace(" ", "")
        if line:
            apdus.append(bytes.fromhex(line))
    if not apdus:
        raise RuntimeError("El script APDU del pack de idioma está vacío")
    return apdus


def run_apdu_script(dev: LedgerHID, script, log, progress):
    apdus = parse_apdu_script(script) if isinstance(script, str) else list(script)
    total = len(apdus)
    for i, apdu in enumerate(apdus, 1):
        sw, _ = dev.exchange(apdu, timeout_ms=120000)
        if sw == 0x9000:
            pass
        elif sw in (0x6985, 0x5501):
            raise RuntimeError("Instalación del pack cancelada en el dispositivo")
        elif sw in (0x6A84, 0x5102):
            raise RuntimeError("No hay espacio para el pack de idioma")
        else:
            raise RuntimeError(f"APDU {i}/{total} del pack falló (SW {sw:04x})")
        progress(i, total)


def language_catalog(device_version_id: int, final_firmware_id: int) -> list[dict]:
    packs = api_get(
        "/language-packages",
        device_version=device_version_id,
        current_se_firmware_final_version=final_firmware_id,
    )
    return packs if isinstance(packs, list) else []


def install_language_pack(dev, device_version_id, final_firmware_id, lang_id, log, progress):
    name = LANGUAGE_NAMES.get(lang_id)
    if not name:
        log(f"Idioma {lang_id} desconocido; no se reinstala.", "warn")
        return
    catalog = language_catalog(device_version_id, final_firmware_id)
    aliases = {name}
    if name == "brazilian":
        aliases.add("portuguese")
    candidates = [p for p in catalog if (p.get("language") or "").lower() in aliases]
    if not candidates:
        log(f"El pack de idioma «{name}» no está disponible para este firmware.", "warn")
        return
    pack = candidates[0]
    log(f"Reinstalando pack de idioma {name} {pack.get('version', '')}…", "accent")
    script = fetch_text(pack["apdu_install_url"])
    apdus = parse_apdu_script(script)
    for installed in list_language_packs(dev):
        try:
            delete_language_pack(dev, installed["id"])
        except Exception as exc:
            log(f"No se pudo borrar el pack anterior: {exc}", "warn")
    run_apdu_script(dev, apdus, log, progress)
    log(f"Pack de idioma {name} instalado.", "ok")


def app_catalog(target_id: int, firmware_version: str, provider: int = 1) -> list[dict]:
    catalog = api_get(
        "/v2/apps/by-target",
        target_id=target_id,
        provider=provider,
        firmware_version_name=firmware_version,
    )
    return catalog if isinstance(catalog, list) else []


def apps_by_hash(hashes: list[str]) -> list[dict | None]:
    if not hashes:
        return []
    result = http_post_json(MANAGER_API + "/v2/apps/hash", hashes)
    return result if isinstance(result, list) else []


def install_app_hsm(dev, target_id, app, log, progress):
    if not app.get("firmware") or not app.get("firmwareKey"):
        raise RuntimeError(f"La app {app.get('versionName')} no tiene binario en el catálogo")
    log(f"Instalando app {app['versionName']} {app.get('version', '')}…", "accent")
    run_socket(
        dev,
        "install",
        {
            "targetId": target_id,
            "perso": app.get("perso") or "perso_11",
            "deleteKey": app.get("deleteKey") or "",
            "firmware": app.get("firmware"),
            "firmwareKey": app.get("firmwareKey"),
            "hash": app.get("hash") or "",
        },
        log,
        progress,
    )


def uninstall_app_hsm(dev, target_id, app, log, progress):
    if not app.get("delete") or not app.get("deleteKey"):
        raise RuntimeError(f"La app {app.get('versionName')} no tiene datos de desinstalación")
    log(f"Desinstalando app {app['versionName']}…", "accent")
    run_socket(
        dev,
        "install",
        {
            "targetId": target_id,
            "perso": app.get("perso") or "perso_11",
            "deleteKey": app.get("deleteKey") or "",
            "firmware": app.get("delete"),
            "firmwareKey": app.get("deleteKey") or "",
            "hash": app.get("hash") or "",
        },
        log,
        progress,
    )


def install_app_entry(dev, target_id, catalog, name, installed_names, log, progress):
    """Instala una app del catálogo, con su app padre si hace falta."""
    by_name = {a["versionName"].strip().lower(): a for a in catalog if a.get("versionName")}
    app = by_name.get(name.strip().lower())
    if not app:
        raise RuntimeError(f"La app «{name}» no está en el catálogo de este firmware")
    installed = {n.strip().lower() for n in installed_names}
    parent = app.get("parentName")
    if parent and parent.strip().lower() not in installed:
        parent_app = by_name.get(parent.strip().lower())
        if parent_app:
            log(f"Instalando dependencia {parent_app['versionName']}…", "dim")
            install_app_hsm(dev, target_id, parent_app, log, progress)
    install_app_hsm(dev, target_id, app, log, progress)


def restore_apps(dev, target_id, firmware_version, provider, names, log, progress):
    """Reinstala las apps que había antes del update, con el catálogo del firmware nuevo."""
    catalog = app_catalog(target_id, firmware_version, provider)
    if not catalog:
        log("No hay catálogo de apps para este firmware; no se reinstala nada.", "warn")
        return
    by_name = {a["versionName"].strip().lower(): a for a in catalog if a.get("versionName")}
    order = []
    seen = set()

    def add(name, depth=0):
        key = name.lower()
        if key in seen or depth > 4:
            return
        app = by_name.get(key)
        if not app:
            log(f"La app «{name}» ya no está disponible para este firmware.", "warn")
            return
        parent = app.get("parentName")
        if parent:
            add(parent, depth + 1)
        seen.add(key)
        order.append(app)

    for name in names:
        add(name)

    ok, failed = [], []
    for i, app in enumerate(order, 1):
        try:
            install_app_hsm(dev, target_id, app, log, lambda c, t, i=i: progress(i - 1 + c / max(t, 1), len(order)))
            ok.append(app["versionName"])
        except Exception as exc:
            failed.append((app["versionName"], str(exc)))
            log(f"No se pudo instalar {app['versionName']}: {exc}", "warn")
    progress(len(order), len(order))
    if ok:
        log("Apps reinstaladas: " + ", ".join(ok), "ok")
    if failed:
        log("Apps que fallaron: " + ", ".join(n for n, _ in failed), "warn")
    return ok, failed


def wait_for_device(timeout: float = 90.0, log=None) -> tuple[LedgerHID, dict]:
    deadline = time.time() + timeout
    last = None
    warned = set()
    while time.time() < deadline:
        try:
            return open_first(log)
        except Exception as exc:
            last = exc
            text = str(exc).lower()
            if log and "bloqueado" in text and "lock" not in warned:
                warned.add("lock")
                log("Desbloquea el dispositivo con el PIN para continuar…", "warn")
            elif log and "no hay ningún" in text and "gone" not in warned:
                warned.add("gone")
                log("Esperando a que el dispositivo vuelva a aparecer…", "dim")
            time.sleep(0.7)
    raise NoDevice(f"El dispositivo no ha vuelto a aparecer ({last})")


# --------------------------------------------------------------------------- socket Ledger


class SocketError(RuntimeError):
    pass


def run_socket(dev: LedgerHID, kind: str, params: dict, log, progress, timeout: float = 180.0) -> str | None:
    """Ejecuta un flujo del HSM de Ledger (install / mcu / genuine)."""
    params = dict(params)
    params.setdefault("livecommonversion", LIVE_COMMON_VERSION)
    url = f"{SOCKET_BASE}/{kind}?" + urllib.parse.urlencode(params)
    if OFFLINE_ONLY:
        raise SocketError("Modo offline: esta acción necesita conexión con Ledger")
    if not url.startswith(SOCKET_BASE + "/"):
        raise SocketError(f"Destino WebSocket no permitido: {url}")
    log(f"Conectando con el servicio de Ledger ({kind})…", "dim")
    ws = websocket.create_connection(
        url,
        timeout=timeout,
        sslopt={"context": _tls_context()},
    )
    sock = getattr(ws, "sock", None)
    if sock is not None and hasattr(sock, "getpeercert"):
        _check_spki_pin(sock.getpeercert(binary_form=True))
    result = None
    try:
        while True:
            raw = ws.recv()
            if not raw:
                continue
            msg = json.loads(raw)
            query = msg.get("query")
            if query == "exchange":
                apdu = bytes.fromhex(msg.get("data") or "")
                sw, data = dev.exchange(apdu, timeout_ms=120000)
                response = "success" if sw == 0x9000 else "error"
                ws.send(json.dumps({"nonce": msg.get("nonce"), "response": response, "data": data.hex()}))
            elif query == "bulk":
                apdus = [bytes.fromhex(x) for x in (msg.get("data") or []) if x]
                total = len(apdus)
                log(f"Transfiriendo {total} bloques…", "accent")
                ws.close()
                for i, apdu in enumerate(apdus, 1):
                    sw, _ = dev.exchange(apdu, timeout_ms=120000)
                    if sw != 0x9000:
                        raise SocketError(
                            f"El dispositivo rechazó el bloque {i}/{total} (SW {sw:04x})"
                        )
                    progress(i, total)
                return None
            elif query == "success":
                result = msg.get("result") or msg.get("data")
                return result
            elif query == "error":
                raise SocketError(str(msg.get("data") or "error del servicio de Ledger"))
            elif query == "warning":
                log(f"Aviso del servicio: {msg.get('data')}", "warn")
            else:
                log(f"Mensaje no soportado del servicio: {query}", "warn")
    finally:
        try:
            ws.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- lógica de update


def quit_app(dev: LedgerHID):
    try:
        dev.exchange(b"\xb0\xa7\x00\x00\x00")
    except Exception:
        pass


def current_mcu_id(mcus: list[dict], info: dict) -> int | None:
    name = (info.get("mcu_version") or "").strip()
    for m in mcus:
        if m.get("name") == name:
            return m["id"]
    nv = norm_version(name)
    if nv:
        for m in mcus:
            if norm_version(m.get("name", "")) == nv:
                return m["id"]
    return None


def install_osu(dev, target_id, final, osu, log, progress):
    if not osu.get("firmware") or not osu.get("firmware_key"):
        raise RuntimeError(f"El OSU {osu.get('name')} no tiene binario publicado")
    log(f"Instalando OSU {osu.get('name')}…", "accent")
    run_socket(
        dev,
        "install",
        {
            "targetId": target_id,
            "perso": osu.get("perso") or final.get("perso") or "perso_11",
            "firmware": osu.get("firmware"),
            "firmwareKey": osu.get("firmware_key"),
        },
        log,
        progress,
    )


def flash_mcu_loop(dev, final, mcus, provider, log, progress):
    """Tras instalar el OSU el dispositivo puede quedar en bootloader: flashea MCU/bootloader."""
    for _ in range(6):
        info = get_firmware_info(dev)
        if not info["is_bootloader"]:
            return dev, info
        mcu = best_mcu(mcus, final, provider)
        if not mcu:
            raise RuntimeError("Ledger no publica un MCU compatible con este firmware")
        current_bl = clean_version(info.get("mcu_bl_version") or info.get("raw_version") or "")
        target_bl = clean_version(mcu.get("from_bootloader_version") or "")
        target = mcu["name"] if current_bl == target_bl else mcu.get("from_bootloader_version")
        log(f"Flasheando {'MCU' if current_bl == target_bl else 'bootloader'} {target}…", "accent")
        run_socket(
            dev,
            "mcu",
            {"targetId": info["mcu_target_id"] or info["target_id"], "version": target},
            log,
            progress,
        )
        dev.close()
        dev, _ = wait_for_device(log=log)
    raise RuntimeError("Demasiados reintentos flasheando MCU/bootloader")


def direct_osu(finals: list[dict], device_version_id: int, target: str):
    """OSU para reinstalar en modo bootloader, donde no conocemos la versión de origen."""
    generic = None
    fallback = None
    for final in finals:
        if device_version_id not in (final.get("device_versions") or []):
            continue
        if final.get("version") != target:
            continue
        for osu in final.get("osu_versions") or []:
            name = osu.get("name") or ""
            if "hidden" in name:
                continue
            if name == f"{target}-osu":
                generic = (target, final, osu)
            elif fallback is None and ("-to-" in name or name.endswith("-osu")):
                fallback = (target, final, osu)
    chosen = generic or fallback
    return [chosen] if chosen else None


def update_plan(path, log) -> dict:
    """Lee el dispositivo y calcula qué firmware se instalaría, sin flashear."""
    dev, info = open_path(path, log)
    try:
        quit_app(dev)
        time.sleep(0.8)
        info = get_firmware_info(dev)
    finally:
        dev.close()
    target_id = info.get("se_target_id") or info.get("target_id")
    provider = provider_from_version(info.get("se_version") or info.get("raw_version") or "")
    dv = api_get("/get_device_version", provider=provider, target_id=target_id)
    finals = fetch_all_finals()
    latest = latest_final(finals, dv["id"])
    current = clean_version(info.get("se_version") or "")
    if (
        latest
        and current
        and not info.get("is_bootloader")
        and (norm_version(latest["version"]) or (0,)) < (norm_version(current) or (0,))
    ):
        raise RuntimeError(
            "La API ofrece una versión anterior a la instalada. "
            "Posible manipulación de la red o error de Ledger; no se actualizará. "
            "Usa Ledger Live o revisa la conexión."
        )
    route = None
    if latest and not info.get("is_bootloader"):
        route = find_update_path(build_edges(finals, dv["id"]), current, latest["version"])
    return {
        "info": info,
        "target_id": target_id,
        "provider": provider,
        "dv": dv,
        "latest": latest,
        "current": current,
        "route": route,
    }


def notes_for_final(final: dict) -> str:
    """Notas de la versión: el final no siempre las trae, pero los OSU sí."""
    if not final:
        return ""
    notes = (final.get("notes") or "").strip()
    if notes:
        return notes
    version = final.get("version", "")
    candidates = []
    for osu in final.get("osu_versions") or []:
        name = osu.get("name") or ""
        if "hidden" in name:
            continue
        text = (osu.get("notes") or "").strip()
        if not text:
            continue
        # Preferimos la variante genérica "X-osu" y luego las "X-to-Y".
        score = 0
        if name == f"{version}-osu":
            score = 2
        elif name.endswith("-osu"):
            score = 1
        candidates.append((score, text))
    if not candidates:
        return ""
    return max(candidates, key=lambda c: c[0])[1]


def changelog_text(plan: dict) -> str:
    info = plan["info"]
    latest = plan["latest"]
    current = plan["current"]
    target_id = plan["target_id"]
    model = model_for_target(target_id) or "Ledger"
    lines = [f"{model}", f"Firmware actual: {current or 'bootloader'}", ""]
    if not latest:
        return "\n".join([*lines, "No hay firmware publicado para este modelo."])
    lines.append(f"Firmware disponible: {latest['version']}")
    if info.get("is_bootloader"):
        lines.append("El dispositivo está en modo bootloader; se reanudará la actualización.")
        notes = notes_for_final(latest)
        if notes:
            lines += ["", notes]
        return "\n".join(lines)
    if current == latest["version"]:
        lines += ["", f"Ya tienes la última versión instalada ({current})."]
        notes = notes_for_final(latest)
        if notes:
            lines += ["", "=" * 60, f"Novedades de la versión {current}", "=" * 60, notes]
        else:
            lines += ["", "(Ledger no publica notas para esta versión.)"]
        return "\n".join(lines)
    route = plan.get("route")
    if not route:
        lines += ["", "No se encontró una ruta OSU publicada para actualizar."]
        return "\n".join(lines)
    lines.append("Ruta: " + " → ".join([current] + [step[0] for step in route]))
    for target, final, osu in route:
        notes = (osu.get("notes") or final.get("notes") or "").strip()
        lines += ["", "=" * 60, f"Versión {target}", "=" * 60, notes or "(sin notas publicadas)"]
    return "\n".join(lines)


def update_device(path, log, progress, force=False, restore=True, restore_language=True) -> dict:
    dev, info = open_path(path, log)
    try:
        quit_app(dev)
        time.sleep(1.0)
        try:
            info = get_firmware_info(dev)
        except Exception:
            dev.close()
            dev, info = wait_for_device(log=log)

        target_id = info.get("se_target_id") or info.get("target_id")
        model = model_for_target(target_id) or model_for_target(info.get("target_id") or 0) or "Ledger"
        provider = provider_from_version(info.get("se_version") or info.get("raw_version") or "")
        log(f"{model} · firmware {info.get('se_version') or 'bootloader ' + str(info.get('mcu_bl_version'))}", "bold")

        backup_apps = []
        if restore and not info.get("is_bootloader"):
            try:
                log("Leyendo apps instaladas (confirma en el dispositivo si lo pide)…", "dim")
                backup_apps = list_installed_apps(dev)
                names = ", ".join(a["name"] for a in backup_apps) or "ninguna"
                log(f"Apps instaladas antes del update: {names}", "dim")
            except Exception as exc:
                log(f"No se pudo leer la lista de apps ({exc}); no se restaurarán.", "warn")

        backup_langs = []
        if restore_language and not info.get("is_bootloader"):
            try:
                backup_langs = list_language_packs(dev)
                if backup_langs:
                    log("Pack de idioma antes del update: " + ", ".join(LANGUAGE_NAMES.get(p["id"], str(p["id"])) for p in backup_langs), "dim")
            except Exception as exc:
                log(f"No se pudo leer el pack de idioma ({exc}); no se restaurará.", "warn")

        dv = api_get("/get_device_version", provider=provider, target_id=target_id)
        device_version_id = dv["id"]
        finals = fetch_all_finals()
        latest = latest_final(finals, device_version_id)
        if not latest:
            raise RuntimeError("No hay firmware publicado para este modelo")
        mcus = api_get("/mcu_versions")

        current = clean_version(info.get("se_version") or "")
        if info.get("is_bootloader"):
            log("El dispositivo está en modo bootloader; se intentará reanudar la actualización.", "warn")
            route = direct_osu(finals, device_version_id, latest["version"])
            if route:
                try:
                    for _target, final, osu in route:
                        install_osu(dev, target_id, final, osu, log, progress)
                        dev.close()
                        dev, _ = wait_for_device(log=log)
                        dev, info = flash_mcu_loop(dev, final, mcus, provider, log, progress)
                    return info
                except Exception as exc:
                    log(f"No se pudo reinstalar el OSU en bootloader ({exc}); se intentará flashear MCU.", "warn")
                    try:
                        dev.close()
                    except Exception:
                        pass
                    dev, _ = wait_for_device(log=log)
            dev, info = flash_mcu_loop(dev, latest, mcus, provider, log, progress)
            return info

        log(f"Última versión estable: {latest['version']}", "dim")
        if current == latest["version"] and not force:
            log("Ya tienes la última versión. No hay nada que hacer.", "ok")
            return info

        edges = build_edges(finals, device_version_id)
        route = find_update_path(edges, current, latest["version"])
        if route is None:
            raise RuntimeError(
                f"No encuentro una ruta OSU de {current} a {latest['version']}. "
                "Puede que necesites una versión intermedia o Ledger Wallet."
            )
        log("Ruta: " + " → ".join([current] + [step[0] for step in route]), "dim")
        for target_version, _final, osu in route:
            notes = (osu.get("notes") or "").strip()
            if not notes:
                continue
            log(f"Novedades {target_version}:", "bold")
            note_lines = notes.splitlines()
            for line in note_lines[:12]:
                log("  " + line, "dim")
            if len(note_lines) > 12:
                log("  … (texto completo en «Novedades…»)", "dim")

        for _target_version, final, osu in route:
            install_osu(dev, target_id, final, osu, log, progress)
            dev.close()
            dev, _ = wait_for_device(log=log)
            dev, info = flash_mcu_loop(dev, final, mcus, provider, log, progress)
            if not info["is_bootloader"]:
                log(f"Ahora: firmware {info.get('se_version')}", "ok")

        info = get_firmware_info(dev)
        final_version = clean_version(info.get("se_version") or "")
        if final_version != latest["version"]:
            log(
                f"La versión final es {info.get('se_version')} (esperaba {latest['version']}).",
                "warn",
            )
        else:
            log(f"Actualización completada: firmware {info.get('se_version')}.", "ok")

        if backup_langs:
            for pack in backup_langs:
                if pack["id"] == 0:
                    continue  # inglés es el idioma base, no hace falta pack
                try:
                    install_language_pack(
                        dev,
                        device_version_id,
                        latest["id"],
                        pack["id"],
                        log,
                        progress,
                    )
                except Exception as exc:
                    log(f"No se pudo reinstalar el pack de idioma: {exc}", "warn")

        if backup_apps:
            log(f"Restaurando {len(backup_apps)} apps con el catálogo de {final_version}…", "bold")
            try:
                restore_apps(
                    dev,
                    target_id,
                    final_version,
                    provider,
                    [a["name"] for a in backup_apps],
                    log,
                    progress,
                )
            except Exception as exc:
                log(f"No se pudieron reinstalar las apps: {exc}", "warn")
        return info
    finally:
        try:
            dev.close()
        except Exception:
            pass


def genuine_check(path, log, progress) -> bool:
    dev, info = open_path(path, log)
    try:
        target_id = info.get("se_target_id") or info.get("target_id")
        dv = api_get("/get_device_version", provider=1, target_id=target_id)
        perso = dv.get("perso") or "perso_11"
        if manager_allowed(info):
            log("El dispositivo ya autorizó el canal seguro en este arranque; no volverá a pedir confirmación. Reinicia el Ledger si quieres ver el aviso de nuevo.", "dim")
        else:
            log("Confirma en el dispositivo: permitir el canal seguro.", "accent")
        log("Conectando con el servicio de genuinidad de Ledger…", "dim")
        result = run_socket(
            dev,
            "genuine",
            {"targetId": target_id, "perso": perso},
            log,
            progress,
        )
        ok = str(result).lower() in ("0000", "true", "1")
        log("Servicio de genuinidad de Ledger: OK (la prueba criptográfica es la atestación offline)." if ok else f"Resultado inesperado: {result}", "ok" if ok else "warn")
        return ok
    finally:
        try:
            dev.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- selftest


def selftest() -> int:
    print("Consultando la API pública de Ledger…")
    finals = fetch_all_finals()
    print(f"Firmwares publicados: {len(finals)}")
    ok = True
    for target_id, name in MODELS.items():
        try:
            dv = api_get("/get_device_version", provider=1, target_id=target_id)
            latest = latest_final(finals, dv["id"])
            if not latest:
                print(f"  {name:18} sin firmware")
                ok = False
                continue
            osus = latest.get("osu_versions") or []
            apps = app_catalog(target_id, latest["version"], 1)
            edges = build_edges(finals, dv["id"])
            sources = [s for s in edges if s and (norm_version(s) or (0,)) < (norm_version(latest["version"]) or (0,))]
            if sources:
                previous = max(sources, key=lambda s: norm_version(s) or (0,))
            else:
                stables = sorted(
                    (
                        f["version"]
                        for f in finals
                        if dv["id"] in (f.get("device_versions") or [])
                        and is_stable(f.get("version", ""))
                    ),
                    key=lambda v: norm_version(v) or (0,),
                )
                previous = stables[0] if stables else None
            route = find_update_path(edges, previous, latest["version"]) if previous else None
            route_txt = " → ".join([previous] + [s[0] for s in route]) if route else "sin ruta directa"
            print(
                f"  {name:18} dv={dv['id']:<4} última={latest['version']:<8} "
                f"OSU={len(osus):<3} apps={len(apps):<4} ruta={route_txt}"
            )
            if previous and route is None:
                ok = False
        except Exception as exc:
            ok = False
            print(f"  {name:18} ERROR: {exc}")
    if not ok:
        print(
            "SELFTEST CON ERRORES: la API de Ledger puede haber cambiado. "
            "Revisa docs/LEDGER-API.md."
        )
        return 1
    print("SELFTEST OK")
    return 0


# --------------------------------------------------------------------------- GUI


try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    HAVE_TK = True
except ImportError:
    HAVE_TK = False


class App:
    ORANGE = "#f8b21c"

    def __init__(self, root):
        self.root = root
        self.root.title("Ledger Firmware Updater")
        self.root.geometry("900x680")
        try:
            self._icon = tk.PhotoImage(
                file=os.path.expanduser("~/.local/share/icons/hicolor/256x256/apps/ledger-updater.png")
            )
            self.root.iconphoto(True, self._icon)
        except Exception:
            pass
        self.queue = queue.Queue()
        self.device = None
        self.busy = False
        self.path = None

        self.setup_style()
        self.build()

        self.root.after(60, self.drain)
        self.root.after(300, self.start_detect)

    # -- estética -----------------------------------------------------------
    def setup_style(self):
        bg, panel, border = "#1c1c1c", "#2a2a2a", "#3d3d3d"
        fg, dim = "#d8d8d8", "#9a9a9a"
        self.root.configure(bg=bg)
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(
            ".",
            background=bg,
            foreground=fg,
            bordercolor=border,
            lightcolor=panel,
            darkcolor=panel,
            troughcolor="#111111",
            focuscolor=self.ORANGE,
            selectbackground="#4a4a4a",
            selectforeground="#ffffff",
            fieldbackground=panel,
        )
        style.configure("TFrame", background=bg)
        style.configure("TLabelframe", background=bg, bordercolor=border, relief="solid")
        style.configure(
            "TLabelframe.Label",
            background=bg,
            foreground=dim,
            font=("TkDefaultFont", 9, "bold"),
        )
        style.configure("TLabel", background=bg, foreground=fg)
        style.configure("Title.TLabel", foreground=self.ORANGE)
        style.configure("Sub.TLabel", background=bg, foreground=dim)
        style.configure("Subtitle.TLabel", background=bg, foreground=dim)
        style.configure("Accent.TLabel", background=bg, foreground=self.ORANGE)
        style.configure("Footer.TLabel", background=bg, foreground="#6f6f6f")
        style.configure(
            "TButton",
            background="#333333",
            foreground="#e0e0e0",
            bordercolor=border,
            focusthickness=1,
            padding=(12, 7),
        )
        style.map(
            "TButton",
            background=[("active", "#454545"), ("disabled", "#262626")],
            foreground=[("disabled", "#666666")],
            bordercolor=[("focus", self.ORANGE)],
        )
        style.configure(
            "Primary.TButton",
            background=self.ORANGE,
            foreground="#161616",
            font=("TkDefaultFont", 10, "bold"),
            bordercolor="#c78d0f",
        )
        style.map(
            "Primary.TButton",
            background=[("active", "#ffcb57"), ("disabled", "#5a4a1e")],
            foreground=[("disabled", "#8a7a4a")],
        )
        style.configure("Accent.TButton", background=self.ORANGE, foreground="#161616")
        style.configure(
            "Danger.TButton",
            background="#a03030",
            foreground="#f5d7d7",
            font=("TkDefaultFont", 9, "bold"),
            bordercolor="#7a2222",
        )
        style.map(
            "Danger.TButton",
            background=[("active", "#c0392b"), ("disabled", "#3d2020")],
            foreground=[("disabled", "#7a5a5a")],
        )
        style.configure(
            "TCombobox",
            fieldbackground="#2e2e2e",
            background="#333333",
            foreground="#e0e0e0",
            arrowcolor=dim,
            bordercolor=border,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", "#2e2e2e")],
            foreground=[("readonly", "#e0e0e0")],
        )
        style.configure(
            "TEntry",
            fieldbackground="#2e2e2e",
            foreground="#e0e0e0",
            insertcolor="#e0e0e0",
            bordercolor=border,
        )
        style.configure(
            "Treeview",
            background="#1c1c1c",
            fieldbackground="#1c1c1c",
            foreground="#d8d8d8",
            bordercolor=border,
            rowheight=24,
            font=("TkDefaultFont", 9),
        )
        style.map(
            "Treeview",
            background=[("selected", "#4a4a4a")],
            foreground=[("selected", "#ffffff")],
        )
        style.configure(
            "Treeview.Heading",
            background="#2a2a2a",
            foreground="#d8d8d8",
            bordercolor=border,
            relief="flat",
            font=("TkDefaultFont", 9, "bold"),
        )
        style.map(
            "Treeview.Heading",
            background=[("active", "#3a3a3a")],
            foreground=[("active", "#ffffff")],
        )
        self.root.option_add("*TCombobox*Listbox.background", "#2e2e2e")
        self.root.option_add("*TCombobox*Listbox.foreground", "#e0e0e0")
        self.root.option_add("*TCombobox*Listbox.selectBackground", self.ORANGE)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#161616")
        style.configure(
            "Sparrow.Horizontal.TProgressbar",
            background=self.ORANGE,
            troughcolor="#111111",
            bordercolor=border,
            lightcolor=self.ORANGE,
            darkcolor=self.ORANGE,
            thickness=10,
        )
        for s in ("TScrollbar", "Vertical.TScrollbar"):
            style.configure(
                s,
                background="#2a2a2a",
                troughcolor="#141414",
                bordercolor=border,
                arrowcolor=dim,
                relief="flat",
            )
        self.log_text = None

    def _mono_font(self):
        try:
            import tkinter.font as tkfont

            families = set(tkfont.families())
            for name in ("DejaVu Sans Mono", "Liberation Mono", "Consolas", "Menlo"):
                if name in families:
                    return (name, 9)
        except Exception:
            pass
        return ("TkFixedFont", 9)

    def build(self):
        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill="both", expand=True)

        ttk.Label(
            outer,
            text="Ledger Firmware Updater",
            font=("TkDefaultFont", 16, "bold"),
            style="Title.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            outer,
            text="Firmware y genuinidad sin Ledger Wallet · el flasheo lo orquesta el HSM de Ledger",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(4, 12))

        devbox = ttk.LabelFrame(outer, text=" DISPOSITIVO ", padding=12)
        devbox.pack(fill="x")
        row = ttk.Frame(devbox)
        row.pack(fill="x")
        self.dev_combo = ttk.Combobox(row, state="readonly", width=42)
        self.dev_combo.pack(side="left", fill="x", expand=True)
        self.dev_combo.bind("<<ComboboxSelected>>", lambda _e: self.select_device())
        ttk.Button(row, text="Buscar", command=self.start_detect).pack(side="left", padx=(8, 0))
        self.dev_label = ttk.Label(devbox, text="Buscando dispositivos…", style="Subtitle.TLabel")
        self.dev_label.pack(anchor="w", pady=(8, 0))

        fw = ttk.LabelFrame(outer, text=" FIRMWARE ", padding=12)
        fw.pack(fill="x", pady=(10, 0))
        self.fw_label = ttk.Label(fw, text="Conecta tu Ledger para consultar la versión.")
        self.fw_label.pack(anchor="w")
        row2 = ttk.Frame(fw)
        row2.pack(fill="x", pady=(10, 0))
        self.btn_update = ttk.Button(
            row2, text="Actualizar firmware", style="Primary.TButton", command=self.start_update
        )
        self.btn_update.pack(side="left")
        self.btn_changelog = ttk.Button(row2, text="Novedades…", command=self.open_changelog)
        self.btn_changelog.pack(side="left", padx=(8, 0))

        row3 = ttk.Frame(fw)
        row3.pack(fill="x", pady=(6, 0))
        self.btn_genuine = ttk.Button(row3, text="Verificar genuinidad", command=self.start_genuine)
        self.btn_genuine.pack(side="left")
        self.btn_genuine_off = ttk.Button(row3, text="Verificar offline", command=self.start_genuine_offline)
        self.btn_genuine_off.pack(side="left", padx=(8, 0))
        self.btn_apps = ttk.Button(row3, text="Gestionar apps…", command=self.open_apps)
        self.btn_apps.pack(side="left", padx=(8, 0))
        self.btn_lang = ttk.Button(row3, text="Idioma…", command=self.open_language)
        self.btn_lang.pack(side="left", padx=(8, 0))
        self.btn_list_apps = ttk.Button(row3, text="Apps instaladas", command=self.start_list_apps)
        self.btn_list_apps.pack(side="left", padx=(8, 0))

        row4 = ttk.Frame(fw)
        row4.pack(fill="x", pady=(8, 0))
        self.restore_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            row4,
            text="Reinstalar apps",
            variable=self.restore_var,
        ).pack(side="left")
        self.lang_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            row4,
            text="Conservar idioma",
            variable=self.lang_var,
        ).pack(side="left", padx=(8, 0))
        self.offline_var = tk.BooleanVar(value=OFFLINE_ONLY)
        ttk.Checkbutton(
            row4,
            text="Solo offline (sin red)",
            variable=self.offline_var,
            command=self.toggle_offline,
        ).pack(side="left", padx=(16, 0))
        self.progress = ttk.Progressbar(
            fw, mode="determinate", maximum=100, style="Sparrow.Horizontal.TProgressbar"
        )
        self.progress.pack(fill="x", pady=(10, 2))

        logbox = ttk.LabelFrame(outer, text=" REGISTRO ", padding=6)
        logbox.pack(fill="both", expand=True, pady=(10, 0))
        self.log_font = self._mono_font()
        self.log_text = tk.Text(
            logbox,
            height=12,
            width=80,
            state="disabled",
            font=self.log_font,
            background="#0f0f0f",
            foreground="#b5b5b5",
            insertbackground="#d8d8d8",
            selectbackground="#4a4a4a",
            selectforeground="#ffffff",
            relief="flat",
            highlightthickness=1,
            highlightbackground="#3d3d3d",
            highlightcolor="#5a5a5a",
            padx=8,
            pady=6,
        )
        scroll = ttk.Scrollbar(logbox, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)
        self.log_text.tag_configure("ok", foreground="#4caf50")
        self.log_text.tag_configure("warn", foreground=self.ORANGE)
        self.log_text.tag_configure("error", foreground="#ef5350")
        self.log_text.tag_configure("accent", foreground=self.ORANGE)
        self.log_text.tag_configure("dim", foreground="#8a8a8a")
        self.log_text.tag_configure("bold", font=(self.log_font[0], self.log_font[1], "bold"))

        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(8, 0))
        ttk.Label(
            footer,
            text="ledger-updater · manager.api.live.ledger.com · scriptrunner.api.live.ledger.com",
            style="Footer.TLabel",
        ).pack(side="left")
        self.refresh_buttons()

    # -- eventos ------------------------------------------------------------
    def log(self, msg, tag=None):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg + "\n", tag or ())
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def emit(self, *ev):
        self.queue.put(ev)

    def drain(self):
        try:
            while True:
                ev = self.queue.get_nowait()
                kind = ev[0]
                if kind == "log":
                    self.log(ev[1], ev[2] if len(ev) > 2 else None)
                elif kind == "busy":
                    self.busy = ev[1]
                    self.refresh_buttons()
                elif kind == "progress":
                    self.progress.configure(maximum=max(ev[2], 1), value=ev[1])
                elif kind == "devices":
                    self.devices = ev[1]
                    labels = [d["label"] for d in self.devices]
                    self.dev_combo.configure(values=labels)
                    if labels:
                        if self.dev_combo.current() < 0:
                            self.dev_combo.current(0)
                        self.select_device()
                    else:
                        self.dev_label.configure(text="No hay ningún Ledger conectado.")
                elif kind == "info":
                    info = ev[1]
                    target = info.get("se_target_id") or info.get("target_id")
                    model = model_for_target(target) or model_for_target(info.get("target_id") or 0) or "Ledger"
                    if info.get("is_bootloader"):
                        self.fw_label.configure(text=f"{model} · modo bootloader {info.get('mcu_bl_version')}")
                    else:
                        self.fw_label.configure(
                            text=f"{model} · firmware {info.get('se_version')} · MCU {info.get('mcu_version') or '?'}"
                        )
                elif kind == "error":
                    self.log(ev[1], "error")
                    messagebox.showerror("Ledger Updater", ev[1])
        except queue.Empty:
            pass
        self.root.after(60, self.drain)

    def refresh_buttons(self):
        busy_state = "disabled" if self.busy else "normal"
        net_state = "disabled" if (self.busy or OFFLINE_ONLY) else "normal"
        self.btn_update.configure(state=net_state)
        self.btn_changelog.configure(state=net_state)
        self.btn_genuine.configure(state=net_state)
        self.btn_apps.configure(state=net_state)
        self.btn_lang.configure(state=net_state)
        self.btn_genuine_off.configure(state=busy_state)
        self.btn_list_apps.configure(state=busy_state)

    def toggle_offline(self):
        set_offline(self.offline_var.get())
        self.refresh_buttons()
        if OFFLINE_ONLY:
            self.log(
                "Modo offline activado: sin red. Quedan disponibles detección, "
                "apps instaladas y genuino offline.",
                "warn",
            )
        else:
            self.log("Modo online activado.", "dim")

    # -- detección ----------------------------------------------------------
    def start_detect(self):
        if self.busy:
            return
        self.emit("busy", True)
        threading.Thread(target=self.detect_job, daemon=True).start()

    def detect_job(self):
        try:
            devices = []
            for d in LedgerHID.enumerate():
                pid = d.get("product_id", 0)
                model = model_for_pid(pid) or "Ledger"
                devices.append({"path": d["path"], "label": f"{model}  ·  {pid:04x}", "pid": pid})
            self.emit("devices", devices)
            if devices:
                dev, info = open_first()
                dev.close()
                self.emit("info", info)
        except Exception as exc:
            self.emit("devices", [])
            self.emit("log", str(exc), "warn")
        finally:
            self.emit("busy", False)

    def select_device(self):
        idx = self.dev_combo.current()
        if idx < 0 or not getattr(self, "devices", None):
            self.path = None
            return
        self.path = self.devices[idx]["path"]
        self.emit("log", f"Seleccionado: {self.devices[idx]['label']}", "dim")

    # -- trabajos -----------------------------------------------------------
    def run_job(self, fn, *args):
        def runner():
            self.emit("busy", True)
            try:
                fn(*args)
            except NoDevice as exc:
                self.emit("error", str(exc))
            except Exception as exc:
                self.emit("error", f"{exc}")
                traceback.print_exc()
            finally:
                self.emit("busy", False)

        threading.Thread(target=runner, daemon=True).start()

    def start_update(self):
        if self.busy:
            return
        if not messagebox.askyesno(
            "Actualizar firmware",
            "Se va a actualizar el firmware de tu Ledger.\n\n"
            "· No desconectes el dispositivo ni cierres este programa.\n"
            "· Tendrás que confirmar en la pantalla del Ledger.\n"
            "· Si el dispositivo está bloqueado, desbloquéalo antes.\n"
            "· Se guardará la lista de apps y se reinstalarán al terminar\n"
            "  (si la casilla está marcada).\n"
            "· También se conservará el pack de idioma si lo tienes instalado.\n\n"
            "¿Continuar?",
        ):
            return
        self.run_job(self.update_job)

    def update_job(self):
        log = lambda m, t=None: self.emit("log", m, t)
        progress = lambda c, t: self.emit("progress", c, t)
        info = update_device(
            self.path,
            log,
            progress,
            restore=self.restore_var.get(),
            restore_language=self.lang_var.get(),
        )
        self.emit("progress", 1, 1)
        self.emit("info", info)

    def start_list_apps(self):
        if self.busy:
            return
        self.run_job(self.list_apps_job)

    def open_apps(self):
        if self.busy:
            return
        AppsWindow(self)

    def open_language(self):
        if self.busy:
            return
        LanguageWindow(self)

    def open_changelog(self):
        if self.busy:
            return
        ChangelogWindow(self)

    def list_apps_job(self):
        log = lambda m, t=None: self.emit("log", m, t)
        dev, _info = open_path(self.path, log)
        try:
            quit_app(dev)
            time.sleep(1.0)
            log("Si el dispositivo lo pide, confirma el acceso en la pantalla.", "dim")
            apps = list_installed_apps(dev)
            if not apps:
                log("No hay apps instaladas.", "warn")
                return
            log(f"{len(apps)} apps instaladas:", "bold")
            for a in apps:
                log(f"  · {a['name']}  ({a['blocks']} bloques)")
        finally:
            dev.close()

    def start_genuine(self):
        if self.busy:
            return
        self.run_job(self.genuine_job)

    def genuine_job(self):
        log = lambda m, t=None: self.emit("log", m, t)
        progress = lambda c, t: self.emit("progress", c, t)
        if not offline_genuine_check(self.path, log):
            log("No se pudo verificar la atestación local; no se consulta el servicio online.", "warn")
            return
        genuine_check(self.path, log, progress)

    def start_genuine_offline(self):
        if self.busy:
            return
        self.run_job(self.genuine_offline_job)

    def genuine_offline_job(self):
        log = lambda m, t=None: self.emit("log", m, t)
        offline_genuine_check(self.path, log)


class AppsWindow:
    """Catálogo de apps del firmware actual: instalar y desinstalar."""

    def __init__(self, app):
        self.app = app
        self.win = tk.Toplevel(app.root)
        self.win.title("Apps · Ledger")
        self.win.geometry("860x580")
        self.win.transient(app.root)
        self.q = queue.Queue()
        self.catalog = []
        self.installed_apps = []
        self.meta_by_hash = {}
        self.installed = set()
        self.update_names = []
        self.model = ""
        self.target_id = None
        self.version = None
        self.provider = 1
        self.busy = True
        self._after = None

        self.build()
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self._after = self.win.after(80, self.drain)
        self.load()

    # -- UI -----------------------------------------------------------------
    def build(self):
        top = ttk.Frame(self.win, padding=(14, 12, 14, 6))
        top.pack(fill="x")
        ttk.Label(top, text="Apps del firmware", style="Title.TLabel").pack(anchor="w")
        self.dev_label = ttk.Label(top, text="Leyendo dispositivo…", style="Sub.TLabel")
        self.dev_label.pack(anchor="w", pady=(4, 0))

        search = ttk.Frame(self.win, padding=(14, 4))
        search.pack(fill="x")
        ttk.Label(search, text="Buscar:").pack(side="left")
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self.render())
        ttk.Entry(search, textvariable=self.filter_var).pack(side="left", fill="x", expand=True, padx=(8, 0))

        table = ttk.Frame(self.win, padding=(14, 4))
        table.pack(fill="both", expand=True)
        cols = ("app", "installed", "available", "state")
        self.tree = ttk.Treeview(table, columns=cols, show="headings", selectmode="browse")
        self.tree.heading("app", text="App")
        self.tree.heading("installed", text="Instalada")
        self.tree.heading("available", text="Disponible")
        self.tree.heading("state", text="Estado")
        self.tree.column("app", width=320)
        self.tree.column("installed", width=100, anchor="center")
        self.tree.column("available", width=100, anchor="center")
        self.tree.column("state", width=150, anchor="center")
        self.tree.tag_configure("update", foreground="#f8b21c")
        self.tree.tag_configure("installed", foreground="#4caf50")
        scroll = ttk.Scrollbar(table, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda _e: self.install_selected())

        bottom = ttk.Frame(self.win, padding=(14, 8, 14, 12))
        bottom.pack(fill="x")
        self.btn_install = ttk.Button(bottom, text="Instalar / actualizar", style="Accent.TButton", command=self.install_selected)
        self.btn_install.pack(side="left")
        self.btn_uninstall = ttk.Button(bottom, text="Desinstalar", command=self.uninstall_selected)
        self.btn_uninstall.pack(side="left", padx=(8, 0))
        self.btn_update_all = ttk.Button(bottom, text="Actualizar todas", command=self.update_all)
        self.btn_update_all.pack(side="left", padx=(8, 0))
        ttk.Button(bottom, text="Exportar lista…", command=self.export_list).pack(side="left", padx=(8, 0))
        ttk.Button(bottom, text="Recargar", command=self.load).pack(side="left", padx=(8, 0))
        ttk.Button(bottom, text="Cerrar", command=self.close).pack(side="right")
        self.status = ttk.Label(bottom, text="", style="Sub.TLabel")
        self.status.pack(side="left", padx=(14, 0))

        self.set_busy(True)

    def set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.btn_install.configure(state=state)
        self.btn_uninstall.configure(state=state)
        self.btn_update_all.configure(state=state)
        self.app.emit("busy", busy)

    def log(self, msg, tag=None):
        self.app.emit("log", msg, tag)

    def put(self, *ev):
        self.q.put(ev)

    def drain(self):
        try:
            while True:
                ev = self.q.get_nowait()
                kind = ev[0]
                if kind == "catalog":
                    self.catalog, self.installed_apps, meta, self.model = ev[1:5]
                    hashes = [a.get("hash", "") for a in self.installed_apps]
                    self.meta_by_hash = {h: m for h, m in zip(hashes, meta, strict=False) if m}
                    self.installed = {a["name"].strip().lower() for a in self.installed_apps}
                    self.dev_label.configure(
                        text=f"{len(self.catalog)} apps disponibles · firmware {self.version} · "
                        f"{len(self.installed_apps)} instaladas"
                    )
                    self.render()
                    updates = len(self.update_names)
                    self.status.configure(
                        text=f"{len(self.catalog)} apps · {updates} actualizables" if updates else f"{len(self.catalog)} apps en el catálogo"
                    )
                elif kind == "render":
                    self.render()
                elif kind == "installed":
                    self.installed = {n.strip().lower() for n in ev[1]}
                    self.render()
                elif kind == "busy":
                    self.set_busy(ev[1])
                elif kind == "status":
                    self.status.configure(text=ev[1])
                elif kind == "error":
                    self.log(ev[1], "error")
                    messagebox.showerror("Apps", ev[1], parent=self.win)
        except queue.Empty:
            pass
        if self.win.winfo_exists():
            self._after = self.win.after(80, self.drain)

    def _app_state(self, app, inst):
        if not inst:
            return "", ()
        key = (app.get("versionName") or "").strip().lower()
        if key in DYNAMIC_HASHES:
            return "instalada", ()
        if inst.get("hash") == app.get("hash"):
            return "actualizada", ("installed",)
        meta = self.meta_by_hash.get(inst.get("hash", ""))
        inst_ver = (meta or {}).get("version") or ""
        available = app.get("version") or ""
        if inst_ver and available and semver_gt(available, inst_ver):
            return "actualizable", ("update",)
        if not inst_ver:
            return "instalada", ()
        return "actualizable", ("update",)

    def render(self):
        query = self.filter_var.get().strip().lower()
        installed_by_name = {a["name"].strip().lower(): a for a in self.installed_apps}
        self.update_names = []
        for app in self.catalog:
            inst = installed_by_name.get((app.get("versionName") or "").strip().lower())
            state, _tag = self._app_state(app, inst)
            if state == "actualizable":
                self.update_names.append(app.get("versionName") or "")

        self.tree.delete(*self.tree.get_children())
        catalog_names = set()
        for app in self.catalog:
            name = app.get("versionName") or ""
            key = name.strip().lower()
            catalog_names.add(key)
            if query and query not in key:
                continue
            inst = installed_by_name.get(key)
            meta = self.meta_by_hash.get(inst.get("hash", "")) if inst else None
            inst_ver = ((meta or {}).get("version") or "") if inst else ""
            state, tag = self._app_state(app, inst)
            self.tree.insert(
                "",
                "end",
                values=(name, inst_ver or "—", app.get("version", "") or "—", state),
                tags=tag,
            )
        for a in self.installed_apps:
            key = a["name"].strip().lower()
            if key in catalog_names:
                continue
            if query and query not in key:
                continue
            meta = self.meta_by_hash.get(a.get("hash", ""))
            ver = (meta or {}).get("version") or "—"
            self.tree.insert(
                "",
                "end",
                values=(a["name"], ver, "—", "instalada (no en catálogo)"),
                tags=("installed",),
            )

    def selected_name(self):
        sel = self.tree.selection()
        if not sel:
            return None
        return self.tree.item(sel[0], "values")[0]

    # -- trabajos -----------------------------------------------------------
    def load(self):
        if self.busy and self.catalog:
            return
        self.set_busy(True)
        self.put("status", "Leyendo dispositivo y catálogo…")
        threading.Thread(target=self.load_job, daemon=True).start()

    def load_job(self):
        try:
            dev, info = open_path(self.app.path, self.log)
            try:
                quit_app(dev)
                time.sleep(0.8)
                info = get_firmware_info(dev)
                self.target_id = info.get("se_target_id") or info.get("target_id")
                self.version = clean_version(info.get("se_version") or "")
                self.provider = provider_from_version(info.get("se_version") or "")
                self.log(f"Catálogo para firmware {self.version}…", "dim")
                catalog = app_catalog(self.target_id, self.version, self.provider)
                self.log("Leyendo apps instaladas (confirma en el dispositivo si lo pide)…", "dim")
                installed = list_installed_apps(dev)
                meta = apps_by_hash([a.get("hash", "") for a in installed])
                model = model_for_target(self.target_id) or "Ledger"
            finally:
                dev.close()
            self.put("catalog", catalog, installed, meta, model)
        except Exception as exc:
            self.put("error", str(exc))
        finally:
            self.put("busy", False)

    def install_selected(self):
        if self.busy:
            return
        name = self.selected_name()
        if not name:
            self.put("status", "Selecciona una app primero")
            return
        self.set_busy(True)
        self.put("status", f"Instalando {name}…")
        threading.Thread(target=self.install_job, args=(name,), daemon=True).start()

    def _install_one(self, name):
        dev, _info = open_path(self.app.path, self.log)
        try:
            quit_app(dev)
            time.sleep(0.8)
            installed = [a["name"] for a in list_installed_apps(dev)]
            install_app_entry(
                dev,
                self.target_id,
                self.catalog,
                name,
                installed,
                self.log,
                lambda c, t: self.app.emit("progress", c, t),
            )
        finally:
            dev.close()
        app = next(
            (a for a in self.catalog if (a.get("versionName") or "").strip().lower() == name.strip().lower()),
            None,
        )
        self.installed_apps = [
            a for a in self.installed_apps if a["name"].strip().lower() != name.strip().lower()
        ]
        if app:
            self.installed_apps.append(
                {
                    "name": name,
                    "hash": app.get("hash", ""),
                    "blocks": 0,
                    "flags": 0,
                    "hash_code_data": "",
                }
            )
            self.meta_by_hash[app.get("hash", "")] = app
        self.installed.add(name.strip().lower())
        self.put("render")
        self.put("status", f"{name} instalada")
        self.log(f"App {name} instalada.", "ok")

    def install_job(self, name):
        try:
            self._install_one(name)
        except Exception as exc:
            self.put("error", str(exc))
            self.put("status", "Error al instalar")
        finally:
            self.put("busy", False)

    def update_all(self):
        if self.busy:
            return
        names = list(dict.fromkeys(self.update_names))
        if not names:
            self.put("status", "No hay apps actualizables")
            return
        if not messagebox.askyesno(
            "Actualizar apps",
            "Se van a actualizar estas apps:\n\n"
            + "\n".join(f"· {n}" for n in names)
            + "\n\n¿Continuar?",
            parent=self.win,
        ):
            return
        self.set_busy(True)
        self.put("status", f"Actualizando {len(names)} apps…")
        threading.Thread(target=self.update_all_job, args=(names,), daemon=True).start()

    def update_all_job(self, names):
        ok = 0
        failed = []
        for name in names:
            try:
                self._install_one(name)
                ok += 1
            except Exception as exc:
                failed.append(name)
                self.log(f"No se pudo actualizar {name}: {exc}", "warn")
        self.put("status", f"Actualizadas {ok}" + (f", fallos {len(failed)}" if failed else ""))
        self.put("busy", False)

    def export_list(self):
        if not self.installed_apps:
            self.put("status", "No hay apps instaladas que exportar")
            return
        path = filedialog.asksaveasfilename(
            parent=self.win,
            title="Exportar lista de apps",
            defaultextension=".json",
            initialfile="ledger-apps.json",
            filetypes=[("JSON", "*.json"), ("Todos", "*.*")],
        )
        if not path:
            return
        payload = {
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "model": self.model,
            "firmware": self.version,
            "target_id": self.target_id,
            "apps": [
                {
                    "name": a.get("name"),
                    "blocks": a.get("blocks"),
                    "flags": a.get("flags"),
                    "hash": a.get("hash"),
                    "hash_code_data": a.get("hash_code_data"),
                    "version": (self.meta_by_hash.get(a.get("hash", "")) or {}).get("version"),
                }
                for a in self.installed_apps
            ],
        }
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
            self.log(f"Lista de apps exportada a {path}", "ok")
            self.put("status", "Lista exportada")
        except Exception as exc:
            self.put("error", f"No se pudo escribir el archivo: {exc}")

    def uninstall_selected(self):
        if self.busy:
            return
        name = self.selected_name()
        if not name:
            self.put("status", "Selecciona una app primero")
            return
        if not messagebox.askyesno(
            "Desinstalar app",
            f"¿Desinstalar «{name}» del dispositivo?",
            parent=self.win,
        ):
            return
        self.set_busy(True)
        self.put("status", f"Desinstalando {name}…")
        threading.Thread(target=self.uninstall_job, args=(name,), daemon=True).start()

    def uninstall_job(self, name):
        try:
            app = next(
                (a for a in self.catalog if (a.get("versionName") or "").strip().lower() == name.strip().lower()),
                None,
            )
            if not app:
                raise RuntimeError(f"«{name}» no está en el catálogo")
            dev, _info = open_path(self.app.path, self.log)
            try:
                quit_app(dev)
                time.sleep(0.8)
                uninstall_app_hsm(
                    dev,
                    self.target_id,
                    app,
                    self.log,
                    lambda c, t: self.app.emit("progress", c, t),
                )
            finally:
                dev.close()
            self.installed_apps = [
                a for a in self.installed_apps if a["name"].strip().lower() != name.strip().lower()
            ]
            self.installed.discard(name.strip().lower())
            self.put("render")
            self.put("status", f"{name} desinstalada")
            self.log(f"App {name} desinstalada.", "ok")
        except Exception as exc:
            self.put("error", str(exc))
            self.put("status", "Error al desinstalar")
        finally:
            self.put("busy", False)

    def close(self):
        if self._after:
            try:
                self.win.after_cancel(self._after)
            except Exception:
                pass
        self.app.emit("busy", False)
        self.win.destroy()


class LanguageWindow:
    """Ver y cambiar el pack de idioma del dispositivo."""

    def __init__(self, app):
        self.app = app
        self.win = tk.Toplevel(app.root)
        self.win.title("Idioma · Ledger")
        self.win.geometry("480x300")
        self.win.transient(app.root)
        self.q = queue.Queue()
        self.packs = []
        self.current = []
        self.device_version_id = None
        self.final_firmware_id = None
        self.busy = True
        self._after = None
        self.build()
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self._after = self.win.after(80, self.drain)
        self.load()

    def build(self):
        box = ttk.Frame(self.win, padding=16)
        box.pack(fill="both", expand=True)
        ttk.Label(box, text="Pack de idioma", style="Title.TLabel").pack(anchor="w")
        self.current_label = ttk.Label(box, text="Leyendo dispositivo…", style="Sub.TLabel")
        self.current_label.pack(anchor="w", pady=(4, 12))

        ttk.Label(box, text="Idioma disponible:").pack(anchor="w")
        self.combo = ttk.Combobox(box, state="readonly")
        self.combo.pack(fill="x", pady=(4, 12))

        row = ttk.Frame(box)
        row.pack(fill="x")
        self.btn_apply = ttk.Button(row, text="Instalar idioma", style="Accent.TButton", command=self.apply)
        self.btn_apply.pack(side="left")
        ttk.Button(row, text="Recargar", command=self.load).pack(side="left", padx=(8, 0))
        ttk.Button(row, text="Cerrar", command=self.close).pack(side="right")
        self.status = ttk.Label(box, text="", style="Sub.TLabel")
        self.status.pack(anchor="w", pady=(12, 0))
        self.set_busy(True)

    def set_busy(self, busy):
        self.busy = busy
        self.btn_apply.configure(state="disabled" if busy else "normal")
        self.app.emit("busy", busy)

    def log(self, msg, tag=None):
        self.app.emit("log", msg, tag)

    def put(self, *ev):
        self.q.put(ev)

    def drain(self):
        try:
            while True:
                ev = self.q.get_nowait()
                if ev[0] == "packs":
                    self.packs, current, self.device_version_id, self.final_firmware_id = ev[1:5]
                    self.current = current
                    self.render()
                elif ev[0] == "busy":
                    self.set_busy(ev[1])
                elif ev[0] == "status":
                    self.status.configure(text=ev[1])
                elif ev[0] == "error":
                    self.log(ev[1], "error")
                    messagebox.showerror("Idioma", ev[1], parent=self.win)
        except queue.Empty:
            pass
        if self.win.winfo_exists():
            self._after = self.win.after(80, self.drain)

    def render(self):
        values = []
        for pack in self.packs:
            name = (pack.get("language") or "").capitalize()
            values.append(f"{name}  ·  {pack.get('version', '')}")
        self.combo.configure(values=values)
        current_ids = [p["id"] for p in self.current]
        if current_ids:
            name = LANGUAGE_NAMES.get(current_ids[0], str(current_ids[0])).capitalize()
            self.current_label.configure(text=f"Idioma actual: {name}")
        else:
            self.current_label.configure(text="Idioma actual: English (base)")
        for i, pack in enumerate(self.packs):
            if pack.get("language", "").lower() in ("brazilian", "portuguese") and 3 in current_ids:
                self.combo.current(i)
                break
            if LANGUAGE_NAME_TO_ID.get(pack.get("language", "").lower()) in current_ids:
                self.combo.current(i)
                break
        else:
            if values:
                self.combo.current(0)
        self.status.configure(text=f"{len(self.packs)} packs disponibles")

    def load(self):
        if self.busy and self.packs:
            return
        self.set_busy(True)
        self.put("status", "Leyendo dispositivo…")
        threading.Thread(target=self.load_job, daemon=True).start()

    def load_job(self):
        try:
            dev, info = open_path(self.app.path, self.log)
            try:
                quit_app(dev)
                time.sleep(0.8)
                info = get_firmware_info(dev)
                target_id = info.get("se_target_id") or info.get("target_id")
                version = clean_version(info.get("se_version") or "")
                provider = provider_from_version(info.get("se_version") or "")
                dv = api_get("/get_device_version", provider=provider, target_id=target_id)
                try:
                    cur = api_get(
                        "/get_firmware_version",
                        device_version=dv["id"],
                        version_name=version,
                        provider=provider,
                    )
                    final_id = cur["id"]
                except Exception:
                    latest = latest_final(fetch_all_finals(), dv["id"])
                    final_id = latest["id"] if latest else None
                packs = language_catalog(dv["id"], final_id) if final_id else []
                current = list_language_packs(dev)
            finally:
                dev.close()
            self.put("packs", packs, current, dv["id"], final_id)
        except Exception as exc:
            self.put("error", str(exc))
        finally:
            self.put("busy", False)

    def apply(self):
        if self.busy or not self.packs:
            return
        idx = self.combo.current()
        if idx < 0:
            self.put("status", "Selecciona un idioma")
            return
        pack = self.packs[idx]
        name = pack.get("language", "")
        if not messagebox.askyesno(
            "Cambiar idioma",
            f"¿Instalar el idioma «{name.capitalize()}» en el dispositivo?\n\n"
            "Se sustituirá el pack actual. Confirma también en la pantalla del Ledger.",
            parent=self.win,
        ):
            return
        lang_id = LANGUAGE_NAME_TO_ID.get(name.lower())
        if lang_id is None:
            self.put("error", f"Idioma no reconocido: {name}")
            return
        self.set_busy(True)
        self.put("status", f"Instalando {name}…")
        threading.Thread(target=self.apply_job, args=(lang_id, name), daemon=True).start()

    def apply_job(self, lang_id, name):
        try:
            dev, _info = open_path(self.app.path, self.log)
            try:
                quit_app(dev)
                time.sleep(0.8)
                install_language_pack(
                    dev,
                    self.device_version_id,
                    self.final_firmware_id,
                    lang_id,
                    self.log,
                    lambda c, t: self.app.emit("progress", c, t),
                )
            finally:
                dev.close()
            self.current = [{"id": lang_id, "size": 0}]
            self.put("packs", self.packs, self.current, self.device_version_id, self.final_firmware_id)
            self.put("status", f"Idioma {name.capitalize()} instalado")
        except Exception as exc:
            self.put("error", str(exc))
            self.put("status", "Error al cambiar el idioma")
        finally:
            self.put("busy", False)

    def close(self):
        if self._after:
            try:
                self.win.after_cancel(self._after)
            except Exception:
                pass
        self.app.emit("busy", False)
        self.win.destroy()


class ChangelogWindow:
    """Novedades del firmware que se instalaría, sin flashear."""

    def __init__(self, app):
        self.app = app
        self.win = tk.Toplevel(app.root)
        self.win.title("Novedades · Ledger")
        self.win.geometry("820x620")
        self.win.transient(app.root)
        self.q = queue.Queue()
        self.busy = False
        self._after = None
        self.build()
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self._after = self.win.after(80, self.drain)
        self.load()

    def build(self):
        box = ttk.Frame(self.win, padding=(14, 12, 14, 8))
        box.pack(fill="both", expand=True)
        ttk.Label(box, text="Novedades del firmware", style="Title.TLabel").pack(anchor="w")
        self.status = ttk.Label(box, text="Leyendo dispositivo…", style="Sub.TLabel")
        self.status.pack(anchor="w", pady=(4, 8))

        textbox = ttk.Frame(box)
        textbox.pack(fill="both", expand=True)
        self.text = tk.Text(
            textbox,
            bg="#0f0f0f",
            fg="#b5b5b5",
            insertbackground="#d8d8d8",
            selectbackground="#4a4a4a",
            selectforeground="#ffffff",
            relief="flat",
            highlightthickness=1,
            highlightbackground="#3d3d3d",
            wrap="word",
            font=self.app.log_font if hasattr(self.app, "log_font") else ("TkFixedFont", 9),
        )
        scroll = ttk.Scrollbar(textbox, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.text.configure(state="disabled")

        row = ttk.Frame(box)
        row.pack(fill="x", pady=(8, 0))
        ttk.Button(row, text="Recargar", command=self.load).pack(side="left")
        ttk.Button(row, text="Cerrar", command=self.close).pack(side="right")

    def log(self, msg, tag=None):
        self.app.emit("log", msg, tag)

    def put(self, *ev):
        self.q.put(ev)

    def drain(self):
        try:
            while True:
                ev = self.q.get_nowait()
                if ev[0] == "text":
                    self.text.configure(state="normal")
                    self.text.delete("1.0", "end")
                    self.text.insert("1.0", ev[1])
                    self.text.configure(state="disabled")
                elif ev[0] == "status":
                    self.status.configure(text=ev[1])
                elif ev[0] == "busy":
                    self.busy = ev[1]
                    self.app.emit("busy", ev[1])
                elif ev[0] == "error":
                    self.log(ev[1], "error")
                    self.status.configure(text="Error")
                    messagebox.showerror("Novedades", ev[1], parent=self.win)
        except queue.Empty:
            pass
        if self.win.winfo_exists():
            self._after = self.win.after(80, self.drain)

    def load(self):
        if self.busy:
            return
        self.put("busy", True)
        self.put("status", "Leyendo dispositivo y calculando la ruta…")
        threading.Thread(target=self.load_job, daemon=True).start()

    def load_job(self):
        try:
            plan = update_plan(self.app.path, self.log)
            text = changelog_text(plan)
            self.put("text", text)
            if plan.get("latest"):
                self.put(
                    "status",
                    f"Actual {plan['current'] or 'bootloader'} → {plan['latest']['version']}",
                )
            else:
                self.put("status", "No hay firmware disponible")
        except Exception as exc:
            self.put("error", str(exc))
        finally:
            self.put("busy", False)

    def close(self):
        if self._after:
            try:
                self.win.after_cancel(self._after)
            except Exception:
                pass
        self.app.emit("busy", False)
        self.win.destroy()


def main() -> int:
    parser = argparse.ArgumentParser(description="Ledger Firmware Updater")
    parser.add_argument("--selftest", action="store_true", help="consulta la API sin dispositivo")
    parser.add_argument("--tls-pin", action="store_true", help="muestra el pin SPKI actual de la API")
    parser.add_argument("--offline", action="store_true", help="bloquea toda conexión de red")
    args = parser.parse_args()

    if args.offline:
        set_offline(True)
    if args.tls_pin:
        return tls_pin_report()
    if args.selftest:
        if OFFLINE_ONLY:
            print("Modo offline activo: el selftest necesita red.")
            return 1
        return selftest()
    if not HAVE_TK:
        print("Falta tkinter (python3-tk).")
        return 1
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
