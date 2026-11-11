# Legal review before publishing

Status: pre-publication review of the repository and its release artifacts.
This is an engineering checklist, **not legal advice**. For commercial use or a
legal dispute, consult a lawyer.

## 1. Repository history and contents

- No secrets, tokens, private keys, seed phrases, pairing codes, logs, or
  personal paths were found in the git history.
- No firmware binaries or vendor installers are committed. The only binary
  asset is the Inter font (0.8 MB).
- Screenshots show the UI only; no wallet data, balances, addresses, or seeds.

## 2. Trademarks

- No vendor logos or brand assets are used. Icons are original.
- Names are used descriptively ("firmware updater for Trezor/Ledger/BitBox"),
  with an explicit non-affiliation disclaimer in `README.md` and
  `TRADEMARKS.md`.
- Residual risk: vendor terms restrict use of their marks in connection with
  other products. The use here is nominative (identifying compatibility), and
  the project can be renamed quickly if a vendor objects.

## 3. Copyright and licenses

- Project code: MIT (`LICENSE`), copyright Arkad.
- Third-party components are documented in `THIRD_PARTY.md`; full texts are in
  `licenses/` (LGPL-3.0, Apache-2.0, BSD-3-Clause) and `common/fonts/OFL.txt`.
- `NOTICE` documents components, copyright holders, and the LGPL position.
- Release packages now include `LICENSE`, `NOTICE`, `THIRD_PARTY.md`,
  `TRADEMARKS.md`, and `licenses/`; `.deb` packages also ship a Debian
  `copyright` file.
- LGPL: PySide6/Qt and trezorlib are unmodified. Complete source and build
  scripts are published so the combined work can be rebuilt with modified
  versions; Qt libraries in the onedir bundle can be replaced in place.
  Residual nuance: pure-Python LGPL code inside the PyInstaller archive is not
  replaceable without rebuilding, which the published build scripts allow.

## 4. Firmware and vendor services

- Firmware, translations and catalogs are downloaded at runtime from official
  endpoints (`data.trezor.io`, BitBox's GitHub releases, Ledger's manager API)
  and verified before installation. Nothing is redistributed.
- Ledger's release API and HSM are used by the app; review their terms if the
  project grows. No circumvention of technical protection measures is involved.

## 5. Privacy

- No accounts, telemetry, or analytics endpoints.
- The app never reads xpubs, addresses, balances or history.
- Network metadata still visible to firmware hosts: IP and requested
  model/version. Optional VPN/proxy mitigates it.
- Logs and BitBox pairing keys stay in `~/.cache`; logs can contain the device
  name and pairing codes, and are documented as non-publishable.

## 6. Liability

- MIT warranty disclaimer and liability limitation; `SECURITY.md` warns that
  flashing can brick a device.
- Disclaimers do not override mandatory consumer law. In the EU, the Product
  Liability Directive now covers software; free, non-commercial open-source
  development outside a commercial activity benefits from specific exemptions,
  but this should be reviewed if the project is monetised.
- Recommended optional hardening: a first-run acknowledgement dialog before any
  device operation.

## 7. Release and provenance

- Releases are signed with a hardware-backed GPG key; tags are signed.
- Builds are reproducible with `PYTHONHASHSEED=0` and pinned PyInstaller.
- Provenance attestation is unavailable for user-owned private repositories;
  it activates automatically if the repository is public.

## 8. Before making the repository public

- [x] Clean history (no secrets, logs, binaries).
- [x] License texts and notices in the repository.
- [x] Disclaimer and trademark policy.
- [x] License/notice files included in packages.
- [ ] Publish the public key (GitHub account and/or keyserver).
- [ ] Rebuild release artifacts so they include the license files (v0.1.2).
- [ ] Optional: first-run risk acknowledgement.
- [ ] Optional: legal review if the project becomes commercial.
