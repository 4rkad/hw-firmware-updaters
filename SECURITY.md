# Security

## Flashing risk

This software writes firmware to hardware devices. An interruption (cable,
port, power loss, forced quit) can brick the device. Use it at your own risk and
keep the device connected directly.

## What the app verifies

- SHA-256 of the downloaded binary against the vendor's published value.
- Firmware signature/magic and that it matches the device edition.
- Device attestation when the vendor provides it.

If any check fails, the app aborts and records it in the log.

## Data

- No telemetry or device information is sent to third parties.
- BitBox pairing keys are stored locally in `~/.cache`.
- Logs are written to `~/.cache/<app>.log`. They can contain the device name and
  pairing codes: do not publish them.

## Reporting

Open an issue with the app version, device model and firmware, and the steps to
reproduce. Do not include seeds, private keys or pairing codes.
