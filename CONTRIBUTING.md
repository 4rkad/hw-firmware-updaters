# Contributing

Small, focused changes are welcome.

## Issues

Include the app (Trezor/Ledger/BitBox), version, device model and firmware, and
the relevant log lines. **Never** include seeds, private keys, pairing codes,
xpubs or addresses.

## Pull requests

- One app or one concern per change; keep the three backends independent.
- Run the checks before opening a PR:
  ```bash
  ruff check trezor ledger bitbox common docs/make_screenshots.py
  cd ledger && python -m pytest -q tests
  ```
- Keep the UI text in Spanish and the documentation in English.
- Do not add vendor logos or brand assets; icons must stay original.
- Do not commit firmware binaries, logs, `.venv` or `build/`.

## Commit messages

Describe what changes, not "update": `trezor: show backup state`,
`bitbox: fix pairing dialog`, `docs: verify release signatures`.

## Signing

Tags are signed with the key in `docs/KEY.asc`.
Contributors do not need to sign their commits.
