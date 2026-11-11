# Third-Party Components

This repository does not bundle firmware, translations, or vendor binaries.
They are downloaded at runtime from official sources and verified before use.

## Python dependencies

| Package | Version | License | Purpose |
|---|---|---|---|
| `trezor` | 0.20.2 | LGPL-3.0-only | Trezor communication |
| `bitbox02` | 7.0.0 | Apache-2.0 | BitBox communication |
| `hidapi` | 0.15.0 | BSD-3-Clause / GPL-3.0 | USB HID transport (Ledger) |
| `websocket-client` | 1.9.2 | Apache-2.0 | Ledger API |
| `cryptography` | 50.0.2 | Apache-2.0 OR BSD-3-Clause | Cryptographic operations (Ledger) |
| `requests` | 2.34.2 | Apache-2.0 | Downloads and GitHub API |
| `PySide6` | 6.8.3 | LGPL-3.0-only / GPL-2.0 / GPL-3.0 | User interface |

These packages are installed as external dependencies through each
application's `requirements.txt`. Their sources are not modified or vendored.
Full license texts are in `licenses/`; component notices are in `NOTICE`.

## Font

| Resource | License | Notes |
|---|---|---|
| Inter (variable) | SIL Open Font License 1.1 | See `common/fonts/OFL.txt` |

## Trademarks

Trademark notices and usage policy: `TRADEMARKS.md`.
