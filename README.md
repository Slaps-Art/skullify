# Skullify

Skullify is a Linux-first Spotify CLI/TUI with search, playlists, Spotify Connect device selection, playback controls, selectable ASCII art, and a PulseAudio/PipeWire visualizer.

## Install

Ubuntu/Debian:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip pipx pulseaudio-utils
pipx ensurepath
pipx install --force git+https://github.com/Slaps-Art/skullify.git
unalias skullify 2>/dev/null || true
hash -r
skullify --setup
```

Other distros need Python 3, `pipx`, and PulseAudio/PipeWire tools that provide `pactl` and `parec`.

- Fedora: `sudo dnf install -y python3 python3-pip pipx pulseaudio-utils`
- Arch: `sudo pacman -S --needed python python-pipx pulseaudio-utils`
- openSUSE: `sudo zypper install python3 python3-pipx pulseaudio-utils`
- Alpine: `sudo apk add python3 py3-pip pipx pulseaudio-utils`

Update:

```bash
pipx install --force git+https://github.com/Slaps-Art/skullify.git
hash -r
skullify --version
```

## First Setup

Run:

```bash
skullify --setup
```

Create a Spotify app when prompted and add this Redirect URI exactly:

```text
http://127.0.0.1:8888/callback
```

Paste your Spotify Client ID into Skullify. Press `Enter` to accept bracketed defaults such as the redirect URI. Skullify uses PKCE, so you do not need a client secret.

For terminal playback, choose `librespot` or `spotifyd` if you have one installed. Choose `none` to use an existing Spotify desktop, web, or mobile player.

## Usage

```bash
skullify
skullify "Daft Punk One More Time"
skullify --devices
skullify --doctor
skullify --logout
skullify --version
```

Useful TUI keys:

- `s` or `/`: search
- `p`: playlists
- `a`: ASCII art picker
- `Enter`: open, play, or save selection
- `Space`: play/pause
- `<` / `>`: previous/next
- `d`: devices
- `v`: visualizer
- `q`: quit

## ASCII Art

Skullify ships with `jellyfish`, `skull`, and `skullify-wordmark`.

Inside the TUI, press `a` or select `ASCII art and animations` from Home.

- `Up` / `Down`: live preview highlighted art
- `Enter`: save highlighted art as the default for next launch
- `s`: search by name or tag
- `p`: open a full-frame preview
- `a`: show all art

Terminal commands:

```bash
skullify --list-ascii
skullify --search-ascii skull
skullify --preview-ascii skull
skullify --set-ascii skull
skullify --ascii jellyfish
```

Licensed local packs can be placed in `~/.config/skullify/ascii/` as `.json`, `.js`, `.txt`, or `.asc` files.

## Art Credits

The jellyfish animation is by [Saida Magic](https://www.instagram.com/saidamagic/).

Jellyfish art licensing details are still being finalized. Do not reuse or redistribute the jellyfish art outside Skullify without permission from the artist. The MIT license applies to Skullify code and project-owned assets, not automatically to third-party art.

## Development

```bash
git clone https://github.com/Slaps-Art/skullify.git
cd skullify
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
skullify --setup
```

## Troubleshooting

Run `skullify --doctor`.

Common fixes:

- Missing Spotify Client ID: run `skullify --setup`.
- Redirect URI mismatch: add `http://127.0.0.1:8888/callback` to your Spotify app.
- Old local command shadows pipx: run `type -a skullify`, remove stale aliases or wrappers, then `hash -r`.
- No Spotify Connect devices: start Spotify elsewhere, install a terminal player, or choose a device with `skullify --devices`.
- Visualizer unavailable: install `pulseaudio-utils` and confirm `pactl` and `parec` are available.

## Privacy

Skullify does not ship Spotify credentials. Each user creates their own Spotify app.

Local files:

- Config: `~/.config/skullify/config.json`
- Token cache: `~/.cache/skullify/spotify-token-cache.json`
- Logs/state: `~/.local/state/skullify/`
- Local ASCII packs: `~/.config/skullify/ascii/`

Do not publish `.env` files, token caches, logs, local config, or screenshots with account data.

## Maintainer Checks

Before publishing:

```bash
scripts/secret_scan.sh
python3 -m py_compile skullify/cli.py skullify/__main__.py skullify/__init__.py
rg --files -uu
```
