#!/usr/bin/env bash
# Voice Recorder (vrec) installer - per-user, no sudo needed.
#
#   curl -fsSL https://raw.githubusercontent.com/nj2216/vrec/main/install.sh | bash
#
# Uninstall:
#   curl -fsSL https://raw.githubusercontent.com/nj2216/vrec/main/install.sh | bash -s -- --uninstall
#
# Pin a version:  VREC_REF=v1.0.0 curl -fsSL ... | bash
set -euo pipefail

REPO="nj2216/vrec"
REF="${VREC_REF:-main}"
BIN_DIR="${HOME}/.local/bin"
APP_DIR="${HOME}/.local/share/applications"
BIN="${BIN_DIR}/vrec"
DESKTOP="${APP_DIR}/vrec.desktop"
URL="https://raw.githubusercontent.com/${REPO}/${REF}/vrec.py"

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

if [[ "${1:-}" == "--uninstall" ]]; then
    rm -f "$BIN" "$DESKTOP"
    command -v update-desktop-database >/dev/null && update-desktop-database "$APP_DIR" 2>/dev/null || true
    say "Removed vrec. Your recordings in ~/Recordings were left untouched."
    exit 0
fi

# --- dependency check (python3 -c so we never read from the piped stdin)
command -v python3 >/dev/null || die "python3 not found."

if ! python3 -c "
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gst', '1.0')
from gi.repository import Gtk, Gst
" 2>/dev/null; then
    cat >&2 <<'MSG'
error: missing Python GTK3 / GStreamer bindings.
Ask an admin to install (or run with sudo):

    sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-gstreamer-1.0 \
                     gstreamer1.0-plugins-base gstreamer1.0-plugins-good

then run this installer again.
MSG
    exit 1
fi

# --- download
mkdir -p "$BIN_DIR" "$APP_DIR"
say "Downloading vrec (${REF})..."
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
if command -v curl >/dev/null; then
    curl -fsSL "$URL" -o "$tmp" || die "download failed: $URL"
elif command -v wget >/dev/null; then
    wget -qO "$tmp" "$URL" || die "download failed: $URL"
else
    die "need curl or wget."
fi
head -n1 "$tmp" | grep -q python || die "downloaded file doesn't look right."

install -m 755 "$tmp" "$BIN"

# --- menu entry
cat > "$DESKTOP" <<EOF
[Desktop Entry]
Name=Voice Recorder
Comment=Simple voice recorder
Exec=${BIN}
Icon=audio-input-microphone
Terminal=false
Type=Application
Categories=AudioVideo;Audio;Recorder;
EOF
command -v update-desktop-database >/dev/null && update-desktop-database "$APP_DIR" 2>/dev/null || true

say "Installed to ${BIN}"
say "Launch from the app menu (Multimedia) or run: vrec"
case ":${PATH}:" in
    *":${BIN_DIR}:"*) ;;
    *) say "Note: ${BIN_DIR} isn't on your PATH. Add this to ~/.bashrc:  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac
