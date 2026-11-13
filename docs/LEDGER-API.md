# Ledger API dependency

The Ledger backend does not use Ledger Live, but it depends on the same public
services that Ledger Live uses. Those services are not a versioned contract and
can change.

## Services used

| Service | Purpose |
|---|---|
| `manager.api.live.ledger.com/api` | device versions, firmware/OSU metadata, apps, language packs |
| `scriptrunner.api.live.ledger.com/update` | secure-channel command transport |
| `download.languages.ledger.com` | language pack data |

Requests include a `livecommonversion` parameter. The default is defined by
`LIVE_COMMON_VERSION` in `ledger/ledger-updater.py` (currently `38.6.0`) and can
be overridden at runtime:

```bash
LIVE_COMMON_VERSION=40.0.0 ./ledger/run.sh
```

## What breaks

- A new `livecommonversion` requirement (HTTP 400/422, missing fields).
- Renamed or removed endpoints, or a changed response schema.
- Changes in the secure-channel protocol or OSU parameters.

The application fails before flashing if it cannot build a consistent update
plan. It does not write partial firmware on an API error.

## Detection

`ledger/ledger-updater.py --selftest` queries the API without a device and
validates firmware, OSU, app catalogs and update routes for every known model.

A scheduled GitHub Actions workflow runs it every Monday:

`.github/workflows/ledger-api.yml`

A failure sends the standard GitHub Actions notification and marks the
workflow red. It can also be run manually:

```bash
gh workflow run ledger-api.yml
```

## Updating

1. Check the failing endpoint and the HTTP status in the workflow log.
2. If Ledger bumped `livecommonversion`, update the default in
   `ledger/ledger-updater.py` (or test with the environment variable first).
3. If an endpoint or schema changed, update `api_get`/`http_post_json` and the
   affected parser. Keep the change isolated in `ledger-updater.py`.
4. Run `--selftest` and the backend tests.
5. Confirm an update plan with a real device before publishing a release.

## Transport hardening

- TLS uses an explicit context: `CERT_REQUIRED`, hostname verification and
  TLS 1.2 minimum.
- Optional SPKI pinning:

  ```bash
  LEDGER_SPKI_PINS=<sha256>,<sha256> ./ledger/run.sh
  ```

  Pins are SHA-256 of the DER SubjectPublicKeyInfo. Ledger rotates its
  certificates roughly every 90 days, so a leaf pin must be updated on
  rotation; leave it unset unless you are prepared to maintain it.
- The update plan refuses a version older than the installed firmware, which
  limits what a manipulated metadata response can achieve.
- Genuine checks require the local attestation (challenge, certificate chain
  and proof of possession). The online HSM result is advisory and is not shown
  as genuine on its own.

## Fallback

If the API is temporarily broken, use Ledger Live for that update. This
application will keep reporting the API error until it is adapted; it does not
fall back to unverified firmware or third-party mirrors.

The offline genuine check (`Verificar offline`) does not use the manager API.
It verifies the device certificate against known issuer keys; an unrecognized
model is reported as unverified, not as counterfeit.
