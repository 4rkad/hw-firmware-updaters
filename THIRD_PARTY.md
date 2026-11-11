# Third-party dependencies

This repository does not bundle firmware, translations or vendor binaries: they
are downloaded at runtime from official sources and verified before use.

## Python libraries

| Package | Version | License | Use |
|---|---|---|---|
| `trezor` | 0.20.2 | LGPL-3.0-only | Trezor communication |
| `bitbox02` | 7.0.0 | Apache-2.0 | BitBox communication |
| `hidapi` | 0.15.0 | BSD-3-Clause / GPL-3.0 | USB HID transport (Ledger) |
| `websocket-client` | 1.9.2 | Apache-2.0 | Ledger API |
| `cryptography` | 50.0.2 | Apache-2.0 OR BSD-3-Clause | Cryptography (Ledger) |
| `requests` | 2.34.2 | Apache-2.0 | Downloads and GitHub API |
| `PySide6` | 6.8.3 | LGPL-3.0-only / GPL-2.0 / GPL-3.0 | GUI |

They are installed as external dependencies through `requirements.txt`; their
sources are neither modified nor included here. Full license texts are in
`licenses/`; see also `NOTICE`.

## Font

| Resource | License | Note |
|---|---|---|
| Inter (variable) | SIL Open Font License 1.1 | See `common/fonts/OFL.txt` |

## Trademarks

Trezor is a trademark of SatoshiLabs s.r.o. Ledger is a trademark of Ledger SAS.
BitBox is a trademark of Shift Crypto AG. They are mentioned only to describe
compatibility. This project is not affiliated with or endorsed by any of them.
