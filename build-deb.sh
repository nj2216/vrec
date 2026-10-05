#!/usr/bin/env bash
# Build dist/vrec_<version>_all.deb   usage: ./build-deb.sh 1.0.0
set -euo pipefail

VERSION="${1:?usage: ./build-deb.sh <version>}"
PKG="vrec_${VERSION}_all"
ROOT="build/${PKG}"

rm -rf "$ROOT"
mkdir -p "$ROOT/DEBIAN" "$ROOT/usr/bin" "$ROOT/usr/share/applications" dist

install -m 755 vrec.py "$ROOT/usr/bin/vrec"

cat > "$ROOT/DEBIAN/control" <<EOF
Package: vrec
Version: ${VERSION}
Section: sound
Priority: optional
Architecture: all
Depends: python3, python3-gi, gir1.2-gtk-3.0, gir1.2-gstreamer-1.0, gstreamer1.0-plugins-base, gstreamer1.0-plugins-good
Maintainer: Jeevan <nj2216@users.noreply.github.com>
Description: Simple voice recorder
 Minimal GTK voice recorder with mic picker, live level meter,
 pause/resume and a recordings list.
EOF

cat > "$ROOT/usr/share/applications/vrec.desktop" <<EOF
[Desktop Entry]
Name=VRec
Comment=Simple voice recorder
Exec=vrec
Icon=audio-input-microphone
Terminal=false
Type=Application
Categories=AudioVideo;Audio;Recorder;
EOF

dpkg-deb --build --root-owner-group "$ROOT" "dist/${PKG}.deb"
echo "Built dist/${PKG}.deb"
