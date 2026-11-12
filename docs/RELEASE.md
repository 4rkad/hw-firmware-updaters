# Release Process

Linux releases are built in CI, signed locally, and published as GitHub
releases.

## 1. Create a signed tag

```bash
git tag -s vX.Y.Z -m "vX.Y.Z — short description"
git push origin refs/tags/vX.Y.Z
```

Tags are signed with the maintainer key (fingerprint in `README.md`). Pushing
the tag starts `.github/workflows/linux-build.yml`, which:

1. installs dependencies and runs the linter;
2. runs backend tests;
3. builds one AppImage and one `.deb` per application;
4. smoke-tests each packaged application headless;
5. attests build provenance;
6. uploads the artifacts.

## 2. Download and verify the artifacts

```bash
gh run download <run-id> -R 4rkad/hw-firmware-updaters \
  -n linux-<run-id> -D /tmp/release
cd /tmp/release
sha256sum -c release/SHA256SUMS
```

## 3. Sign the checksums

CI does not hold the signing key. Sign the checksum file on a machine with the
hardware token:

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

## 5. Verification by users

```bash
gpg --import docs/KEY.asc
gpg --verify SHA256SUMS.asc SHA256SUMS
sha256sum -c SHA256SUMS
gh attestation verify hwfw-trezor-X.Y.Z-x86_64.AppImage \
  --repo 4rkad/hw-firmware-updaters
git tag -v vX.Y.Z
```

## Reproducibility

`packaging/build_linux.sh` sets `PYTHONHASHSEED=0`, pins PyInstaller and the
static AppImage runtime by SHA-256, and normalises squashfs timestamps. It
requires `squashfs-tools`. With identical Python and dependency versions,
rebuilding a tag produces byte-identical files. CI rebuilds the AppImage and
compares the hash. This was verified on `v0.1.0` (487/487 files, including a build from a
different directory) and on the AppImage output.

## Packaging requirements

Every package must include `LICENSE`, `NOTICE`, `THIRD_PARTY.md`,
`TRADEMARKS.md` and `licenses/`. Debian packages also require a `copyright`
file. The build script handles this.
