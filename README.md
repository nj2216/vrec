# vrec - Simple Voice Recorder

A small GTK3 voice recorder for Xubuntu/XFCE (works on any GTK3 Linux desktop).

- Mic picker and live level meter
- Pause / resume
- Recordings list with play, delete and open-folder
- Saves WAV files to `~/Recordings`

## Install (no sudo)

```bash
curl -fsSL https://raw.githubusercontent.com/nj2216/vrec/main/install.sh | bash
```

Installs the latest release to `~/.local/bin/vrec` and adds an app-menu entry.
Pin a version (or use the dev branch) with:

```bash
curl -fsSL https://raw.githubusercontent.com/nj2216/vrec/main/install.sh | VREC_REF=v1.1.0 bash
```

Uninstall:

```bash
curl -fsSL https://raw.githubusercontent.com/nj2216/vrec/main/install.sh | bash -s -- --uninstall
```

## Install the .deb

Download the `.deb` from the [Releases](https://github.com/nj2216/vrec/releases) page, then:

```bash
sudo apt install ./vrec_1.0.0_all.deb
```

## Requirements

`python3-gi gir1.2-gtk-3.0 gir1.2-gstreamer-1.0 gstreamer1.0-plugins-base gstreamer1.0-plugins-good`
(usually already present on Xubuntu).
