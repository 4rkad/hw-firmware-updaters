# Firmware Updater for Trezor, Ledger and BitBox

[![Linux builds](https://github.com/4rkad/hw-firmware-updaters/actions/workflows/linux-build.yml/badge.svg)](https://github.com/4rkad/hw-firmware-updaters/actions/workflows/linux-build.yml)
[![Latest release](https://img.shields.io/github/v/release/4rkad/hw-firmware-updaters)](https://github.com/4rkad/hw-firmware-updaters/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Independent, unofficial desktop applications for updating hardware wallet
firmware over USB, without installing the vendor's full wallet suite.

> Not affiliated with, sponsored by, or endorsed by SatoshiLabs s.r.o.,
> Ledger SAS, or Shift Crypto AG. Product names are used only to describe
> compatibility and remain the property of their respective owners.

## Overview

Three native Linux applications, one per vendor:

| Application | Firmware | Device management |
|---|---|---|
| Trezor | Universal and Bitcoin-only editions | PIN, passphrase, wallet setup, backup, recovery, language |
| Ledger | Firmware, apps and language packs | App catalog, export, genuine check |
| BitBox | BitBox02 and BitBox02 Nova, both editions | Passphrase, microSD backup, wallet setup, BIP85, rename, reset |

Each application performs one task: identify the device, download the official
firmware, verify it, and write it. No account, wallet, portfolio, or telemetry.

| Trezor | Ledger | BitBox |
|---|---|---|
| ![](docs/screenshots/trezor.png) | ![](docs/screenshots/ledger.png) | ![](docs/screenshots/bitbox.png) |

*Screenshots with a device connected. Trezor and Ledger use representative
data to show the full interface.*

## Motivation

Vendor firmware is a signed binary of a few megabytes. The official update
path, however, is a complete wallet application:

- **Trezor Suite** is an Electron wallet with accounts, portfolio and usage
  analytics (opt-out available). Firmware updates are part of that application.
- **Ledger Live** is closed-source and the only official channel for firmware,
  apps and language packs. It includes an account layer, usage analytics, and a
  packaging model that does not always integrate well with Linux.
- **BitBoxApp** is open source, but still a full wallet application that must be
  installed and maintained in order to flash a device.

Firmware updates do not require wallet functionality. They require a reliable
USB connection, the official firmware, and verification before installation.

## Privacy

Vendor suites are wallets. Rendering balances and history requires account
descriptors (xpubs or addresses) and queries to blockchain backends; depending
on configuration, they also collect usage analytics. Opening such an
application solely to update firmware unnecessarily exposes that surface.

These applications do not process wallet data:

- No accounts, portfolios, or telemetry.
- No xpub, address, balance, or transaction history is read from the device or
  requested from any service.
- Device calls are limited to system and firmware information, settings, and —
  only on explicit user action — seed display or backup.
- Logs are written locally to `~/.cache/<app>.log` and never uploaded. BitBox
  pairing keys are stored in the same directory.
- Ledger offline mode bypasses the vendor's genuine-check service; the online
  check is optional.

Network requests are limited to firmware sources:

| Endpoint | Purpose |
|---|---|
| `data.trezor.io` | Trezor firmware metadata and binaries |
| `api.github.com`, GitHub releases | BitBox firmware metadata and binaries |
| `manager.api.live.ledger.com` | Ledger firmware, apps and language packs |
| `download.languages.ledger.com` | Ledger language data |

Those hosts see the client IP and the requested model or version. This is
metadata, not wallet data, and can be reduced with a VPN or proxy. No analytics
endpoint, account identifier, or wallet fingerprint is contacted.

## Verification

Each application verifies as much as the vendor allows before writing:

| Vendor | Host-side checks | Device-side checks |
|---|---|---|
| Trezor | Model, published fingerprint, firmware signature (`trezorlib`) | Bootloader verifies the firmware |
| BitBox | SHA-256 against the published digest, edition magic | Bootloader verifies the signature; attestation on connect |
| Ledger | Firmware parameters and HTTPS transport | Device verifies the firmware before applying it; HSM genuine check |

A failed check aborts the operation before the device is modified.

The BitBox SHA-256 is read from the same GitHub release metadata as the
binary. It provides integrity, not authenticity; authenticity comes from the
bootloader's signature verification. The host does not claim otherwise. The Ledger
backend review is documented in `docs/REVIEW-ledger.md` (Spanish).

### Release verification

Releases publish a single `SHA256SUMS` file covering every artifact, signed
with GPG:

```
B6DD B2AF 5466 FCBC 26C9  A48E 08D4 B2CC 5280 22BB
```

Signatures are produced by a hardware-backed signing subkey; `gpg --verify`
prints its fingerprint when checking a release.

```bash
gpg --import docs/KEY.asc
gpg --verify SHA256SUMS.asc SHA256SUMS
sha256sum -c SHA256SUMS
```

A valid signature proves the checksums were signed by the key; the checksums
prove the artifacts were not modified. Signing does not prove how a binary was
built.

Build provenance is available for CI artifacts:

```bash
gh attestation verify hwfw-trezor-X.Y.Z-x86_64.AppImage \
  --repo 4rkad/hw-firmware-updaters
```

## Architecture

- **One application per vendor**, each with an independent backend. Device
  commands, states and verification rules differ enough that a shared flashing
  layer would add fragility rather than reduce duplication.
- **PySide6/Qt** user interface. Trezor and BitBox share theme, dialogs and
  components; Ledger uses the same visual language. A legacy Tkinter mode is
  available with `--tk`.
- **Worker threads** for downloads and device I/O. The interface remains
  responsive, operations have timeouts, and failures leave the UI in a
  consistent state.
- **Direct USB HID transport**, without Bridge or background daemons.
- **Client-side verification** of hashes, magic, model, edition, and signatures
  where the host can validate them.
- **Local logs** in `~/.cache/<app>.log`.

## Provenance

- Application code was written for this project. No UI, icons, or assets were
  taken from the vendor suites.
- Trezor communication uses `trezorlib` (LGPL-3.0); BitBox uses the `bitbox02`
  library (Apache-2.0).
- Ledger provides no equivalent update library. The backend implements HID
  transport and queries the vendor manager API and HSM directly.
- Firmware, translations and catalogs are not redistributed. They are
  downloaded at runtime from official sources and verified before installation.
- Icons are original. The typeface is Inter, licensed under SIL OFL 1.1
  (`common/fonts/OFL.txt`).
- Dependency licenses are documented in `THIRD_PARTY.md`, `NOTICE` and
  `licenses/`.

## Limitations

- Linux only. Windows and macOS are not supported or tested.
- BitBox flows were validated with a physical BitBox02 Bitcoin-only: detection,
  pairing, attestation, backup, rename, BIP85 and reset. BitBox02 Nova uses the
  same flow with model-specific signed assets and has not been hardware-tested.
- Trezor and Ledger are implemented against their official libraries and APIs.
  Not every model has been tested.
- This is not a wallet manager: it does not store seeds, sign transactions, or
  read xpubs.
- Ledger integration pins `livecommonversion` (overridable with the
  `LIVE_COMMON_VERSION` environment variable) and may break if the vendor
  changes its API. A weekly CI job runs `--selftest`; see `docs/LEDGER-API.md`
  for detection and update steps. Offline genuine checks use the issuer keys
  known to this project; an unrecognized model is reported as unverified, not
  as counterfeit.
- The legacy Tk interface (`--tk`) exposes an optional BIP85 Lightning seed
  export to the host. The Qt interface only displays the BIP39 mnemonic on the
  device.
- The interface and logs are in Spanish; the documentation is in English.
- Flashing firmware carries risk. An interrupted operation can render the
  device unusable. The software is provided without warranty; see `SECURITY.md`.

## Requirements

- Linux (x86_64).
- Python 3.12 or newer and `venv` when running from source.
- USB access to the device; the `.deb` packages install the required udev
  rules.
- `squashfs-tools` when building AppImages from source.

## Installation

### Prebuilt packages

The [releases page](https://github.com/4rkad/hw-firmware-updaters/releases)
provides, for each application:

- **AppImage** (x86_64): portable, no installation. udev rules are required for
  the vendor's USB IDs; the `.deb` installs them.
- **`.deb`** (amd64): installs to `/opt/hwfw`, adds the menu entry and the
  vendor udev rules.

Verify the signed `SHA256SUMS` before running any artifact.

### From source

```bash
cd trezor && ./run.sh
cd ledger && ./run.sh
cd bitbox && ./run.sh
```

Each script creates its virtual environment on first run.

Desktop integration:

```bash
./packaging/install.sh
./packaging/install.sh --uninstall
```

### Building packages

```bash
VERSION=0.1.2 ./packaging/build_linux.sh
```

The script runs PyInstaller for each application and produces
`build/appimage/`, `build/deb/` and `build/release/SHA256SUMS`. Python
interpreters can be overridden with `PY_TREZOR`, `PY_LEDGER` and `PY_BITBOX`.
CI does not hold the signing key; artifacts are signed by the maintainer after
download.

## Reproducible builds

`packaging/build_linux.sh` sets `PYTHONHASHSEED=0` and pins PyInstaller. The AppImage runtime is pinned by
SHA-256. With
identical Python and dependency versions, rebuilding a tag produces
byte-identical files.

Verified on the `v0.1.0` tag: two builds in place and one from a different
directory produced 487/487 identical files. `v0.1.2` uses the same settings and
is built from the tag in CI. Dependencies are pinned with hashes in `requirements.txt`, and CI actions are
pinned by commit SHA.

## Development

```bash
make lint          # ruff
make test          # backend tests
make screenshots   # regenerate docs/screenshots
make build         # AppImage and .deb
```

Release procedure: `docs/RELEASE.md`. Contribution guidelines:
`CONTRIBUTING.md`.

## Project structure

```
.github/       CI: build, tests, smoke test, provenance attestation
common/        shared UI (theme, dialogs, jobs) and Inter font
trezor/        Qt application, backend and Tkinter mode
ledger/        Qt application, backend and tests
bitbox/        Qt application and backend
icons/         original icons
licenses/      third-party license texts
packaging/     release build and desktop installer
docs/          screenshots, release process, Ledger API notes and review
```

## Security

Report vulnerabilities as described in `SECURITY.md`. Do not include seeds,
private keys, pairing codes, xpubs, or addresses in reports or logs.

## License and trademarks

Source code is licensed under MIT (`LICENSE`). Third-party notices are in
`THIRD_PARTY.md`, `NOTICE` and `licenses/`.

Trezor®, Ledger® and BitBox® are trademarks of their respective owners. This
project does not use their logos and implies no affiliation or endorsement. See
`TRADEMARKS.md`.
