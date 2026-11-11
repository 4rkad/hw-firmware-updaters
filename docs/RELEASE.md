# Release process

Linux releases are built by CI, signed locally, and published as GitHub
releases.

## 1. Tag

```bash
git tag -s vX.Y.Z -m "vX.Y.Z — short description"
git push origin refs/tags/vX.Y.Z
```

The tag must be signed with the maintainer key (fingerprints in `README.md`).
Pushing the tag starts `.github/workflows/linux-build.yml`, which builds,
runs backend tests, smoke-tests each app headless, attests provenance, and
uploads the artifacts.

## 2. Artifacts

Download the CI artifact (`linux-<run-id>`) and verify its checksums:

```bash
gh run download <run-id> -R 4rkad/hw-firmware-updaters -n linux-<run-id> -D /tmp/release
cd /tmp/release && sha256sum -c release/SHA256SUMS
```

## 3. Sign

The CI never holds the signing key. Sign the checksum file on a machine with
the hardware token:

```bash
gpg --armor --detach-sign SHA256SUMS
gpg --verify SHA256SUMS.asc SHA256SUMS
```

## 4. Publish

```bash
gh release create vX.Y.Z \
  *.AppImage *.deb SHA256SUMS SHA256SUMS.asc \
  --title "vX.Y.Z — short description" --notes "..."
```

## 5. Verify as a user

```bash
gpg --import docs/KEY.asc
gpg --verify SHA256SUMS.asc SHA256SUMS
sha256sum -c SHA256SUMS
gh attestation verify hwfw-trezor-X.Y.Z-x86_64.AppImage --repo 4rkad/hw-firmware-updaters
git tag -v vX.Y.Z
```

## Reproducibility

`packaging/build_linux.sh` sets `PYTHONHASHSEED=0` and pins PyInstaller.
Given the same Python and dependency versions, rebuilding a tag produces
byte-identical files. This was verified on `v0.1.0`: 487/487 files matched,
including a build from a different directory.

## Licenses and trademarks

Packages include `LICENSE`, `NOTICE`, `THIRD_PARTY.md`, `TRADEMARKS.md` and
`licenses/`. Keep those files in every release. See `TRADEMARKS.md` before
using any vendor name or asset.
