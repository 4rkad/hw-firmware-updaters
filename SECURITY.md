# Security Policy

## Supported versions

Security fixes are applied to the latest release only.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting: **Security → Advisories → Report a
vulnerability** in this repository. Do not open a public issue for an
unpatched vulnerability.

Include the affected application and version, device model and firmware, the
steps to reproduce, and any relevant sanitized log lines. Never include seeds,
private keys, pairing codes, xpubs, or addresses.

## Scope

In scope:

- Application code and backends in this repository.
- Packaging, build scripts, and release workflow.
- Signature and checksum verification logic.

Out of scope:

- Vendor firmware and vendor libraries.
- Vulnerabilities in the devices themselves.
- Physical attacks against the hardware wallet.

## Flashing risk

This software writes firmware to hardware devices. An interruption (cable,
port, power loss, forced termination) can render a device unusable. Keep the
device connected directly and do not interrupt an operation.

## Data handling

- No telemetry or device information is sent to third parties.
- Logs are written to `~/.cache/<app>.log` and may contain the device name and
  pairing codes. Do not publish them.
- BitBox pairing keys are stored locally in the same cache directory.
