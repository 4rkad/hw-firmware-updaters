# Contributing

Focused changes are welcome.

## Reporting issues

Include the application (Trezor, Ledger, or BitBox), version, device model and
firmware, and the relevant sanitized log lines. Never include seeds, private
keys, pairing codes, xpubs, or addresses.

## Pull requests

- Keep each change limited to one application or one concern.
- Do not merge vendor-specific flashing logic; the three backends remain
  independent.
- Run the checks before opening a pull request:

  ```bash
  ruff check trezor ledger bitbox common docs/make_screenshots.py
  cd ledger && python -m pytest -q tests
  ```

- User interface text is Spanish; documentation is English.
- Do not add vendor logos or brand assets. Icons must remain original.
- Do not commit firmware binaries, logs, virtual environments, or build output.

## Commit messages

Describe the change precisely:

```
trezor: show backup state after verification
bitbox: fix pairing dialog lifetime
docs: correct release verification steps
```

Avoid generic messages such as `update` or `fixes`.

## Signing

Release tags are signed with the key published in `docs/KEY.asc`. Contributors
do not need to sign commits.
