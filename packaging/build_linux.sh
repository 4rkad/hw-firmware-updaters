#!/usr/bin/env bash
# Build Linux artifacts: one AppImage and one .deb per app.
#
#   VERSION=0.1.0 ./packaging/build_linux.sh
#
# Python interpreters can be overridden:
#   PY_TREZOR=/path/to/python PY_LEDGER=... PY_BITBOX=... ./packaging/build_linux.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${VERSION:-0.1.0}"
ARCH="$(dpkg --print-architecture 2>/dev/null || echo amd64)"
BUILD="$ROOT/build"
DIST="$BUILD/dist"
APPIMAGE_DIST="$BUILD/appimage"
DEB_DIST="$BUILD/deb"
TOOLS="$BUILD/tools"

PY_TREZOR="${PY_TREZOR:-$ROOT/trezor/.venv/bin/python}"
PY_LEDGER="${PY_LEDGER:-$ROOT/ledger/.venv/bin/python}"
PY_BITBOX="${PY_BITBOX:-$ROOT/bitbox/.venv/bin/python}"

declare -A PY=([trezor]="$PY_TREZOR" [ledger]="$PY_LEDGER" [bitbox]="$PY_BITBOX")
declare -A ENTRY=([trezor]=trezor/trezor_qt.py [ledger]=ledger/ledger_qt.py [bitbox]=bitbox/bitbox_qt.py)
declare -A BACKEND=([trezor]=trezor/trezor-updater.py [ledger]=ledger/ledger-updater.py [bitbox]=bitbox/bitbox-updater.py)
declare -A LABEL=(
    [trezor]="Actualizador de firmware · Trezor (no oficial)"
    [ledger]="Actualizador de firmware · Ledger (no oficial)"
    [bitbox]="Actualizador de firmware · BitBox (no oficial)"
)
declare -A SUMMARY=(
    [trezor]="Updates Trezor firmware over USB without Trezor Suite"
    [ledger]="Updates Ledger firmware, apps and language over USB without Ledger Live"
    [bitbox]="Updates BitBox02/Nova firmware over USB without BitBoxApp"
)

mkdir -p "$DIST" "$APPIMAGE_DIST" "$DEB_DIST" "$TOOLS"

appimagetool() {
    local tool="$TOOLS/appimagetool"
    if [ ! -x "$tool" ]; then
        echo "· Descargando appimagetool"
        curl -fsSL -o "$tool" \
            "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
        chmod +x "$tool"
    fi
    APPIMAGE_EXTRACT_AND_RUN=1 "$tool" "$@"
}

udev_rule() {
    local key="$1"
    case "$key" in
        trezor)
            cat <<'EOF'
ACTION!="add|change", GOTO="hwfw_end"
KERNEL!="hidraw*", GOTO="hwfw_end"
ATTRS{idVendor}=="1209", TAG+="uaccess"
ATTRS{idVendor}=="534c", TAG+="uaccess"
LABEL="hwfw_end"
EOF
            ;;
        ledger)
            cat <<'EOF'
ACTION!="add|change", GOTO="hwfw_end"
KERNEL!="hidraw*", GOTO="hwfw_end"
ATTRS{idVendor}=="2c97", TAG+="uaccess"
LABEL="hwfw_end"
EOF
            ;;
        bitbox)
            cat <<'EOF'
ACTION!="add|change", GOTO="hwfw_end"
KERNEL!="hidraw*", GOTO="hwfw_end"
ATTRS{idVendor}=="03eb", ATTRS{idProduct}=="2403", TAG+="uaccess"
LABEL="hwfw_end"
EOF
            ;;
    esac
}

desktop_entry() {
    local key="$1"
    cat <<EOF
[Desktop Entry]
Type=Application
Name=${LABEL[$key]}
GenericName=Firmware updater
Comment=${SUMMARY[$key]}
Exec=hwfw-$key
TryExec=hwfw-$key
Icon=hwfw-$key
Terminal=false
Categories=Utility;
Keywords=firmware;bitcoin;hardware;
StartupNotify=true
EOF
}

build_pyinstaller() {
    local key="$1" py="${PY[$1]}"
    local dir="$DIST/hwfw-$key"
    if [ -x "$dir/hwfw-$key" ]; then
        echo "· $key: binario ya presente"
        return
    fi
    "$py" -m pip install -q pyinstaller
    local extra=()
    case "$key" in
        trezor) extra=(--collect-all trezorlib --copy-metadata slip10) ;;
        bitbox) extra=(--collect-all bitbox02 --hidden-import hid --hidden-import semver) ;;
        ledger) extra=(--collect-all cryptography --hidden-import hid --hidden-import websocket --collect-submodules websocket) ;;
    esac
    echo "· $key: PyInstaller"
    "$py" -m PyInstaller --noconfirm --clean --onedir \
        --name "hwfw-$key" \
        --paths "$ROOT/common" \
        --add-data "$ROOT/icons:icons" \
        --add-data "$ROOT/common/fonts:common/fonts" \
        --add-data "$ROOT/${BACKEND[$key]}:." \
        "${extra[@]}" \
        --distpath "$DIST" --workpath "$BUILD/work" --specpath "$BUILD" \
        "$ROOT/${ENTRY[$key]}" >"$BUILD/pyinstaller-$key.log" 2>&1
}

build_appimage() {
    local key="$1"
    local appdir="$BUILD/AppDir-$key"
    local out="$APPIMAGE_DIST/hwfw-$key-$VERSION-x86_64.AppImage"
    rm -rf "$appdir"
    mkdir -p "$appdir"
    cp -a "$DIST/hwfw-$key/." "$appdir/"
    cp "$ROOT/icons/$key.png" "$appdir/hwfw-$key.png"
    ln -sf "hwfw-$key.png" "$appdir/.DirIcon"
    desktop_entry "$key" > "$appdir/hwfw-$key.desktop"
    cat > "$appdir/AppRun" <<EOF
#!/bin/sh
HERE="\$(dirname "\$(readlink -f "\$0")")"
exec "\$HERE/hwfw-$key" "\$@"
EOF
    chmod +x "$appdir/AppRun"
    rm -f "$out"
    echo "· $key: AppImage"
    appimagetool "$appdir" "$out" >/dev/null
}

build_deb() {
    local key="$1"
    local pkg="hwfw-$key"
    local stage="$BUILD/debroot-$key"
    local out="$DEB_DIST/${pkg}_${VERSION}_${ARCH}.deb"
    rm -rf "$stage"
    mkdir -p "$stage/DEBIAN" \
             "$stage/opt/hwfw/$key" \
             "$stage/usr/bin" \
             "$stage/usr/share/applications" \
             "$stage/usr/share/icons/hicolor/512x512/apps" \
             "$stage/lib/udev/rules.d"
    cp -a "$DIST/$pkg/." "$stage/opt/hwfw/$key/"
    desktop_entry "$key" > "$stage/usr/share/applications/$pkg.desktop"
    cp "$ROOT/icons/$key.png" "$stage/usr/share/icons/hicolor/512x512/apps/$pkg.png"
    udev_rule "$key" > "$stage/lib/udev/rules.d/60-$pkg.rules"
    cat > "$stage/usr/bin/$pkg" <<EOF
#!/bin/sh
exec /opt/hwfw/$key/hwfw-$key "\$@"
EOF
    chmod +x "$stage/usr/bin/$pkg"
    cat > "$stage/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if command -v udevadm >/dev/null 2>&1; then
    udevadm control --reload-rules || true
    udevadm trigger --subsystem-match=hidraw || true
fi
exit 0
EOF
    cp "$stage/DEBIAN/postinst" "$stage/DEBIAN/postrm"
    chmod +x "$stage/DEBIAN/postinst" "$stage/DEBIAN/postrm"
    cat > "$stage/DEBIAN/control" <<EOF
Package: $pkg
Version: $VERSION
Section: utils
Priority: optional
Architecture: $ARCH
Depends: libc6, libstdc++6, libgl1, libegl1, libxkbcommon0, libxkbcommon-x11-0, libdbus-1-3, libfontconfig1, libglib2.0-0, libxcb-cursor0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, libxcb-randr0, libxcb-render0, libxcb-render-util0, libxcb-shape0, libxcb-shm0, libxcb-sync1, libxcb-util1, libxcb-xfixes0, libxcb-xkb1, libxcb1, libx11-6, libx11-xcb1
Maintainer: Arkad <55397917+4rkad@users.noreply.github.com>
Homepage: https://github.com/4rkad/hw-firmware-updaters
Description: ${SUMMARY[$key]}
 Unofficial. Not affiliated with or endorsed by the hardware vendor.
EOF
    (cd "$stage" && find . -type f -print0 | xargs -0 md5sum > DEBIAN/md5sums)
    echo "· $key: .deb"
    rm -f "$out"
    dpkg-deb --build --root-owner-group "$stage" "$out" >/dev/null
}

for key in trezor ledger bitbox; do
    build_pyinstaller "$key"
    build_appimage "$key"
    build_deb "$key"
done

(cd "$APPIMAGE_DIST" && sha256sum ./*.AppImage > SHA256SUMS)
(cd "$DEB_DIST" && sha256sum ./*.deb > SHA256SUMS)
echo
echo "Listo:"
ls -lh "$APPIMAGE_DIST" "$DEB_DIST"
