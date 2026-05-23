#!/usr/bin/env python3
"""Skullify's command-line entry point and terminal UI."""

import argparse, curses, os, time, locale, shutil, subprocess, threading, re, shlex, socket, sys, math, json, importlib.util
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

try:
    from . import __version__
except Exception:
    __version__ = "0.2.4"

try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth, SpotifyPKCE
    from spotipy.exceptions import SpotifyException
except Exception:
    spotipy = None
    SpotifyOAuth = SpotifyPKCE = None

    class SpotifyException(Exception):
        http_status = None

# ---- Real-time Visualizer tuning (env overridable) ----
def _envf(name, default):
    try:    return float(os.environ.get(name, str(default)))
    except: return default

def _envi(name, default):
    try:    return int(os.environ.get(name, str(default)))
    except: return default

VIZ_FPS_DEFAULT  = _envf("SKULLIFY_VIZ_FPS", 50.0)
VIZ_INTERVAL     = 1.0 / max(5.0, VIZ_FPS_DEFAULT)
VIZ_BAR_W        = _envi("SKULLIFY_VIZ_BAR_W", 2)
VIZ_MAX_BARS     = _envi("SKULLIFY_VIZ_MAX_BARS", 56)

VIZ_CAPTURE_RATE = _envi("SKULLIFY_VIZ_RATE", 48000)
VIZ_PAREC_MS     = _envi("SKULLIFY_VIZ_PAREC_MS", 8)
VIZ_READ_MS      = _envf("SKULLIFY_VIZ_READ_MS", 8.0)
VIZ_BUFFER_MS    = _envi("SKULLIFY_VIZ_BUFFER_MS", 250)

VIZ_ATTACK_TC    = _envf("SKULLIFY_VIZ_ATTACK_TC", 0.020)  # fast rise
VIZ_RELEASE_TC   = _envf("SKULLIFY_VIZ_RELEASE_TC", 0.18)  # slower fall
VIZ_GAMMA        = _envf("SKULLIFY_VIZ_GAMMA", 1.15)

VIZ_BEAT_BOOST   = _envf("SKULLIFY_VIZ_BEAT_BOOST", 0.30)
VIZ_BEAT_DECAY_TC= _envf("SKULLIFY_VIZ_BEAT_DECAY_TC", 0.18)
VIZ_HORIZONTAL_SMOOTH = _envf("SKULLIFY_VIZ_HORIZONTAL_SMOOTH", 0.10)
VIZ_ENVELOPE_FILL     = _envf("SKULLIFY_VIZ_ENVELOPE_FILL", 0.66)
VIZ_MAX_DROP          = _envf("SKULLIFY_VIZ_MAX_DROP", 0.26)
VIZ_ENVELOPE_RADIUS   = _envi("SKULLIFY_VIZ_ENVELOPE_RADIUS", 2)

def _sleep_until_next_frame(next_frame_at: float, frame_interval: float) -> float:
    now = time.monotonic()
    if next_frame_at <= now - frame_interval or next_frame_at > now + frame_interval:
        next_frame_at = now + frame_interval
    else:
        next_frame_at += frame_interval
    delay = next_frame_at - time.monotonic()
    if delay > 0:
        time.sleep(delay)
    return next_frame_at

def ease_cubic_in(x):   return x*x*x
def ease_cubic_out(x):  return 1.0 - (1.0 - x)**3
def ease_cubic_inout(x):
    return 4*x*x*x if x < 0.5 else 1.0 - ((-2*x + 2)**3) / 2.0

def smooth_visualizer_levels(levels):
    if np is None:
        return levels
    arr = np.asarray(levels, dtype=np.float32)
    if arr.size < 3:
        return np.nan_to_num(arr, nan=0.0, posinf=1.0, neginf=0.0).clip(0.0, 1.0)

    arr = np.nan_to_num(arr, nan=0.0, posinf=1.0, neginf=0.0).clip(0.0, 1.0)

    padded = np.pad(arr, (1, 1), mode="edge")
    neighbor_blend = (padded[:-2] + arr * 2.0 + padded[2:]) * 0.25
    smooth_amount = clamp(float(VIZ_HORIZONTAL_SMOOTH), 0.0, 1.0)
    arr = np.maximum(arr, neighbor_blend * smooth_amount + arr * (1.0 - smooth_amount))

    envelope = arr.copy()
    radius = max(1, min(8, int(VIZ_ENVELOPE_RADIUS)))
    for offset in range(1, radius + 1):
        left = np.r_[arr[:1].repeat(offset), arr[:-offset]]
        right = np.r_[arr[offset:], arr[-1:].repeat(offset)]
        envelope = np.maximum(envelope, np.maximum(left, right))
    fill = clamp(float(VIZ_ENVELOPE_FILL), 0.0, 1.0)
    arr = np.maximum(arr, envelope * fill)

    max_drop = clamp(float(VIZ_MAX_DROP), 0.0, 1.0)
    for i in range(1, arr.size):
        arr[i] = max(arr[i], arr[i - 1] - max_drop)
    for i in range(arr.size - 2, -1, -1):
        arr[i] = max(arr[i], arr[i + 1] - max_drop)

    return arr.clip(0.0, 1.0)

def fill_visualizer_height_gaps(heights: List[int], max_h: int) -> List[int]:
    if len(heights) < 3:
        return heights
    filled = list(heights)

    for i in range(1, len(heights) - 1):
        neighbor_floor = min(heights[i - 1], heights[i + 1])
        if neighbor_floor >= 3 and heights[i] <= neighbor_floor - 3:
            filled[i] = max(filled[i], min(max_h, neighbor_floor - 1))

    for i in range(1, len(heights) - 2):
        neighbor_floor = min(heights[i - 1], heights[i + 2])
        valley_peak = max(heights[i], heights[i + 1])
        if neighbor_floor >= 4 and valley_peak <= neighbor_floor - 4:
            fill = min(max_h, neighbor_floor - 2)
            filled[i] = max(filled[i], fill)
            filled[i + 1] = max(filled[i + 1], fill)

    return filled

try:
    import numpy as np
except Exception:
    np = None

locale.setlocale(locale.LC_ALL, "")

APP_NAME = "skullify"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8888/callback"
SPOTIFY_DASHBOARD_URL = "https://developer.spotify.com/dashboard"
PROJECT_INSTALL_URL = "git+https://github.com/Slaps-Art/skullify.git"
PYTHON_DEPENDENCIES = ("spotipy", "requests", "numpy", "sounddevice")

def _xdg_path(env_name: str, default: Path) -> Path:
    raw = os.environ.get(env_name, "").strip()
    return Path(os.path.expanduser(raw)) if raw else default

CONFIG_DIR = _xdg_path("XDG_CONFIG_HOME", Path.home() / ".config") / APP_NAME
CACHE_DIR = _xdg_path("XDG_CACHE_HOME", Path.home() / ".cache") / APP_NAME
STATE_DIR = _xdg_path("XDG_STATE_HOME", Path.home() / ".local" / "state") / APP_NAME
ASCII_DIR = CONFIG_DIR / "ascii"
CONFIG_PATH = _xdg_path("SKULLIFY_CONFIG", CONFIG_DIR / "config.json")
TOKEN_CACHE_PATH = _xdg_path("SKULLIFY_TOKEN_CACHE", CACHE_DIR / "spotify-token-cache.json")
DEFAULT_TERMINAL_LOG = STATE_DIR / "terminal-player.log"
MAX_LOG_BYTES = int(os.environ.get("SKULLIFY_LOG_MAX_BYTES", str(1024 * 1024)))

def _ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass

def _load_config() -> Dict[str, Any]:
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}

def _save_config(config: Dict[str, Any]) -> None:
    _ensure_private_dir(CONFIG_PATH.parent)
    tmp = CONFIG_PATH.with_suffix(CONFIG_PATH.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, sort_keys=True)
        f.write("\n")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(CONFIG_PATH)

LOCAL_CONFIG = _load_config()

def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")

def _config_str(key: str, env_name: str, default: str = "") -> str:
    raw = os.environ.get(env_name)
    if raw is not None:
        return raw.strip()
    return str(LOCAL_CONFIG.get(key, default) or "").strip()

def _redact_text(text: str) -> str:
    if not text:
        return text
    home = str(Path.home())
    if home and home != "/":
        text = text.replace(home, "~")
    text = re.sub(r"(BQB|AQ)[A-Za-z0-9_-]{20,}", "<redacted-token>", text)
    text = re.sub(r"(?i)(token|secret|password|client_secret)=([^ \t]+)", r"\1=<redacted>", text)
    return text

def _redacted_command(cmd: List[str]) -> str:
    redacted: List[str] = []
    redact_next = False
    for part in cmd:
        lowered = part.lower()
        if redact_next:
            redacted.append("<redacted>")
            redact_next = False
            continue
        if any(word in lowered for word in ("token", "secret", "password")):
            if "=" in part:
                name = part.split("=", 1)[0]
                redacted.append(f"{name}=<redacted>")
            else:
                redacted.append(part)
                redact_next = True
            continue
        redacted.append(_redact_text(part))
    return " ".join(shlex.quote(part) for part in redacted)

def _rotate_log(path: Path) -> None:
    try:
        if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
            rotated_log = path.with_suffix(path.suffix + ".1")
            if rotated_log.exists():
                rotated_log.unlink()
            path.replace(rotated_log)
    except OSError:
        pass

def _prompt_value(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"{prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return value or default

def _prompt_bool(prompt: str, default: bool = True) -> bool:
    label = "Y/n" if default else "y/N"
    try:
        raw = input(f"{prompt} [{label}]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    if not raw:
        return default
    return raw.startswith("y")

def _module_available(name: str) -> bool:
    if name == "spotipy":
        return spotipy is not None
    if name == "numpy":
        return np is not None
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False

def _read_os_release() -> Dict[str, str]:
    data: Dict[str, str] = {}
    try:
        with open("/etc/os-release", "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                data[key] = value.strip().strip('"')
    except OSError:
        pass
    return data

def _linux_family(os_release: Optional[Dict[str, str]] = None) -> str:
    os_release = os_release or _read_os_release()
    tokens = " ".join(
        (os_release.get("ID", ""), os_release.get("ID_LIKE", ""), os_release.get("NAME", ""))
    ).lower()
    if any(token in tokens for token in ("debian", "ubuntu", "pop", "mint")):
        return "debian"
    if any(token in tokens for token in ("fedora", "rhel", "centos", "rocky", "alma")):
        return "fedora"
    if any(token in tokens for token in ("arch", "manjaro", "endeavouros")):
        return "arch"
    if any(token in tokens for token in ("opensuse", "suse")):
        return "opensuse"
    if "alpine" in tokens:
        return "alpine"
    return "generic"

def _dependency_status() -> Dict[str, Any]:
    os_release = _read_os_release()
    return {
        "os_release": os_release,
        "family": _linux_family(os_release),
        "python_packages": {name: _module_available(name) for name in PYTHON_DEPENDENCIES},
        "tools": {
            "python3": shutil.which("python3") is not None,
            "pipx": shutil.which("pipx") is not None,
            "pactl": shutil.which("pactl") is not None,
            "parec": shutil.which("parec") is not None,
            "librespot": shutil.which("librespot") is not None,
            "spotifyd": shutil.which("spotifyd") is not None,
        },
    }

def _missing_python_packages(status: Optional[Dict[str, Any]] = None) -> List[str]:
    status = status or _dependency_status()
    return [name for name, present in status["python_packages"].items() if not present]

def _install_commands(family: str) -> List[str]:
    commands = {
        "debian": [
            "sudo apt update",
            "sudo apt install -y python3 python3-venv python3-pip pipx pulseaudio-utils",
            "pipx ensurepath",
            f"pipx install --force {PROJECT_INSTALL_URL}",
        ],
        "fedora": [
            "sudo dnf install -y python3 python3-pip pipx pulseaudio-utils",
            "pipx ensurepath",
            f"pipx install --force {PROJECT_INSTALL_URL}",
        ],
        "arch": [
            "sudo pacman -S --needed python python-pipx pulseaudio-utils",
            "pipx ensurepath",
            f"pipx install --force {PROJECT_INSTALL_URL}",
        ],
        "opensuse": [
            "sudo zypper install python3 python3-pipx pulseaudio-utils",
            "pipx ensurepath",
            f"pipx install --force {PROJECT_INSTALL_URL}",
        ],
        "alpine": [
            "sudo apk add python3 py3-pip pipx pulseaudio-utils",
            "pipx ensurepath",
            f"pipx install --force {PROJECT_INSTALL_URL}",
        ],
        "generic": [
            "python3 -m venv .venv",
            ". .venv/bin/activate",
            "python -m pip install -e .",
        ],
    }
    return commands.get(family, commands["generic"])

def _print_check(name: str, present: bool, detail: str = "") -> None:
    marker = "OK" if present else "missing"
    suffix = f" - {detail}" if detail else ""
    print(f"  {name}: {marker}{suffix}")

def _all_command_paths(command: str) -> List[str]:
    paths: List[str] = []
    seen = set()
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory:
            continue
        candidate = os.path.join(os.path.expanduser(directory), command)
        if candidate in seen:
            continue
        seen.add(candidate)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            paths.append(candidate)
    return paths

def _print_setup_summary(status: Optional[Dict[str, Any]] = None) -> None:
    status = status or _dependency_status()
    os_release = status["os_release"]
    distro_name = os_release.get("PRETTY_NAME") or os_release.get("NAME") or "Unknown Linux"
    print(f"System: {distro_name}")
    print("Python packages:")
    for name, present in status["python_packages"].items():
        _print_check(name, present)
    print("System tools:")
    _print_check("pipx", status["tools"]["pipx"], "recommended installer")
    _print_check("pactl", status["tools"]["pactl"], "audio device detection")
    _print_check("parec", status["tools"]["parec"], "audio visualizer")
    terminal_ok = status["tools"]["librespot"] or status["tools"]["spotifyd"]
    _print_check("librespot or spotifyd", terminal_ok, "terminal Spotify Connect playback")

def _print_install_guidance(status: Optional[Dict[str, Any]] = None) -> None:
    status = status or _dependency_status()
    family = status["family"]
    print()
    print("Suggested install commands")
    print("Skullify will not run these commands for you.")
    for command in _install_commands(family):
        print(f"  {command}")
    print()
    print("Install or update Skullify from GitHub main:")
    print(f"  pipx install --force {PROJECT_INSTALL_URL}")
    print("  unalias skullify 2>/dev/null || true")
    print("  hash -r")
    print("  skullify --version")
    print()
    print("If the wrong command runs after pipx installs Skullify:")
    print("  type -a skullify")
    print("  alias skullify 2>/dev/null || true")
    print("  ~/.local/bin/skullify --version")
    print("  unalias skullify 2>/dev/null || true")
    print("  mkdir -p ~/skullify-legacy-launchers")
    print("  mv ~/bin/skullify* ~/skullify-legacy-launchers/ 2>/dev/null || true")
    print("  hash -r")
    print("  skullify --version")
    print()
    print("Optional terminal playback")
    if family == "debian":
        print("  Ubuntu/Debian repositories may not include spotifyd or librespot.")
        print("  Choose 'none' during setup, or install a terminal Spotify Connect player from a trusted package source.")
    elif family == "alpine":
        print("  Install spotifyd or librespot from a trusted package source if you want terminal playback.")
    elif family == "generic":
        print("  Also install PulseAudio/PipeWire tools and either librespot or spotifyd with your system package manager.")
    else:
        print("  Install spotifyd or librespot separately if you want Skullify to launch a terminal player.")

def _open_url(url: str) -> bool:
    try:
        import webbrowser as _webbrowser
        return bool(_webbrowser.open(url))
    except Exception:
        return False

def show_runtime_setup_guidance() -> int:
    status = _dependency_status()
    print("Skullify is not fully installed yet.")
    print()
    _print_setup_summary(status)
    _print_install_guidance(status)
    print()
    print("After installing, run `skullify --setup` to configure Spotify.")
    return 1

def run_setup() -> int:
    config = dict(LOCAL_CONFIG)
    print("Skullify first-run setup")
    print()
    status = _dependency_status()
    _print_setup_summary(status)
    if _missing_python_packages(status) or not status["tools"]["pipx"]:
        _print_install_guidance(status)
        print()
        print("You can continue Spotify configuration now, then install dependencies afterward.")
    print()
    print("Spotify app setup")
    print(f"Create a Spotify app and add this redirect URI: {DEFAULT_REDIRECT_URI}")
    print("When a prompt shows a value in brackets, press Enter to accept it.")
    if _prompt_bool("Open the Spotify Developer Dashboard in your browser", True):
        if not _open_url(SPOTIFY_DASHBOARD_URL):
            print(f"Open this URL manually: {SPOTIFY_DASHBOARD_URL}")
    client_id = _prompt_value("Spotify Client ID", str(config.get("spotify_client_id", "")))
    print("Press Enter at the Redirect URI prompt unless you used a different URI in Spotify.")
    redirect_uri = _prompt_value("Redirect URI", str(config.get("redirect_uri", DEFAULT_REDIRECT_URI)))
    print("For full terminal playback, press Enter for librespot. Choose none to use an existing Spotify app/device.")
    terminal_player = _prompt_value(
        "Terminal Spotify player (librespot, spotifyd, auto, none)",
        str(config.get("terminal_player", "librespot")),
    ).lower()
    if terminal_player not in ("librespot", "spotifyd", "auto", "none"):
        terminal_player = "librespot"
    launch_terminal = False if terminal_player == "none" else _prompt_bool(
        "Launch terminal player automatically when needed",
        bool(config.get("launch_terminal_player", True)),
    )
    preferred_device = _prompt_value("Preferred Spotify device name substring (optional)", str(config.get("preferred_device_name", "")))
    print("Bundled ASCII animations: jellyfish, skull, skullify-wordmark. Put licensed packs in your Skullify config folder to use more.")
    ascii_animation = _prompt_value(
        "ASCII animation name or file path",
        str(config.get("ascii_animation", "jellyfish") or "jellyfish"),
    )

    config.update(
        {
            "spotify_client_id": client_id,
            "redirect_uri": redirect_uri,
            "use_pkce": True,
            "terminal_player": terminal_player,
            "launch_terminal_player": launch_terminal,
            "preferred_device_name": preferred_device,
            "ascii_animation": ascii_animation,
        }
    )
    _save_config(config)
    print(f"Saved local config to {_redact_text(str(CONFIG_PATH))}")
    print(f"Local ASCII packs can live in {_redact_text(str(ASCII_DIR))}")
    print(f"Spotify token cache will live at {_redact_text(str(TOKEN_CACHE_PATH))}")
    print("No client secret was stored. Run `skullify --doctor` to check your setup.")
    return 0

def reset_auth() -> int:
    removed = False
    for path in {TOKEN_CACHE_PATH, CACHE_DIR / ".cache"}:
        try:
            if path.exists() and path.is_file():
                path.unlink()
                removed = True
        except OSError as e:
            print(f"Could not remove {_redact_text(str(path))}: {e}", file=sys.stderr)
            return 1
    print("Removed Skullify's local Spotify token cache." if removed else "No Skullify token cache found.")
    return 0

def run_doctor() -> int:
    config = _load_config()
    status = _dependency_status()
    client_id = os.environ.get("SPOTIPY_CLIENT_ID") or config.get("spotify_client_id")
    redirect_uri = os.environ.get("SPOTIPY_REDIRECT_URI") or config.get("redirect_uri") or DEFAULT_REDIRECT_URI
    print("Skullify doctor")
    print(f"Config: {_redact_text(str(CONFIG_PATH))} ({'found' if CONFIG_PATH.exists() else 'missing'})")
    print(f"Cache:  {_redact_text(str(CACHE_DIR))}")
    print(f"State:  {_redact_text(str(STATE_DIR))}")
    print(f"Spotify Client ID: {'set' if client_id else 'missing'}")
    print(f"Redirect URI: {redirect_uri}")
    print(f"Token cache: {_redact_text(str(TOKEN_CACHE_PATH))} ({'found' if TOKEN_CACHE_PATH.exists() else 'missing'})")
    ascii_spec = (
        os.environ.get("SKULLIFY_ASCII")
        or os.environ.get("SKULLIFY_ASCII_JS")
        or str(config.get("ascii_animation", "jellyfish") or "jellyfish")
    )
    ascii_loaded = try_load_ascii_animation(ascii_spec)
    if ascii_loaded:
        _, ascii_name, ascii_path = ascii_loaded
        print(f"ASCII animation: {ascii_name} ({_redact_text(str(ascii_path))})")
    else:
        print(f"ASCII animation: fallback jellyfish (configured value not found: {_redact_text(ascii_spec)})")
    print(f"ASCII directory: {_redact_text(str(ASCII_DIR))}")
    command_paths = _all_command_paths("skullify")
    if command_paths:
        print("Command path:")
        for index, path in enumerate(command_paths[:5]):
            marker = "first" if index == 0 else "also"
            print(f"  {marker}: {_redact_text(path)}")
        if command_paths[0].endswith("/bin/skullify") and ".local/bin/skullify" not in command_paths[0]:
            print("WARNING: a non-pipx skullify command appears first on PATH.")
            print("Run `type -a skullify` and move stale wrappers out of the way if needed.")
    else:
        print("Command path: missing from PATH")
    print()
    _print_setup_summary(status)
    if _missing_python_packages(status):
        _print_install_guidance(status)
    legacy_cache = Path.cwd() / ".cache"
    legacy_log = Path.cwd() / "launch.log"
    if legacy_cache.exists():
        print("WARNING: legacy repo-root .cache exists. Delete it before publishing.")
    if legacy_log.exists():
        print("WARNING: legacy repo-root launch.log exists. Delete it before publishing.")
    if not client_id:
        print("Run `skullify --setup` to configure Spotify PKCE auth.")
    return 0

SCOPES = (
    "user-read-private "
    "playlist-read-private "
    "playlist-modify-private "
    "playlist-modify-public "
    "user-library-read "
    "user-read-currently-playing "
    "user-read-playback-state "
    "user-modify-playback-state"
)

# -------- ASCII frames --------
def _is_placeholder_ascii_frame(frame: List[str]) -> bool:
    text = "\n".join(frame).strip()
    if not text:
        return True
    compact = "".join(ch for ch in text if not ch.isspace())
    return bool(compact) and compact.strip(".") == "" and len(compact) <= 3

def normalize_ascii_frames(frames: List[List[str]]) -> Optional[List[List[str]]]:
    cleaned = [f for f in frames if not _is_placeholder_ascii_frame(f)]
    if not cleaned:
        return None
    width = max((len(line) for frame in cleaned for line in frame), default=0)
    height = max((len(frame) for frame in cleaned), default=0)
    if width <= 0 or height <= 0:
        return None
    blank = " " * width
    normalized: List[List[str]] = []
    for frame in cleaned:
        padded = [(line + blank)[:width] for line in frame]
        while len(padded) < height:
            padded.append(blank)
        normalized.append(padded[:height])
    return normalized

def _ascii_row_counts(frame: List[str]) -> List[int]:
    return [sum(ch != " " for ch in line) for line in frame]

def _has_ascii_frame_gap(frame: List[str]) -> bool:
    rows = _ascii_row_counts(frame)
    if len(rows) < 12:
        return False
    for y in range(4, len(rows) - 4):
        surrounding = (rows[y - 1] + rows[y + 1]) / 2.0
        if rows[y] <= 1 and surrounding >= 20:
            return True
        if surrounding - rows[y] >= 25:
            return True
    return False

def _ascii_frame_diff(a: List[str], b: List[str]) -> int:
    return sum(ca != cb for ra, rb in zip(a, b) for ca, cb in zip(ra, rb))

def _shift_ascii_frame(frame: List[str], dx: int = 0, dy: int = 0) -> List[str]:
    width, height = frame_size(frame)
    blank = " " * width
    shifted: List[str] = []
    for y in range(height):
        src_y = y - dy
        if not 0 <= src_y < height:
            shifted.append(blank)
            continue
        src = frame[src_y]
        row = []
        for x in range(width):
            src_x = x - dx
            row.append(src[src_x] if 0 <= src_x < width else " ")
        shifted.append("".join(row))
    return shifted

def stabilize_ascii_frames(frames: List[List[str]]) -> Optional[List[List[str]]]:
    normalized = normalize_ascii_frames(frames)
    if not normalized:
        return None

    cleaned = [frame for frame in normalized if not _has_ascii_frame_gap(frame)]
    if len(cleaned) < 3:
        return cleaned

    stabilized = [frame[:] for frame in cleaned]
    for i in range(1, len(cleaned) - 1):
        base = _ascii_frame_diff(cleaned[i - 1], cleaned[i]) + _ascii_frame_diff(cleaned[i], cleaned[i + 1])
        best_score = base
        best_frame = cleaned[i]
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0), (-1, -1), (1, -1), (-1, 1), (1, 1)):
            shifted = _shift_ascii_frame(cleaned[i], dx=dx, dy=dy)
            score = _ascii_frame_diff(cleaned[i - 1], shifted) + _ascii_frame_diff(shifted, cleaned[i + 1])
            if score < best_score:
                best_score = score
                best_frame = shifted
        if base - best_score >= 300 and best_score <= base * 0.85:
            stabilized[i] = best_frame
    return normalize_ascii_frames(stabilized)

def smooth_ascii_loop(frames: List[List[str]]) -> List[List[str]]:
    mode = os.environ.get("SKULLIFY_ASCII_LOOP_MODE", "auto").strip().lower()
    if mode in ("forward", "normal", "off", "0", "false", "no") or len(frames) < 3:
        return frames
    if mode in ("pingpong", "bounce"):
        return frames + frames[-2:0:-1]

    neighbor_diffs = [_ascii_frame_diff(frames[i], frames[i + 1]) for i in range(len(frames) - 1)]
    if not neighbor_diffs:
        return frames
    median_neighbor = sorted(neighbor_diffs)[len(neighbor_diffs) // 2]
    loop_diff = _ascii_frame_diff(frames[-1], frames[0])
    if loop_diff > median_neighbor * 1.75:
        return frames + frames[-2:0:-1]
    return frames

def parse_js_ascii_frames(js_text: str) -> Optional[List[List[str]]]:
    if not js_text:
        return None
    indexed_frames: List[Tuple[int, List[str]]] = []
    for m in re.finditer(r"n\[(\d+)\]\s*=\s*'((?:\\.|[^'])*?)';", js_text, re.S):
        s = (
            m.group(2)
            .replace("\\\\", "\\")
            .replace("\\'", "'")
            .replace('\\"', '"')
            .replace("\\n", "\n")
            .replace("\\t", "\t")
            .replace("\r", "")
        )
        indexed_frames.append((int(m.group(1)), s.splitlines()))
    frames = [frame for _, frame in sorted(indexed_frames, key=lambda item: item[0])]
    stabilized = stabilize_ascii_frames(frames or [])
    return smooth_ascii_loop(stabilized) if stabilized else None

def load_js_frames_from_file(path: str) -> Optional[List[List[str]]]:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return parse_js_ascii_frames(f.read())
    except FileNotFoundError:
        return None
    except Exception:
        return None

def _frames_from_json_payload(data: Any) -> Optional[List[List[str]]]:
    raw_frames = data.get("frames") if isinstance(data, dict) else data
    if not isinstance(raw_frames, list):
        return None

    frames: List[List[str]] = []
    for frame in raw_frames:
        if isinstance(frame, str):
            frames.append(frame.replace("\r", "").splitlines())
        elif isinstance(frame, list) and all(isinstance(line, str) for line in frame):
            frames.append([line.replace("\r", "") for line in frame])

    stabilized = stabilize_ascii_frames(frames)
    return smooth_ascii_loop(stabilized) if stabilized else None

def load_json_frames_from_file(path: str) -> Optional[List[List[str]]]:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return _frames_from_json_payload(json.load(f))
    except Exception:
        return None

def load_text_frames_from_file(path: str) -> Optional[List[List[str]]]:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read().replace("\r", "")
    except Exception:
        return None
    chunks = text.split("\f") if "\f" in text else re.split(r"(?m)^\s*---+\s*$", text)
    frames = [chunk.strip("\n").splitlines() for chunk in chunks if chunk.strip()]
    stabilized = stabilize_ascii_frames(frames)
    return smooth_ascii_loop(stabilized) if stabilized else None

def load_ascii_frames_from_file(path: str) -> Optional[List[List[str]]]:
    suffix = Path(path).suffix.lower()
    if suffix == ".json":
        return load_json_frames_from_file(path)
    if suffix == ".js":
        return load_js_frames_from_file(path)
    return load_text_frames_from_file(path)

def _ascii_metadata_from_file(path: Path) -> Dict[str, str]:
    meta: Dict[str, str] = {}
    if path.suffix.lower() != ".json":
        return meta
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            data = json.load(f)
    except Exception:
        return meta
    if not isinstance(data, dict):
        return meta
    for key in ("title", "artist", "source_url", "license"):
        value = str(data.get(key, "") or "").strip()
        if value:
            meta[key] = value
    tags = data.get("tags")
    if isinstance(tags, list):
        tag_text = ", ".join(str(tag).strip() for tag in tags if str(tag).strip())
        if tag_text:
            meta["tags"] = tag_text
    elif isinstance(tags, str) and tags.strip():
        meta["tags"] = tags.strip()
    return meta

def _builtin_ascii_path() -> Path:
    return Path(__file__).with_name("jellyfish_ascii.js")

def _builtin_ascii_dir() -> Path:
    return Path(__file__).with_name("ascii")

def _add_ascii_files_from_dir(candidates: Dict[str, Path], directory: Path) -> None:
    try:
        for path in sorted(directory.glob("*")):
            if path.is_file() and path.suffix.lower() in (".json", ".js", ".txt", ".asc"):
                candidates[path.stem.lower()] = path
    except OSError:
        pass

def _ascii_candidates() -> Dict[str, Path]:
    candidates = {"jellyfish": _builtin_ascii_path(), "default": _builtin_ascii_path()}
    _add_ascii_files_from_dir(candidates, _builtin_ascii_dir())
    _add_ascii_files_from_dir(candidates, ASCII_DIR)
    return candidates

def find_ascii_animation(spec: str) -> Optional[Tuple[Path, str]]:
    raw = (spec or "jellyfish").strip()
    candidates = _ascii_candidates()
    key = raw.lower()
    if key in candidates:
        return candidates[key], key

    path = Path(os.path.expanduser(raw))
    if path.exists() and path.is_file():
        return path, path.stem

    for suffix in (".json", ".js", ".txt", ".asc"):
        candidate = ASCII_DIR / f"{raw}{suffix}"
        if candidate.exists() and candidate.is_file():
            return candidate, candidate.stem

    return None

def resolve_ascii_animation(spec: str) -> Tuple[Path, str]:
    found = find_ascii_animation(spec)
    if found:
        return found
    return _builtin_ascii_path(), "jellyfish"

def try_load_ascii_animation(spec: str) -> Optional[Tuple[List[List[str]], str, Path]]:
    found = find_ascii_animation(spec)
    if not found:
        return None
    path, name = found
    frames = load_ascii_frames_from_file(str(path))
    if not frames:
        return None
    return frames, name, path

def load_ascii_animation(spec: str) -> Tuple[List[List[str]], str, Path]:
    loaded = try_load_ascii_animation(spec)
    if loaded:
        return loaded
    fallback = load_js_frames_from_file(str(_builtin_ascii_path())) or smooth_ascii_loop(stabilize_ascii_frames(DEFAULT_HEAD_FRAMES) or DEFAULT_HEAD_FRAMES)
    return fallback, "jellyfish", _builtin_ascii_path()

def set_active_ascii_animation(spec: str) -> bool:
    global HEAD_FRAMES, ASCII_ANIMATION_NAME, ASCII_ANIMATION_PATH

    loaded = try_load_ascii_animation(spec)
    if not loaded:
        return False
    HEAD_FRAMES, ASCII_ANIMATION_NAME, ASCII_ANIMATION_PATH = loaded
    return True

def ascii_animation_snapshot() -> Tuple[List[List[str]], str, Path]:
    return HEAD_FRAMES, ASCII_ANIMATION_NAME, ASCII_ANIMATION_PATH

def restore_ascii_animation_snapshot(snapshot: Tuple[List[List[str]], str, Path]) -> None:
    global HEAD_FRAMES, ASCII_ANIMATION_NAME, ASCII_ANIMATION_PATH

    HEAD_FRAMES, ASCII_ANIMATION_NAME, ASCII_ANIMATION_PATH = snapshot

def list_ascii_animations() -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = [
        {
            "name": "jellyfish",
            "path": str(_builtin_ascii_path()),
            "title": "Jellyfish",
            "artist": "Saida Magic",
            "source_url": "https://www.instagram.com/saidamagic/",
            "license": "Included with artist credit; reuse outside Skullify requires artist permission.",
            "tags": "jellyfish, animated, built-in",
        }
    ]
    seen = {"jellyfish", "default"}
    for name, path in sorted(_ascii_candidates().items()):
        if name in seen:
            continue
        seen.add(name)
        meta = _ascii_metadata_from_file(path)
        row = {"name": name, "path": str(path)}
        row.update(meta)
        rows.append(row)
    return rows

def search_ascii_animations(query: str = "") -> List[Dict[str, str]]:
    rows = list_ascii_animations()
    terms = [term for term in (query or "").lower().split() if term]
    if not terms:
        return rows

    primary_matches = []
    secondary_matches = []
    for row in rows:
        primary_text = " ".join(
            str(row.get(key, ""))
            for key in ("name", "title", "tags")
        ).lower()
        full_text = " ".join(
            str(row.get(key, ""))
            for key in ("name", "title", "artist", "source_url", "license", "tags", "path")
        ).lower()
        if all(term in primary_text for term in terms):
            primary_matches.append(row)
        elif all(term in full_text for term in terms):
            secondary_matches.append(row)
    return primary_matches or secondary_matches

def frame_size(frame: List[str]) -> Tuple[int,int]:
    if not frame:
        return (0,0)
    return (max((len(line) for line in frame), default=0), len(frame))

def scale_ascii(frame: List[str], scale: float, max_w: Optional[int]=None, max_h: Optional[int]=None) -> List[str]:
    if not frame: return frame[:]
    src_w, src_h = frame_size(frame)
    if src_w == 0 or src_h == 0: return frame[:]
    scale = max(0.2, min(2.0, float(scale)))
    tw = max(1, int(round(src_w * scale)))
    th = max(1, int(round(src_h * scale)))
    if max_w is not None and tw > max_w:
        scale *= max(0.2, max_w / max(1.0, tw)); tw = max(1, int(round(src_w * scale))); th = max(1, int(round(src_h * scale)))
    if max_h is not None and th > max_h:
        scale *= max(0.2, max_h / max(1.0, th)); tw = max(1, int(round(src_w * scale))); th = max(1, int(round(src_h * scale)))
    out: List[str] = []
    for y2 in range(th):
        ys = 0 if th==1 else int(round((y2/(th-1))*(src_h-1)))
        src = frame[ys] if 0<=ys<src_h else ""
        row = []
        for x2 in range(tw):
            xs = 0 if tw==1 else int(round((x2/(tw-1))*(src_w-1)))
            ch = src[xs] if 0<=xs<len(src) else " "
            row.append(ch)
        out.append("".join(row))
    return out

DEFAULT_HEAD_FRAMES = [[
"            ██████████████████",
"          ███                ███",
"         ██   ██        ██     ██",
"        ██   ████      ████     ██",
"       ██    █  █      █  █      ██",
"       ██     ██        ██       ██",
"       ██        ██████          ██",
"       ██      ██████████        ██",
"       ██      ██  ██  ██        ██",
"       ██      ██  ██  ██        ██",
"       ██        ██████          ██",
"        ██                      ██",
"         ██       ▄▄▄▄▄       ██",
"          ███              ███",
"             ██████████████"
]]

ASCII_ANIMATION_SPEC = (
    os.environ.get("SKULLIFY_ASCII")
    or os.environ.get("SKULLIFY_ASCII_JS")
    or str(LOCAL_CONFIG.get("ascii_animation", "jellyfish") or "jellyfish")
)
HEAD_FRAMES, ASCII_ANIMATION_NAME, ASCII_ANIMATION_PATH = load_ascii_animation(ASCII_ANIMATION_SPEC)

DEFAULT_ANIM_INTERVAL = float(os.environ.get("SKULLIFY_ANIM_INTERVAL", "0.02"))
DEFAULT_HEAD_SCALE    = float(os.environ.get("SKULLIFY_HEAD_SCALE", "0.75"))
DEFAULT_HEAD_LAYOUT   = os.environ.get("SKULLIFY_HEAD_LAYOUT", "auto")  # auto|right|below
PREF_DEVICE_NAME      = _config_str("preferred_device_name", "SKULLIFY_DEVICE_NAME", "").lower()
PREFER_DESKTOP_DEVICE = os.environ.get("SKULLIFY_PREFER_DESKTOP", "1").strip().lower() not in ("0", "false", "no", "off")
PREFER_TERMINAL_DEVICE = os.environ.get("SKULLIFY_PREFER_TERMINAL", "1").strip().lower() not in ("0", "false", "no", "off")
TERMINAL_DEVICE_NAME   = os.environ.get("SKULLIFY_TERMINAL_DEVICE_NAME", "skullify-terminal").strip()
TERMINAL_PLAYER        = _config_str("terminal_player", "SKULLIFY_TERMINAL_PLAYER", "librespot").lower()
if TERMINAL_PLAYER == "none":
    TERMINAL_PLAYER = "disabled"
LAUNCH_TERMINAL_PLAYER = _env_bool(
    "SKULLIFY_LAUNCH_TERMINAL",
    bool(LOCAL_CONFIG.get("launch_terminal_player", True)),
) and _env_bool("SKULLIFY_LAUNCH_SPOTIFYD", True)
TERMINAL_LAUNCH_WAIT   = float(os.environ.get(
    "SKULLIFY_TERMINAL_LAUNCH_WAIT",
    os.environ.get("SKULLIFY_SPOTIFYD_LAUNCH_WAIT", "20.0"),
))
TERMINAL_PLAYER_LOG    = os.environ.get("SKULLIFY_TERMINAL_LOG", str(DEFAULT_TERMINAL_LOG))

HELP_TEXT = [
"KEYS:",
"[s]/[/] Search     [ENTER] Select/Open       [b]/Backspace/Esc Back",
"[p] Playlists      [n] New playlist          [A] Add track to playlist",
"[r] Remove from playlist",
"[↑]/[↓] Move       [PgUp]/[PgDn] Page        [Home]/[End] Jump",
"[SPACE] Play/Pause [>] Next   [<] Prev       [x] Shuffle mode",
"[h] Home           [q] Quit                  [a] ASCII art",
"[v] Visualizer (system audio)    [d] Devices    [?] Help",
"[=]/[-]/[0] speed +/-/reset   [[]/[]] scale -/+   [\\] layout auto/right/below"
]

def clamp(v, lo, hi): return max(lo, min(hi, v))

def make_spotify_client():
    if spotipy is None or SpotifyOAuth is None or SpotifyPKCE is None:
        raise RuntimeError("Missing Spotify dependency. Run: pip install -r requirements.txt")
    config = _load_config()
    client_id = os.environ.get("SPOTIPY_CLIENT_ID") or str(config.get("spotify_client_id", "") or "")
    client_secret = os.environ.get("SPOTIPY_CLIENT_SECRET") or str(config.get("spotify_client_secret", "") or "")
    redirect_uri = os.environ.get("SPOTIPY_REDIRECT_URI") or str(config.get("redirect_uri", DEFAULT_REDIRECT_URI) or DEFAULT_REDIRECT_URI)
    use_pkce = _env_bool("SKULLIFY_USE_PKCE", bool(config.get("use_pkce", True)))
    cache_path = os.environ.get("SPOTIPY_CACHE_PATH") or str(TOKEN_CACHE_PATH)

    if not client_id:
        raise RuntimeError("Spotify Client ID is not configured. Run: skullify --setup")
    _ensure_private_dir(TOKEN_CACHE_PATH.parent)
    if use_pkce:
        auth_manager = SpotifyPKCE(
            client_id=client_id,
            scope=SCOPES,
            redirect_uri=redirect_uri,
            cache_path=cache_path,
            open_browser=True,
        )
    else:
        if not client_secret:
            raise RuntimeError("Client-secret OAuth needs SPOTIPY_CLIENT_SECRET. Prefer `skullify --setup` for PKCE.")
        auth_manager = SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            scope=SCOPES,
            redirect_uri=redirect_uri,
            cache_path=cache_path,
            open_browser=True,
        )
    return spotipy.Spotify(auth_manager=auth_manager)

def spotify_device_label(d: Dict[str, Any]) -> str:
    active = "(active) " if d.get("is_active") else ""
    restricted = " [restricted]" if d.get("is_restricted") else ""
    volume = f' vol:{d.get("volume_percent")}' if d.get("volume_percent") is not None else ""
    return f"{active}{d.get('name','Device')} - {d.get('type','?')}{volume}{restricted}"

def _device_id(d: Optional[Dict[str, Any]]) -> Optional[str]:
    return (d or {}).get("id") or None

def _is_usable_device(d: Dict[str, Any]) -> bool:
    return bool(d.get("id")) and not d.get("is_restricted", False)

def _is_desktop_device(d: Dict[str, Any]) -> bool:
    dtype = (d.get("type") or "").strip().lower()
    if dtype in ("computer", "desktop"):
        return True
    name = (d.get("name") or "").strip().lower()
    host_tokens = {
        t for t in (
            socket.gethostname(),
            socket.getfqdn(),
            os.environ.get("HOSTNAME", ""),
        )
        if t
    }
    return any(t.lower().split(".")[0] and t.lower().split(".")[0] in name for t in host_tokens)

def _is_terminal_device(d: Dict[str, Any]) -> bool:
    name = (d.get("name") or "").strip().lower()
    if not name:
        return False
    terminal_name = TERMINAL_DEVICE_NAME.lower()
    tokens = [terminal_name, "librespot", "spotifyd", "skullify"]
    return any(token and token in name for token in tokens)

def _match_device_name(d: Dict[str, Any], needle: str) -> bool:
    return needle and needle.lower() in (d.get("name") or "").lower()

def fetch_spotify_devices(sp) -> List[Dict[str, Any]]:
    try:
        return (sp.devices() or {}).get("devices", []) or []
    except Exception:
        return []

def pick_spotify_device(
    devices: List[Dict[str, Any]],
    preferred_id: Optional[str] = None,
    preferred_name: str = "",
    prefer_terminal: bool = PREFER_TERMINAL_DEVICE,
    prefer_desktop: bool = PREFER_DESKTOP_DEVICE,
    allow_fallback: bool = True,
) -> Optional[Dict[str, Any]]:
    usable = [d for d in devices if _is_usable_device(d)]
    pool = usable or devices

    if preferred_id:
        for d in pool:
            if d.get("id") == preferred_id:
                return d

    preferred_name = (preferred_name or "").strip().lower()
    if preferred_name:
        for d in pool:
            if _match_device_name(d, preferred_name):
                return d

    if prefer_terminal:
        for d in pool:
            if d.get("is_active") and _is_terminal_device(d):
                return d
        for d in pool:
            if _is_terminal_device(d):
                return d
        if not allow_fallback:
            return None

    if prefer_desktop:
        for d in pool:
            if d.get("is_active") and _is_desktop_device(d):
                return d
        for d in pool:
            if _is_desktop_device(d):
                return d
        if not allow_fallback:
            return None

    if allow_fallback:
        for d in pool:
            if d.get("is_active"):
                return d
        if pool:
            return pool[0]
    return None

def _command_from_env(name: str, uri: Optional[str] = None) -> Optional[List[str]]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        parts = shlex.split(raw)
    except ValueError:
        return None
    if uri:
        joined = " ".join(parts)
        if "{uri}" in joined:
            return [p.replace("{uri}", uri) for p in parts]
        return parts + [uri]
    return parts

def _available_command(cmd: List[str]) -> bool:
    if not cmd:
        return False
    exe = cmd[0]
    return bool(shutil.which(exe) or (os.path.isabs(exe) and os.path.exists(exe)))

def _command_executable(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    for path in (
        os.path.expanduser(f"~/.cargo/bin/{name}"),
        f"/usr/local/bin/{name}",
        f"/usr/bin/{name}",
    ):
        if os.path.exists(path):
            return path
    return name

def _pulse_default_sink() -> Optional[str]:
    forced = os.environ.get("SKULLIFY_PULSE_SINK") or os.environ.get("PULSE_SINK")
    if forced:
        return forced.strip() or None
    if not shutil.which("pactl"):
        return None
    try:
        out = subprocess.check_output(["pactl", "info"], text=True, stderr=subprocess.DEVNULL, timeout=2.0)
    except Exception:
        return None
    for line in out.splitlines():
        if line.startswith("Default Sink:"):
            return line.split(":", 1)[1].strip() or None
    return None

def _terminal_player_env() -> Dict[str, str]:
    allowed = (
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "XDG_RUNTIME_DIR",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_STATE_HOME",
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "DBUS_SESSION_BUS_ADDRESS",
        "PULSE_SERVER",
    )
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    sink = _pulse_default_sink()
    if sink:
        env["PULSE_SINK"] = sink
    return env

def _terminal_player_running() -> bool:
    needle = TERMINAL_DEVICE_NAME.strip().lower()
    if not needle:
        return False
    try:
        out = subprocess.check_output(["ps", "-eo", "args="], text=True, stderr=subprocess.DEVNULL, timeout=2.0)
    except Exception:
        return False
    for line in out.splitlines():
        lower = line.lower()
        if needle in lower and ("librespot" in lower or "spotifyd" in lower):
            return True
    return False

def _popen_quiet(cmd: List[str], env: Optional[Dict[str, str]] = None) -> bool:
    if not _available_command(cmd):
        return False
    try:
        log_path = Path(os.path.expanduser(TERMINAL_PLAYER_LOG))
        _ensure_private_dir(log_path.parent)
        _rotate_log(log_path)
        with log_path.open("ab") as log:
            log.write(("\n--- skullify launch %s ---\n" % time.strftime("%Y-%m-%d %H:%M:%S")).encode("utf-8", "replace"))
            log.write(("command: %s\n" % _redacted_command(cmd)).encode("utf-8", "replace"))
            sink = (env or os.environ).get("PULSE_SINK", "")
            if sink:
                log.write(("PULSE_SINK=%s\n" % _redact_text(sink)).encode("utf-8", "replace"))
            log.flush()
            proc = subprocess.Popen(cmd, stdout=log, stderr=log, start_new_session=True, env=env)
        time.sleep(0.35)
        return proc.poll() is None
    except Exception:
        return False

def _run_quiet(cmd: List[str], timeout: float = 3.0) -> bool:
    if not _available_command(cmd):
        return False
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout, check=True)
        return True
    except Exception:
        return False

def launch_terminal_spotify() -> bool:
    if not LAUNCH_TERMINAL_PLAYER:
        return False
    if TERMINAL_PLAYER in ("disabled", "none", "off"):
        return False
    if _terminal_player_running():
        return True
    env_cmd = (
        _command_from_env("SKULLIFY_TERMINAL_PLAYER_CMD")
        or _command_from_env("SKULLIFY_LIBRESPOT_CMD")
    )
    candidates = []
    if env_cmd:
        candidates.append(env_cmd)
    else:
        spotifyd_env_cmd = _command_from_env("SKULLIFY_SPOTIFYD_CMD")
        backend = os.environ.get("SKULLIFY_LIBRESPOT_BACKEND", os.environ.get("SKULLIFY_SPOTIFYD_BACKEND", "pulseaudio"))
        cache_path = os.path.expanduser(os.environ.get("SKULLIFY_LIBRESPOT_CACHE", os.environ.get("SKULLIFY_SPOTIFYD_CACHE", "~/.cache/spotifyd")))
        oauth_path = os.path.expanduser(os.environ.get("SKULLIFY_LIBRESPOT_OAUTH", os.environ.get("SKULLIFY_SPOTIFYD_OAUTH", os.path.join(cache_path, "oauth"))))
        librespot_cmd = [
            _command_executable("librespot"),
            "--name",
            TERMINAL_DEVICE_NAME,
            "--device-type",
            "computer",
            "--backend",
            backend,
            "--bitrate",
            os.environ.get("SKULLIFY_LIBRESPOT_BITRATE", "160"),
            "--cache",
            cache_path,
            "--system-cache",
            oauth_path,
            "--enable-volume-normalisation",
            "--normalisation-pregain",
            os.environ.get("SKULLIFY_LIBRESPOT_NORMALISATION_PREGAIN", "-3"),
            "--initial-volume",
            os.environ.get("SKULLIFY_LIBRESPOT_INITIAL_VOLUME", "90"),
        ]
        spotifyd_cmd = spotifyd_env_cmd or [
            _command_executable("spotifyd"),
            "--no-daemon",
            "--backend",
            os.environ.get("SKULLIFY_SPOTIFYD_BACKEND", "pulseaudio"),
            "--device-name",
            TERMINAL_DEVICE_NAME,
            "--volume-controller",
            os.environ.get("SKULLIFY_SPOTIFYD_VOLUME", "soft-volume"),
        ]
        if TERMINAL_PLAYER in ("", "librespot"):
            candidates.append(librespot_cmd)
        elif TERMINAL_PLAYER == "spotifyd":
            candidates.append(spotifyd_cmd)
        elif TERMINAL_PLAYER == "auto":
            candidates.extend([librespot_cmd, spotifyd_cmd])
        else:
            candidates.append(librespot_cmd)
    env = _terminal_player_env()
    for cmd in candidates:
        if _popen_quiet(cmd, env=env):
            return True
    return False

def wait_for_spotify_device(
    sp,
    preferred_id: Optional[str] = None,
    preferred_name: str = "",
    prefer_terminal: bool = PREFER_TERMINAL_DEVICE,
    prefer_desktop: bool = PREFER_DESKTOP_DEVICE,
    timeout: float = TERMINAL_LAUNCH_WAIT,
) -> Optional[Dict[str, Any]]:
    deadline = time.time() + max(0.0, timeout)
    while time.time() <= deadline:
        devices = fetch_spotify_devices(sp)
        picked = pick_spotify_device(
            devices,
            preferred_id=preferred_id,
            preferred_name=preferred_name,
            prefer_terminal=prefer_terminal,
            prefer_desktop=prefer_desktop,
            allow_fallback=False,
        )
        if picked:
            return picked
        time.sleep(0.5)
    return None

def ensure_spotify_device(
    sp,
    preferred_id: Optional[str] = None,
    preferred_name: str = "",
    prefer_terminal: bool = PREFER_TERMINAL_DEVICE,
    prefer_desktop: bool = PREFER_DESKTOP_DEVICE,
    launch_if_needed: bool = True,
    transfer: bool = True,
    force_play_on_transfer: bool = False,
) -> Optional[Dict[str, Any]]:
    devices = fetch_spotify_devices(sp)
    picked = pick_spotify_device(
        devices,
        preferred_id=preferred_id,
        preferred_name=preferred_name,
        prefer_terminal=prefer_terminal,
        prefer_desktop=prefer_desktop,
        allow_fallback=not prefer_terminal,
    )

    should_launch = (
        launch_if_needed
        and LAUNCH_TERMINAL_PLAYER
        and prefer_terminal
        and (
            not picked
            or (not preferred_id and not preferred_name and not _is_terminal_device(picked))
        )
    )
    if should_launch and launch_terminal_spotify():
        launched = wait_for_spotify_device(
            sp,
            preferred_id=preferred_id,
            preferred_name=preferred_name,
            prefer_terminal=prefer_terminal,
            prefer_desktop=prefer_desktop,
        )
        if launched:
            picked = launched
        elif not picked:
            devices = fetch_spotify_devices(sp)
            picked = pick_spotify_device(
                devices,
                preferred_id=preferred_id,
                preferred_name=preferred_name,
                prefer_terminal=prefer_terminal,
                prefer_desktop=prefer_desktop,
                allow_fallback=not prefer_terminal,
            )

    if not picked:
        return None

    device_id = _device_id(picked)
    if transfer and device_id and not picked.get("is_active"):
        try:
            sp.transfer_playback(device_id, force_play=force_play_on_transfer)
            time.sleep(0.5)
            for d in fetch_spotify_devices(sp):
                if d.get("id") == device_id:
                    picked = d
                    break
        except Exception:
            pass
    return picked

def start_spotify_track(
    sp,
    track_uri: str,
    device: Optional[Dict[str, Any]],
    context_uri: Optional[str] = None,
    prefer_terminal: bool = PREFER_TERMINAL_DEVICE,
    prefer_desktop: bool = PREFER_DESKTOP_DEVICE,
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    device = device or {}
    if not _device_id(device):
        return False, "No terminal Spotify Connect device is available. Run `spotifyd authenticate` to refresh the terminal-player credentials, then try again.", None
    last_error = ""
    for attempt in range(3):
        try:
            kwargs: Dict[str, Any] = {"position_ms": 0}
            if _device_id(device):
                kwargs["device_id"] = _device_id(device)
            if context_uri:
                kwargs["context_uri"] = context_uri
                kwargs["offset"] = {"uri": track_uri}
            else:
                kwargs["uris"] = [track_uri]
            sp.start_playback(**kwargs)
            return True, "", device
        except SpotifyException as e:
            last_error = f"Spotify API {e.http_status}: {e}"
            if e.http_status == 404 and attempt < 2:
                device = ensure_spotify_device(
                    sp,
                    preferred_id=_device_id(device),
                    prefer_terminal=prefer_terminal,
                    prefer_desktop=prefer_desktop,
                    transfer=True,
                )
                time.sleep(0.7 + attempt * 0.5)
                continue
            break
        except Exception as e:
            last_error = str(e)
            break

    return False, last_error, device or None

# -------- Pulse/PipeWire helpers --------
def _pactl_info() -> str:
    return subprocess.check_output(["pactl", "info"], text=True, stderr=subprocess.DEVNULL, timeout=2.0)

def _pactl_list_short_sources() -> str:
    return subprocess.check_output(["pactl", "list", "short", "sources"], text=True, stderr=subprocess.DEVNULL, timeout=2.0)

def find_pulse_monitor() -> Optional[tuple]:
    forced = os.environ.get("SKULLIFY_PULSE_SOURCE")
    if forced: return forced, forced
    if not shutil.which("pactl"): return None
    try:
        sink = ""
        for line in _pactl_info().splitlines():
            if line.startswith("Default Sink:"):
                sink = line.split(":", 1)[1].strip()
                break
        if not sink: return None
        source_lines = _pactl_list_short_sources().splitlines()
        for line in source_lines:
            parts = line.split('\t')
            if len(parts)>=2 and parts[1]==f"{sink}.monitor": return parts[1], parts[1]
        for line in source_lines:
            parts = line.split('\t')
            if len(parts)>=2 and parts[1].endswith(".monitor") and sink in parts[1]: return parts[1], parts[1]
        for line in source_lines:
            parts = line.split('\t')
            if len(parts)>=2 and (parts[1].endswith(".monitor") or "monitor" in parts[1].lower()): return parts[1], parts[1]
    except Exception:
        return None
    return None

class ParecReader:
    def __init__(self, source_name: str, rate: int=VIZ_CAPTURE_RATE, channels: int=2, latency_ms: Optional[int]=None):
        if not shutil.which("parec"):
            raise RuntimeError("parec not found. Install pulseaudio-utils.")
        self.rate, self.channels = int(rate), int(channels)
        self.latency_ms = max(1, int(latency_ms if latency_ms is not None else VIZ_PAREC_MS))
        self.bytes_per_frame = self.channels * 2
        self.proc = subprocess.Popen(
            [
                "parec",
                "-d",
                source_name,
                "--raw",
                "--format=s16le",
                "--rate",
                str(self.rate),
                "--channels",
                str(self.channels),
                f"--latency-msec={self.latency_ms}",
            ],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0
        )
        self.buffer = bytearray(); self.lock = threading.Lock(); self.stop=False
        threading.Thread(target=self._reader, daemon=True).start()
    def _reader(self):
        read_ms = max(2.0, min(50.0, float(VIZ_READ_MS)))
        chunk = max(self.bytes_per_frame, int(self.rate * self.bytes_per_frame * read_ms / 1000.0))
        chunk -= chunk % self.bytes_per_frame
        max_bytes = max(chunk * 2, int(self.rate * self.bytes_per_frame * max(40, VIZ_BUFFER_MS) / 1000.0))
        while not self.stop:
            data=self.proc.stdout.read(chunk)
            if not data: break
            with self.lock:
                self.buffer += data
                if len(self.buffer)>max_bytes: self.buffer=self.buffer[-max_bytes:]
    def get_block(self, frames:int):
        if np is None: return None
        need=frames*self.channels*2
        with self.lock:
            if len(self.buffer)<need: return None
            data=self.buffer[-need:]
        arr = np.frombuffer(data, dtype=np.int16).astype(np.float64)/32768.0
        return arr.reshape(-1,2)[:,0] if self.channels==2 else arr
    def close(self):
        self.stop=True
        try: self.proc.terminate()
        except Exception: pass

# -------- App --------
class Skullify:
    def _np_make_banner(self):
        """Build a Now‑Playing footer: Title — Artist [mm:ss/mm:ss] • o: open context | controls"""
        base = "[v/q] Exit  [SPACE] Play/Pause  [>] Next  [<] Prev"
        try:
            sp = getattr(self,'sp', None) or getattr(self,'spotify', None) or getattr(self,'api', None)
            if not sp:
                return base
            pb = sp.current_playback()
            if not pb or not pb.get('item'):
                return base
            it = pb['item']
            title   = it.get('name','')
            artists = ', '.join(a.get('name','') for a in it.get('artists',[]))
            dur = it.get('duration_ms') or 0
            pos = pb.get('progress_ms') or 0
            def mmss(ms):
                s = int((ms or 0)//1000)
                return f"{s//60}:{s%60:02d}"
            # remember context for the 'o' key
            ctx = (pb.get('context') or {})
            self._np_ctx_uri  = ctx.get('uri')
            self._np_ctx_type = ctx.get('type')
            left = f"{title} — {artists}  [{mmss(pos)}/{mmss(dur)}]  •  o: open context"
            return left + "   |   " + base
        except Exception:
            return base
    def visualizer(self):
        self.visualizer_system()

    def __init__(self, stdscr):
        self.stdscr = stdscr
        curses.start_color(); curses.use_default_colors(); curses.init_pair(1, curses.COLOR_GREEN, -1)
        self.green = curses.color_pair(1); self.bold_green = curses.color_pair(1)|curses.A_BOLD
        try:
            curses.init_pair(2, curses.COLOR_GREEN, curses.COLOR_GREEN)
            self.green_fill = curses.color_pair(2)
            self.green_fill_uses_background = True
        except curses.error:
            self.green_fill = self.bold_green
            self.green_fill_uses_background = False

        self.sp = make_spotify_client()

        self.user = self.safe_call(self.sp.current_user) or {}
        self.username = self.user.get("display_name") or self.user.get("id") or "You"

        # UI state
        self.view="home"; self.items=[]; self.selected=0; self.message="Welcome to Skullify."
        self.history=[]; self.top=0; self._visible_count=10
        self.current_artist=None; self.current_album=None; self.current_tracks=[]
        self.current_album_id=None; self.current_playlist_id=None
        self.pending_add_track_uri=None
        self.shuffle_mode="off"
        self.suggested_queue_signature=None

        # animation/layout
        self.anim_idx=0; self.anim_last=time.time(); self.anim_interval=DEFAULT_ANIM_INTERVAL
        self.head_scale=DEFAULT_HEAD_SCALE; self.head_layout=DEFAULT_HEAD_LAYOUT
        self.ascii_committed_name = ASCII_ANIMATION_NAME
        self.ascii_picker_original: Optional[Tuple[List[List[str]], str, Path]] = None
        self.ascii_preview_name = ASCII_ANIMATION_NAME
        self.ascii_search = ""

        # device preference
        self.preferred_device_id=None

        self.stdscr.nodelay(False); self.stdscr.keypad(True); self.stdscr.timeout(100)

    # ---- utils ----
    def safe_call(self, fn, *args, **kwargs):
        try: return fn(*args, **kwargs)
        except Exception as e:
            self.message=f"Error: {e}"
            return None

    def _reset_scroll(self):
        self.top = 0

    def _capture_view_state(self) -> Dict[str, Any]:
        return {
            "view": self.view,
            "items": list(self.items),
            "selected": self.selected,
            "top": getattr(self, "top", 0),
            "message": self.message,
            "current_artist": self.current_artist,
            "current_album": self.current_album,
            "current_tracks": list(self.current_tracks),
            "current_album_id": self.current_album_id,
            "current_playlist_id": self.current_playlist_id,
            "pending_add_track_uri": self.pending_add_track_uri,
        }

    def _restore_view_state(self, state: Dict[str, Any], message: Optional[str] = None):
        self.view = state.get("view", "home")
        self.items = list(state.get("items", []))
        self.selected = int(state.get("selected", 0) or 0)
        self.top = int(state.get("top", 0) or 0)
        self.message = message if message is not None else state.get("message", "")
        self.current_artist = state.get("current_artist")
        self.current_album = state.get("current_album")
        self.current_tracks = list(state.get("current_tracks", []))
        self.current_album_id = state.get("current_album_id")
        self.current_playlist_id = state.get("current_playlist_id")
        self.pending_add_track_uri = state.get("pending_add_track_uri")
        if self._is_ascii_view():
            self._begin_ascii_picker()
        self._clamp_selection()
        if self.view == "ascii_art":
            self.preview_selected_ascii_art(announce=False)

    def _push_view_state(self):
        if self.view == "visualizer":
            return
        self.history.append(self._capture_view_state())
        if len(self.history) > 50:
            self.history = self.history[-50:]

    def _is_ascii_view(self, view: Optional[str] = None) -> bool:
        return (view if view is not None else self.view) in ("ascii_art", "ascii_preview")

    def _begin_ascii_picker(self):
        if self.ascii_picker_original is None:
            self.ascii_picker_original = ascii_animation_snapshot()
            self.ascii_preview_name = ASCII_ANIMATION_NAME

    def _finish_ascii_picker(self):
        if self.ascii_picker_original is not None:
            restore_ascii_animation_snapshot(self.ascii_picker_original)
            self.anim_idx = 0
            self.anim_last = 0
            self.ascii_preview_name = self.ascii_committed_name
        self.ascii_picker_original = None

    def _enter_view(self, view: str, remember: bool = True):
        if self._is_ascii_view() and not self._is_ascii_view(view):
            self._finish_ascii_picker()
        elif not self._is_ascii_view() and self._is_ascii_view(view):
            self._begin_ascii_picker()
        if remember:
            self._push_view_state()
        self.view = view
        self.selected = 0
        self._reset_scroll()

    def go_home(self):
        if self._is_ascii_view():
            self._finish_ascii_picker()
        self.history.clear()
        self.view_home(remember=False)

    def go_back(self, message: Optional[str] = None):
        if self.history:
            next_view = self.history[-1].get("view")
            if self._is_ascii_view() and not self._is_ascii_view(next_view):
                self._finish_ascii_picker()
            self._restore_view_state(self.history.pop(), message=message)
            self.draw()
            return
        if self.view != "home":
            self.view_home(remember=False)
            if message is not None:
                self.message = message
        else:
            self.message = message or "Already at Home."
        self.draw()

    def _is_back_key(self, ch: int) -> bool:
        return ch in (ord('b'), 27, curses.KEY_BACKSPACE, 127, 8)

    def _item_selectable(self, item: Dict[str, Any]) -> bool:
        return item.get("kind") != "text"

    def _selectable_indices(self) -> List[int]:
        return [i for i, item in enumerate(self.items) if self._item_selectable(item)]

    def _clamp_selection(self):
        if not self.items:
            self.selected = 0
            self.top = 0
            return
        self.selected = clamp(self.selected, 0, len(self.items) - 1)
        selectable = self._selectable_indices()
        if not selectable or self.selected in selectable:
            return
        after = [i for i in selectable if i >= self.selected]
        self.selected = after[0] if after else selectable[-1]

    def _move_selection(self, delta: int):
        if not self.items:
            self.selected = 0
            return
        selectable = self._selectable_indices()
        if not selectable:
            self.selected = clamp(self.selected + delta, 0, len(self.items) - 1)
            return
        if self.selected not in selectable:
            self.selected = selectable[0] if delta >= 0 else selectable[-1]
            return
        pos = selectable.index(self.selected)
        self.selected = selectable[clamp(pos + delta, 0, len(selectable) - 1)]

    def _page_selection(self, direction: int):
        page = max(1, int(getattr(self, "_visible_count", 10) or 10) - 1)
        self._move_selection(direction * page)

    def _jump_selection(self, end: bool = False):
        if not self.items:
            self.selected = 0
            return
        selectable = self._selectable_indices()
        if selectable:
            self.selected = selectable[-1] if end else selectable[0]
        else:
            self.selected = len(self.items) - 1 if end else 0

    def _view_label(self, view: Optional[str]) -> str:
        labels = {
            "home": "HOME",
            "help": "HELP",
            "search": "SEARCH",
            "artist": "ARTIST",
            "albums": "ALBUMS",
            "album_tracks": "TRACKS",
            "playlists": "PLAYLISTS",
            "playlist_tracks": "TRACKS",
            "liked_songs": "LIKED SONGS",
            "devices": "DEVICES",
            "ascii_art": "ASCII ART",
            "ascii_preview": "ASCII PREVIEW",
            "choose_playlist": "ADD TO PLAYLIST",
            "visualizer": "VISUALIZER",
        }
        return labels.get(view or "", (view or "HOME").replace("_", " ").upper())

    def _breadcrumb(self) -> str:
        views = [state.get("view") for state in self.history] + [self.view]
        deduped = []
        for view in views:
            if view and (not deduped or deduped[-1] != view):
                deduped.append(view)
        labels = [self._view_label(view) for view in deduped]
        if len(labels) > 4:
            labels = ["..."] + labels[-3:]
        return " > ".join(labels)

    def _position_label(self) -> str:
        if not self.items:
            return "0 items"
        selectable = self._selectable_indices()
        if not selectable:
            return f"{len(self.items)} lines"
        try:
            pos = selectable.index(self.selected) + 1
        except ValueError:
            pos = 1
        return f"{pos}/{len(selectable)}"

    def _footer_hint(self) -> str:
        shuffle_label = self._shuffle_mode_label() if hasattr(self, "_shuffle_mode_label") else "Shuffle off"
        by_view = {
            "home": "[↑/↓] Move  [Enter] Open  [s] Search  [p] Playlists  [a] ASCII art  [q] Quit",
            "help": "[b/Esc] Back  [h] Home  [q] Quit",
            "search": "[↑/↓] Move  [Enter] Open/Play  [s] Search again  [b/Esc] Back  [q] Quit",
            "artist": "[↑/↓] Move  [Enter] Play/Open  [l] Albums  [A] Add  [b/Esc] Back  [q] Quit",
            "albums": "[↑/↓] Move  [Enter] Open album  [b/Esc] Back  [h] Home  [q] Quit",
            "album_tracks": "[Enter] Play  [A] Add  [SPACE] Play/Pause  [x] {shuffle}  [b/Esc] Back  [q] Quit",
            "playlist_tracks": "[Enter] Play  [A] Add  [r] Remove  [SPACE] Play/Pause  [x] {shuffle}  [b/Esc] Back  [q] Quit",
            "liked_songs": "[Enter] Play  [A] Add  [SPACE] Play/Pause  [x] {shuffle}  [b/Esc] Back  [q] Quit",
            "playlists": "[Enter] Open  [n] New playlist  [PgUp/PgDn] Page  [b/Esc] Back  [q] Quit",
            "devices": "[Enter] Use device  [d] Refresh  [b/Esc] Back  [h] Home  [q] Quit",
            "ascii_art": "[↑/↓] Live preview  [Enter] Save  [s] Search  [a] All  [p] Full preview  [b/Esc] Back",
            "ascii_preview": "[b/Esc] Back  [h] Home  [q] Quit",
            "choose_playlist": "[Enter] Add here  [b/Esc] Cancel  [PgUp/PgDn] Page  [q] Quit",
        }
        hint = by_view.get(self.view, "[↑/↓] Move  [Enter] Open  [b/Esc] Back  [h] Home  [q] Quit")
        return hint.format(shuffle=shuffle_label)

    def open_visualizer(self):
        self._push_view_state()
        self.visualizer_system()
        self.go_back(message=self.message)

    def ask(self, prompt: str) -> Optional[str]:
        curses.echo()
        try:
            curses.curs_set(1)
        except curses.error:
            pass
        self.stdscr.nodelay(False)
        self.stdscr.timeout(-1)
        self.draw()
        h,w=self.stdscr.getmaxyx()
        y=max(0,h-1)
        prompt_text=prompt+" "
        if len(prompt_text)>max(1,w-20):
            prompt_text=prompt_text[:max(1,w-20)]
        x=min(len(prompt_text), max(0,w-1))
        try:
            self.stdscr.move(y,0)
            self.stdscr.clrtoeol()
            self.stdscr.addstr(y,0,prompt_text[:max(0,w-1)], self.bold_green)
            self.stdscr.move(y,x)
            self.stdscr.refresh()
            max_len=max(1,min(200,w-x-1))
            s=self.stdscr.getstr(y,x,max_len).decode("utf-8").strip()
        except Exception:
            s=""
        curses.noecho()
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        self.stdscr.timeout(100)
        return s if s else None

    def _tick_anim(self):
        now=time.time()
        if now-self.anim_last>=self.anim_interval:
            self.anim_idx=(self.anim_idx+1)%len(HEAD_FRAMES); self.anim_last=now

    def _compute_layout(self, art_w:int, art_h:int, term_w:int, term_h:int)->str:
        if self.head_layout in ("right","below"): return self.head_layout
        return "right" if term_w-(art_w+2)>=40 else "below"

    def _scaled_head_for_term(self)->List[str]:
        h,w=self.stdscr.getmaxyx()
        return scale_ascii(HEAD_FRAMES[self.anim_idx], self.head_scale, max_w=w-2, max_h=max(4,h//2))

    # ---- draw ----
    def draw(self, status: Optional[str]=None):
        self._tick_anim()
        term_h,term_w=self.stdscr.getmaxyx()
        tentative_max_w=max(20, term_w//2)
        head=scale_ascii(HEAD_FRAMES[self.anim_idx], self.head_scale, max_w=tentative_max_w, max_h=term_h-6)
        art_w,art_h=frame_size(head)
        layout=self._compute_layout(art_w,art_h,term_w,term_h)
        if layout=="below":
            head=scale_ascii(HEAD_FRAMES[self.anim_idx], self.head_scale, max_w=term_w-2, max_h=term_h//2)
            art_w,art_h=frame_size(head)

        self.stdscr.erase()
        for i,line in enumerate(head):
            if i>=term_h: break
            try: self.stdscr.addstr(i,0,line[:term_w],self.bold_green)
            except curses.error: pass

        if layout=="right":
            x0=min(term_w-40, art_w+2); y0=0
            try:
                for y in range(min(term_h,art_h)):
                    if 0<=x0-1<term_w: self.stdscr.addstr(y,x0-1,"│",self.green)
            except curses.error: pass
        else:
            x0=0; y0=min(art_h+1, term_h-6)

        self._clamp_selection()

        # title
        try:
            art_label = ASCII_ANIMATION_NAME
            if self._is_ascii_view() and ASCII_ANIMATION_NAME != self.ascii_committed_name:
                art_label = f"Preview: {ASCII_ANIMATION_NAME}"
            title=f"Skullify CLI  —  Hello, {self.username}  —  Art: {art_label}"
            col = x0 if x0+len(title)<term_w else max(0, term_w-len(title)-1)
            self.stdscr.addstr(0,col,title[:max(0,term_w-col-1)], self.bold_green)
        except curses.error: pass

        try:
            self.stdscr.addstr(y0, x0, "-"*max(0,term_w-x0-1), self.green)
            view_title = f"[{self._breadcrumb()}]  {self._position_label()}"
            self.stdscr.addstr(y0+1, x0, view_title[:max(0,term_w-x0-1)], self.bold_green)
        except curses.error: pass

        max_lines = max(0, term_h-(y0+5))
        self._visible_count = max_lines
        self._clamp_selection()

        # scroll-follow so selected row stays visible

        self.top = getattr(self, 'top', 0)

        if max_lines <= 0:

            self.top = 0

        elif self.selected < self.top:

            self.top = self.selected

        elif self.selected >= self.top + max_lines:

            self.top = max(0, self.selected - max_lines + 1)

        visible = self.items[self.top:self.top+max_lines] if max_lines > 0 else []

        for row_idx, it in enumerate(visible):

            i_abs = self.top + row_idx

            prefix = '➤ ' if i_abs == self.selected else '  '

            text   = it.get('display','')

            attr   = self.bold_green if i_abs == self.selected else self.green

            try: self.stdscr.addstr(y0+2+row_idx, x0, (prefix+text)[:max(0,term_w-x0-1)], attr)

            except curses.error: pass

        try:
            self.stdscr.addstr(term_h-3, 0, "-"*max(0,term_w-1), self.green)
            self.stdscr.addstr(term_h-2, 0, self._footer_hint()[:max(0,term_w-1)], self.green)
            if status is None: status=self.message
            self.stdscr.addstr(term_h-1, 0, (status or "")[:max(0,term_w-1)], self.bold_green)
        except curses.error: pass

        self.stdscr.refresh()

    # ---- list builders ----
    def build_artist_item(self,a):
        name=a.get("name","Artist"); followers=a.get("followers",{}).get("total",0); genres=", ".join(a.get("genres",[])[:2])
        return {"kind":"artist","id":a["id"],"uri":a["uri"],"raw":a,"display":f"{name}  —  followers:{followers}  {genres}"}
    def build_album_item(self,alb):
        year=(alb.get("release_date","")[:4] or "????"); return {"kind":"album","id":alb["id"],"uri":alb["uri"],"raw":alb,"display":f'{alb.get("name","Album")}  ({year})'}
    def build_track_item(self,t):
        m=t.get("duration_ms",0)//60000; s=(t.get("duration_ms",0)%60000)//1000
        artists=", ".join([a["name"] for a in t.get("artists",[])][:3])
        return {"kind":"track","id":t["id"],"uri":t["uri"],"raw":t,"display":f'{t.get("name","Track")} — {artists}  [{m}:{s:02d}]'}
    def build_playlist_item(self,p):
        tracks=p.get("tracks",{}).get("total",0); owner=p.get("owner",{}).get("display_name") or p.get("owner",{}).get("id","")
        return {"kind":"playlist","id":p["id"],"uri":p["uri"],"raw":p,"display":f'{p.get("name","Playlist")}  —  {tracks} tracks  (by {owner})'}

    def build_liked_songs_item(self):
        total = None
        try:
            page = self.sp.current_user_saved_tracks(limit=1, offset=0)
            total = (page or {}).get("total")
        except Exception:
            pass
        count = f"  —  {total} tracks" if total is not None else ""
        return {"kind":"liked_songs","id":"liked_songs","uri":"spotify:user:library:collection","raw":{},"display":f"Liked Songs{count}  (Spotify Library)"}

    # ---- views ----
    def view_home(self, remember: bool = True):
        self._enter_view("home", remember=remember)
        self.items=[
            {"kind":"action","display":"Search Spotify  (press 's' or '/')","action":"search"},
            {"kind":"action","display":"Your playlists & liked songs   (press 'p')","action":"playlists"},
            {"kind":"action","display":"Visualizer (system audio)  (press 'v')","action":"visualizer_system"},
            {"kind":"action","display":"Devices (press 'd')","action":"devices"},
            {"kind":"action","display":"ASCII art and animations  (press 'a')","action":"ascii_art"},
            {"kind":"action","display":"Help  (press '?')","action":"help"},
        ]
        self.selected=0; self.message="Use ↑/↓ to move, ENTER to select."; self.draw()

    def view_help(self, remember: bool = True):
        self._enter_view("help", remember=remember)
        self.items=[{"kind":"text","display":line} for line in HELP_TEXT]
        self.selected=0; self.message="Help"; self.draw()

    def build_ascii_item(self, row: Dict[str, str]) -> Dict[str, Any]:
        name = row.get("name", "")
        title = row.get("title") or name
        artist = row.get("artist", "")
        loaded = try_load_ascii_animation(name)
        frame_count = len(loaded[0]) if loaded else 0
        active = "SAVED   " if name == self.ascii_committed_name else "        "
        details = f"{title}"
        if artist:
            details += f" by {artist}"
        if frame_count:
            details += f"  ({frame_count} frame{'s' if frame_count != 1 else ''})"
        tags = row.get("tags", "")
        if tags:
            details += f"  [{tags}]"
        return {"kind": "ascii_art", "name": name, "raw": row, "display": f"{active}{name} - {details}"}

    def view_ascii_art(self, search: str = "", remember: bool = True, selected_name: Optional[str] = None):
        self._enter_view("ascii_art", remember=remember)
        rows = search_ascii_animations(search)
        self.items = [self.build_ascii_item(row) for row in rows]
        if selected_name is None and not search:
            selected_name = self.ascii_committed_name
        self.selected = 0
        if selected_name:
            for index, item in enumerate(self.items):
                if item.get("name") == selected_name:
                    self.selected = index
                    break
        self.ascii_search = search
        if rows:
            scope = f" matching '{search}'" if search else ""
            self.message = f"ASCII art{scope}: highlighted art previews live; press ENTER to save it."
        else:
            self.message = f"No ASCII art found for '{search}'. Press 's' to search again or 'a' to show all."
        self.preview_selected_ascii_art(announce=False)
        self.draw()

    def search_ascii_art(self):
        query = self.ask("Search ASCII art:")
        if query:
            self.view_ascii_art(query, remember=False)
        else:
            self.message = "ASCII search cancelled."
            self.draw()

    def preview_selected_ascii_art(self, announce: bool = True):
        if self.view != "ascii_art" or not (0 <= self.selected < len(self.items)):
            return
        item = self.items[self.selected]
        if item.get("kind") != "ascii_art":
            return
        name = item.get("name") or ""
        if not name:
            return
        if name == self.ascii_preview_name and name == ASCII_ANIMATION_NAME:
            return
        if not set_active_ascii_animation(name):
            if announce:
                self.message = f"Could not preview ASCII art: {name}"
            return
        self.ascii_preview_name = name
        self.anim_idx = 0
        self.anim_last = 0
        if announce:
            self.message = f"Previewing {name}. Press ENTER to save it for next time."

    def set_ascii_art(self, item: Dict[str, Any]):
        name = item.get("name") or ""
        if not set_active_ascii_animation(name):
            self.message = f"Could not load ASCII art: {name}"
            self.draw()
            return
        config = _load_config()
        config["ascii_animation"] = name
        _save_config(config)
        self.ascii_committed_name = name
        self.ascii_picker_original = ascii_animation_snapshot()
        self.ascii_preview_name = name
        self.anim_idx = 0
        self.anim_last = 0
        self.view_ascii_art(getattr(self, "ascii_search", ""), remember=False, selected_name=name)
        note = " Environment variable override is active." if os.environ.get("SKULLIFY_ASCII") or os.environ.get("SKULLIFY_ASCII_JS") else ""
        self.message = f"Active ASCII art saved: {name}.{note}"
        self.draw()

    def preview_ascii_art(self):
        if not (0 <= self.selected < len(self.items)):
            self.message = "Select ASCII art to preview."
            self.draw()
            return
        item = self.items[self.selected]
        if item.get("kind") != "ascii_art":
            self.message = "Select ASCII art to preview."
            self.draw()
            return
        loaded = try_load_ascii_animation(item.get("name", ""))
        if not loaded:
            self.message = f"Could not preview ASCII art: {item.get('name', '')}"
            self.draw()
            return
        frames, name, _ = loaded
        frame = scale_ascii(frames[0], self.head_scale, max_w=max(20, self.stdscr.getmaxyx()[1] - 4))
        preview_lines = [{"kind": "text", "display": f"Preview: {name}  ({len(frames)} frame{'s' if len(frames) != 1 else ''})"}]
        preview_lines += [{"kind": "text", "display": line} for line in frame]
        self._enter_view("ascii_preview", remember=True)
        self.items = preview_lines
        self.selected = 0
        self.message = "Preview only. Press Back, then Enter to set it active."
        self.draw()

    def view_search(self, preset: Optional[str]=None, remember: bool = True):
        query=preset or self.ask("Search Spotify:")
        if not query:
            self.message="Search cancelled."
            self.draw()
            return
        self._enter_view("search", remember=remember)
        self.items=[]; self.selected=0
        res=self.safe_call(self.sp.search, q=query, type="track,artist,album", limit=10)
        tracks=(res or {}).get("tracks",{}).get("items",[]) or []
        artists=(res or {}).get("artists",{}).get("items",[]) or []
        albums=(res or {}).get("albums",{}).get("items",[]) or []
        items=[]
        for t in tracks:
            try:
                it=self.build_track_item(t)
                it["display"]="[Track] "+it["display"]
                items.append(it)
            except Exception:
                pass
        for a in artists:
            try:
                it=self.build_artist_item(a)
                it["display"]="[Artist] "+it["display"]
                items.append(it)
            except Exception:
                pass
        for alb in albums:
            try:
                it=self.build_album_item(alb)
                it["display"]="[Album] "+it["display"]
                items.append(it)
            except Exception:
                pass
        self.items=items
        if not self.items:
            self.message=f"No Spotify results found for '{query}'. Press 's' to search again."
        else:
            self.message=f"Found {len(self.items)} result(s) for '{query}'. ENTER to open/play; 's' to search again."
        self.draw()

    def view_artist(self, artist_id:str, remember: bool = True):
        self._enter_view("artist", remember=remember)
        a=self.safe_call(self.sp.artist, artist_id) or {}; self.current_artist=a
        name=a.get("name","Artist")
        top=(self.safe_call(self.sp.artist_top_tracks, artist_id, country="US") or {}).get("tracks",[])
        self.current_tracks=top; tracks=[self.build_track_item(t) for t in top]
        self.items=[{"kind":"text","display":f"{name} — TOP TRACKS:"}]+tracks+[{"kind":"text","display":""},{"kind":"action","display":"View albums (press 'l')","action":"albums"}]
        self.selected=1 if tracks else 0
        self.message="ENTER to play track; 'A' to add track to a playlist; 'l' to view albums."; self.draw()

    def view_albums(self, artist_id:str, remember: bool = True):
        self._enter_view("albums", remember=remember)
        albs=[]; seen=set()
        for group in ("album","single"):
            page=self.safe_call(self.sp.artist_albums, artist_id, album_type=group, limit=50)
            for alb in (page or {}).get("items",[]):
                key=(alb.get("name"), (alb.get("release_date","")[:4]))
                if key not in seen: seen.add(key); albs.append(alb)
        albs.sort(key=lambda a:a.get("release_date",""), reverse=True)
        self.items=[self.build_album_item(a) for a in albs]
        self.selected=0; self.message="ENTER to open album tracks."; self.draw()

    def view_album_tracks(self, album_id:str, remember: bool = True):
        self._enter_view("album_tracks", remember=remember)
        self.current_album_id=album_id; self.current_playlist_id=None; tr=[]; off=0
        while True:
            page=self.safe_call(self.sp.album_tracks, album_id, limit=50, offset=off) or {}
            tr+=page.get("items",[]); 
            if not page.get("next"): break
            off+=50
        self.current_tracks=tr; self.items=[self.build_track_item(t) for t in tr]
        self.selected=0; self.message="ENTER to play track; 'A' to add track to a playlist."; self.draw()

    def view_playlists(self, remember: bool = True):
        self._enter_view("playlists", remember=remember)
        pls=[]; off=0
        while True:
            page=self.safe_call(self.sp.current_user_playlists, limit=50, offset=off) or {}
            pls+=page.get("items",[]); 
            if not page.get("next"): break
            off+=50
        self.items=[self.build_liked_songs_item()]+[self.build_playlist_item(p) for p in pls]
        self.items.insert(0, {"kind":"action","display":"[Create new playlist]  (press 'n')","action":"new_playlist"})
        self.selected=1 if len(self.items)>1 else 0
        self.message="ENTER to open playlist/library; 'n' to create a new playlist."; self.draw()

    def view_liked_tracks(self, remember: bool = True):
        self._enter_view("liked_songs", remember=remember)
        self.current_album_id=None; self.current_playlist_id = None
        tracks=[]; off=0
        try:
            while True:
                page=self.sp.current_user_saved_tracks(limit=50, offset=off) or {}
                tracks += [it.get("track") for it in page.get("items",[]) if it.get("track")]
                if not page.get("next"): break
                off+=50
        except SpotifyException as e:
            self.items=[]; self.selected=0
            if e.http_status in (401, 403):
                self.message="Liked Songs needs Spotify library permission (`user-library-read`). Restart Skullify and approve the updated Spotify permissions."
            else:
                self.message=f"Could not load Liked Songs: Spotify API {e.http_status}"
            self.draw(); return
        except Exception as e:
            self.items=[]; self.selected=0; self.message=f"Could not load Liked Songs: {e}"; self.draw(); return
        self.current_tracks=tracks; self.items=[self.build_track_item(t) for t in tracks]
        self.selected=0; self.message="Liked Songs: ENTER to play track; 'A' to add this track to a playlist."; self.draw()

    def view_playlist_tracks(self, playlist_id:str, remember: bool = True, selected_after: Optional[int] = None):
        self._enter_view("playlist_tracks", remember=remember)
        self.current_album_id=None; self.current_playlist_id = playlist_id
        tracks=[]; items=[]; off=0
        while True:
            page=self.safe_call(self.sp.playlist_items, playlist_id, limit=100, offset=off) or {}
            page_items = page.get("items",[]) or []
            for idx, playlist_item in enumerate(page_items):
                track = playlist_item.get("track")
                if not track:
                    continue
                item = self.build_track_item(track)
                item["playlist_position"] = off + idx
                item["playlist_item"] = playlist_item
                tracks.append(track)
                items.append(item)
            if not page.get("next"): break
            off+=100
        self.current_tracks=tracks; self.items=items
        self.selected=clamp(int(selected_after or 0), 0, max(0, len(self.items)-1))
        self.message="ENTER to play track; 'A' to add this track to another playlist; 'r' to remove."; self.draw()

    # ---- device helpers ----
    def _devices(self)->List[Dict[str,Any]]:
        return fetch_spotify_devices(self.sp)

    def _pick_device(self)->Optional[Dict[str,Any]]:
        devs=self._devices()
        return pick_spotify_device(
            devs,
            preferred_id=self.preferred_device_id,
            preferred_name=PREF_DEVICE_NAME,
            prefer_terminal=PREFER_TERMINAL_DEVICE,
            prefer_desktop=PREFER_DESKTOP_DEVICE,
            allow_fallback=not PREFER_TERMINAL_DEVICE,
        )

    def ensure_active_device(self, force_play_on_transfer: bool = False) -> Optional[Dict[str,Any]]:
        d=ensure_spotify_device(
            self.sp,
            preferred_id=self.preferred_device_id,
            preferred_name=PREF_DEVICE_NAME,
            prefer_terminal=PREFER_TERMINAL_DEVICE,
            prefer_desktop=PREFER_DESKTOP_DEVICE,
            launch_if_needed=True,
            transfer=True,
            force_play_on_transfer=force_play_on_transfer,
        )
        if not d:
            if LAUNCH_TERMINAL_PLAYER:
                self.message="No terminal Spotify device found. I tried starting the terminal player; run `spotifyd authenticate` if credentials need refreshing."
            else:
                self.message="No terminal Spotify device found. Start librespot/spotifyd or pick a device with 'd'."
            return None
        self.preferred_device_id=d.get("id") or self.preferred_device_id
        return d

    def view_devices(self, remember: bool = True):
        self._enter_view("devices", remember=remember)
        devs=self._devices()
        terminal_dev=pick_spotify_device(
            devs,
            preferred_name=PREF_DEVICE_NAME,
            prefer_terminal=PREFER_TERMINAL_DEVICE,
            prefer_desktop=PREFER_DESKTOP_DEVICE,
            allow_fallback=not PREFER_TERMINAL_DEVICE,
        )
        if not devs or (PREFER_TERMINAL_DEVICE and not terminal_dev):
            if launch_terminal_spotify():
                wait_for_spotify_device(
                    self.sp,
                    preferred_name=PREF_DEVICE_NAME,
                    prefer_terminal=PREFER_TERMINAL_DEVICE,
                    prefer_desktop=PREFER_DESKTOP_DEVICE,
                )
            devs=fetch_spotify_devices(self.sp)
        if not devs:
            self.items=[{"kind":"text","display":"No devices. Start the terminal player or run `spotifyd authenticate`, then press 'd' again."}]
            self.selected=0; self.message="No Spotify Connect devices found."; self.draw(); return
        devs.sort(key=lambda d: (not _is_terminal_device(d), not d.get("is_active", False), d.get("name","").lower()))
        self.items=[{"kind":"device","id":d.get("id"),"raw":d,"display":spotify_device_label(d)} for d in devs]
        self.selected=0; self.message="ENTER to transfer playback here."; self.draw()

    def use_device(self, item: Dict[str, Any]):
        d=item.get("raw") or {}
        self.preferred_device_id=d.get("id")
        try:
            if d.get("id"):
                self.sp.transfer_playback(d["id"], force_play=True)
            message=f"Using {d.get('name','device')}."
        except Exception as e:
            message=f"Saved {d.get('name','device')} as preferred, but transfer failed: {e}"
        self.go_back(message=message)

    # ---- actions ----
    def create_playlist(self):
        name=self.ask("New playlist name:"); 
        if not name: self.message="Cancelled."; return
        user_id=(self.user or {}).get("id"); 
        if not user_id: self.message="Cannot get user id."; return
        res=self.safe_call(self.sp.user_playlist_create, user_id, name, public=False, description="Created from Skullify CLI")
        if res and res.get("id"):
            message=f"Playlist '{name}' created."
            self.view_playlists(remember=False)
            self.message=message
            self.draw()
        else: self.message="Failed to create playlist."

    def select_playlist_and_add(self, track_uri:str):
        pls=[]; off=0
        while True:
            page=self.safe_call(self.sp.current_user_playlists, limit=50, offset=off) or {}
            pls+=page.get("items",[]); 
            if not page.get("next"): break
            off+=50
        choices=[self.build_playlist_item(p) for p in pls]
        if not choices: self.message="You have no playlists. Create one first ('p' then 'n')."; return
        self._enter_view("choose_playlist", remember=True)
        self.pending_add_track_uri=track_uri
        for choice in choices:
            choice["kind"]="add_playlist"
        self.items=choices; self.selected=0; self.message="Select a playlist and press ENTER to add."; self.draw()

    def add_pending_track_to_playlist(self, item: Dict[str, Any]):
        track_uri=self.pending_add_track_uri
        if not track_uri:
            self.go_back(message="No track selected to add.")
            return
        pid=item.get("id")
        res=self.safe_call(self.sp.playlist_add_items, pid, [track_uri])
        if res is None and str(self.message).startswith("Error:"):
            message=self.message
        else:
            message=f"Added to playlist: {item.get('raw',{}).get('name','Playlist')}."
        self.pending_add_track_uri=None
        self.go_back(message=message)

    def remove_selected_playlist_track(self):
        if self.view != "playlist_tracks" or not self.current_playlist_id:
            self.message="Open a playlist first, then select a song to remove."
            return
        if not (0 <= self.selected < len(self.items)):
            self.message="No song selected to remove."
            return
        item=self.items[self.selected]
        if item.get("kind") != "track":
            self.message="Select a song to remove."
            return

        position=item.get("playlist_position")
        uri=item.get("uri")
        track=item.get("raw") or {}
        name=track.get("name") or "this song"
        if position is None or not uri:
            self.message="Could not determine the playlist position for this song."
            return

        answer=self.ask(f"Remove '{name}' from this playlist? Type y to confirm:")
        if (answer or "").strip().lower() not in ("y", "yes"):
            self.message="Remove cancelled."
            self.draw()
            return

        selected_idx=self.selected
        res=self.safe_call(
            self.sp.playlist_remove_specific_occurrences_of_items,
            self.current_playlist_id,
            [{"uri": uri, "positions": [int(position)]}],
        )
        if res is None and str(self.message).startswith("Error:"):
            self.draw()
            return
        self.view_playlist_tracks(self.current_playlist_id, remember=False, selected_after=selected_idx)
        self.message=f"Removed '{name}' from playlist."
        self.draw()

    def _shuffle_mode_label(self) -> str:
        return {
            "off": "Shuffle off",
            "shuffle": "Shuffle on",
            "suggested": "Shuffle + suggestions",
        }.get(getattr(self, "shuffle_mode", "off"), "Shuffle off")

    def _current_context_signature(self) -> Tuple[str, Optional[str]]:
        if self.view == "playlist_tracks" and self.current_playlist_id:
            return ("playlist", self.current_playlist_id)
        if self.view == "album_tracks" and self.current_album_id:
            return ("album", self.current_album_id)
        if self.view == "liked_songs":
            return ("liked_songs", None)
        return (self.view, None)

    def _set_shuffle(self, enabled: bool, device: Optional[Dict[str, Any]] = None) -> bool:
        d = device or self.ensure_active_device(force_play_on_transfer=False)
        if not d or not d.get("id"):
            return False
        try:
            self.sp.shuffle(bool(enabled), device_id=d.get("id"))
            return True
        except SpotifyException as e:
            self.message = f"Shuffle failed: Spotify API {e.http_status}"
        except Exception as e:
            self.message = f"Shuffle failed: {e}"
        return False

    def _track_artist_ids(self, track: Dict[str, Any]) -> List[str]:
        return [a.get("id") for a in (track or {}).get("artists", []) if a.get("id")]

    def _suggested_tracks_for_current_context(self, seed_track_uri: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
        if self.view not in ("playlist_tracks", "album_tracks", "liked_songs", "artist"):
            return []
        context_tracks = [t for t in self.current_tracks if isinstance(t, dict) and t.get("uri")]
        existing_uris = {t.get("uri") for t in context_tracks if t.get("uri")}
        seed_track_ids: List[str] = []
        seed_artist_ids: List[str] = []

        if seed_track_uri:
            for t in context_tracks:
                if t.get("uri") == seed_track_uri and t.get("id"):
                    seed_track_ids.append(t["id"])
                    seed_artist_ids.extend(self._track_artist_ids(t))
                    break
        for t in context_tracks:
            if len(seed_track_ids) < 2 and t.get("id") and t.get("id") not in seed_track_ids:
                seed_track_ids.append(t["id"])
            for artist_id in self._track_artist_ids(t):
                if artist_id not in seed_artist_ids:
                    seed_artist_ids.append(artist_id)
                if len(seed_artist_ids) >= 3:
                    break
            if len(seed_track_ids) >= 2 and len(seed_artist_ids) >= 3:
                break

        suggestions: List[Dict[str, Any]] = []
        try:
            rec = self.sp.recommendations(
                seed_tracks=seed_track_ids[:2] or None,
                seed_artists=seed_artist_ids[:3] or None,
                limit=limit,
            )
            suggestions = (rec or {}).get("tracks", []) or []
        except Exception:
            suggestions = []

        if not suggestions:
            for artist_id in seed_artist_ids[:5]:
                try:
                    top = (self.sp.artist_top_tracks(artist_id, country="US") or {}).get("tracks", []) or []
                except Exception:
                    top = []
                for t in top:
                    if t.get("uri") and t.get("uri") not in existing_uris:
                        suggestions.append(t)
                    if len(suggestions) >= limit:
                        break
                if len(suggestions) >= limit:
                    break

        seen = set(existing_uris)
        out = []
        for track in suggestions:
            uri = track.get("uri")
            if uri and uri not in seen:
                seen.add(uri)
                out.append(track)
            if len(out) >= limit:
                break
        return out

    def queue_suggested_tracks(self, seed_track_uri: Optional[str] = None, device: Optional[Dict[str, Any]] = None) -> int:
        d = device or self.ensure_active_device(force_play_on_transfer=False)
        if not d or not d.get("id"):
            return 0
        signature = (self._current_context_signature(), seed_track_uri)
        if self.suggested_queue_signature == signature:
            return 0
        suggestions = self._suggested_tracks_for_current_context(seed_track_uri=seed_track_uri, limit=10)
        queued = 0
        for track in suggestions:
            try:
                self.sp.add_to_queue(track["uri"], device_id=d.get("id"))
                queued += 1
            except Exception:
                continue
        self.suggested_queue_signature = signature
        return queued

    def cycle_shuffle_mode(self):
        order = ["off", "shuffle", "suggested"]
        current = getattr(self, "shuffle_mode", "off")
        mode = order[(order.index(current) + 1) % len(order)] if current in order else "shuffle"
        d = self.ensure_active_device(force_play_on_transfer=False)
        if not d:
            return
        if mode == "off":
            if self._set_shuffle(False, d):
                self.shuffle_mode = "off"
                self.message = "Shuffle off."
            return
        if not self._set_shuffle(True, d):
            return
        self.shuffle_mode = mode
        if mode == "suggested":
            queued = self.queue_suggested_tracks(device=d)
            detail = f" Queued {queued} suggested track(s)." if queued else " No new suggestions queued yet."
            self.message = "Shuffle + suggestions on." + detail
        else:
            self.message = "Shuffle on."

    def start_playback_for_track(self, track_uri: str):
        """Robust: ensure/transfer to an active device, then start. If inside a playlist view,
        start in *playlist context* so the queue continues."""
        d=self.ensure_active_device(force_play_on_transfer=False)
        if not d: return
        if getattr(self, "shuffle_mode", "off") in ("shuffle", "suggested"):
            self._set_shuffle(True, d)
        context_uri=None
        if self.view=="playlist_tracks" and self.current_playlist_id:
            context_uri=f"spotify:playlist:{self.current_playlist_id}"
        elif self.view=="album_tracks" and self.current_album_id:
            context_uri=f"spotify:album:{self.current_album_id}"
        ok, err, used_device = start_spotify_track(
            self.sp,
            track_uri,
            d,
            context_uri=context_uri,
            prefer_terminal=PREFER_TERMINAL_DEVICE,
            prefer_desktop=PREFER_DESKTOP_DEVICE,
        )
        device_name=(used_device or d).get("name", TERMINAL_DEVICE_NAME)
        if ok:
            suffix=""
            if getattr(self, "shuffle_mode", "off") == "suggested":
                queued = self.queue_suggested_tracks(seed_track_uri=track_uri, device=used_device or d)
                suffix = f" Queued {queued} suggested track(s)." if queued else ""
            self.message=f"Playing on {device_name}. {self._shuffle_mode_label()}.{suffix}"
        else:
            self.message=f"Playback failed on {device_name}: {err}. Try 'd' to pick another device."

    def toggle_play_pause(self):
        d=self.ensure_active_device(force_play_on_transfer=False)
        if not d: return
        pb=self.safe_call(self.sp.current_playback)
        if not pb: self.message="No playback state found."; return
        if pb.get("is_playing"):
            self.safe_call(self.sp.pause_playback, device_id=d.get("id")); self.message="Paused."
        else:
            self.safe_call(self.sp.start_playback, device_id=d.get("id")); self.message="Playing."

    def next_track(self):
        d=self.ensure_active_device(force_play_on_transfer=False)
        if not d: return
        self.safe_call(self.sp.next_track, device_id=d.get("id")); self.message="Next >>"

    def prev_track(self):
        d=self.ensure_active_device(force_play_on_transfer=False)
        if not d: return
        self.safe_call(self.sp.previous_track, device_id=d.get("id")); self.message="Previous <<"

    # ---- visualizers ----
    def visualizer_system(self):
        self.view="visualizer"; self.items=[]; self.selected=0
        self.message="System visualizer: [h/v/q/Esc] exit, [x] Shuffle mode, [SPACE] Play/Pause, [<]/[>] Prev/Next"
        self.stdscr.nodelay(True)
        if np is None: self.stdscr.nodelay(False); self.message="Missing numpy. Run: pip install numpy"; return
        mon=find_pulse_monitor()
        if not mon: self.stdscr.nodelay(False); self.message="No monitor source. Set SKULLIFY_PULSE_SOURCE or install pulseaudio-utils."; return
        source_name,display=mon
        try: reader=ParecReader(source_name, VIZ_CAPTURE_RATE, 2, latency_ms=VIZ_PAREC_MS)
        except Exception as e: self.stdscr.nodelay(False); self.message=f"parec error: {e}"; return
        sample_rate = reader.rate
        nfft = max(256, _envi("SKULLIFY_VIZ_NFFT", 512))
        frame_interval = 1.0 / max(5.0, _envf("SKULLIFY_VIZ_FPS", VIZ_FPS_DEFAULT))
        window = np.hanning(nfft).astype(np.float32)
        freqs = np.fft.rfftfreq(nfft, 1.0 / sample_rate)
        levels = None
        bin_cache: Dict[int, List[Tuple[int, int]]] = {}
        np_refresh_interval = max(1.0, _envf("SKULLIFY_NP_REFRESH", 3.0))
        self._np_inline_text = display
        self._np_inline_busy = False
        self._np_inline_last = 0.0

        def _update_now_playing_async():
            if getattr(self, "_np_inline_busy", False):
                return
            self._np_inline_busy = True

            def worker():
                text = ""
                try:
                    pb = self.sp.current_playback()
                    it = pb.get("item") if pb else None
                    if not it and hasattr(self.sp, "currently_playing"):
                        cp = self.sp.currently_playing()
                        it = (cp or {}).get("item")
                        if cp and not pb:
                            pb = cp
                    if it:
                        name = it.get("name", "")
                        artists = ", ".join(a.get("name", "") for a in (it.get("artists") or []))
                        dur = it.get("duration_ms") or 0
                        pos = (pb or {}).get("progress_ms", 0) or 0
                        def _mmss(ms):
                            s = int((ms or 0) // 1000)
                            return f"{s//60}:{s%60:02d}"
                        text = f"{artists} - {name} [{_mmss(pos)}/{_mmss(dur)}]"
                except Exception:
                    text = ""
                finally:
                    if text:
                        self._np_inline_text = text
                    self._np_inline_busy = False

            threading.Thread(target=worker, daemon=True).start()

        def _bin_ranges(nbars: int) -> List[Tuple[int, int]]:
            cached = bin_cache.get(nbars)
            if cached:
                return cached
            edges = np.geomspace(35.0, sample_rate / 2.0, nbars + 1)
            ranges: List[Tuple[int, int]] = []
            for i in range(nbars):
                start = int(np.searchsorted(freqs, edges[i], side="left"))
                end = int(np.searchsorted(freqs, edges[i + 1], side="left"))
                ranges.append((start, max(start + 1, end)))
            bin_cache[nbars] = ranges
            return ranges

        next_frame_at = time.monotonic()
        try:
            while True:
                ch=self.stdscr.getch()
                if ch in (ord('q'), ord('v'), ord('h'), 27): self.message="Exited visualizer."; return
                elif ch in (ord('o'), ord('O')):
                    try:
                        _np_open_context(self)
                    except Exception:
                        pass
                elif ch==curses.KEY_RESIZE: pass

                elif ch==ord(' '): self.toggle_play_pause()
                elif ch==ord('>'): self.next_track()
                elif ch==ord('<'): self.prev_track()
                elif ch==ord('x'): self.cycle_shuffle_mode()

                tnow = time.time()
                if tnow - getattr(self,'_np_inline_last',0) >= np_refresh_interval:
                    self._np_inline_last = tnow
                    _update_now_playing_async()
                np_text = getattr(self,'_np_inline_text','')
                block=reader.get_block(frames=nfft)
                self._tick_anim(); head=self._scaled_head_for_term()
                if block is None:
                    self.draw_visualizer_bars([0.0]*32, "System audio", (np_text or display), head)
                    next_frame_at = _sleep_until_next_frame(next_frame_at, frame_interval)
                    continue
                fft=np.fft.rfft(window*block[:nfft])
                mag=np.abs(fft)/max(1.0, float(window.sum())*0.5)
                db=20.0*np.log10(np.maximum(mag, 1e-7))
                norm=np.clip((db+68.0)/58.0, 0.0, 1.0)
                h,w=self.stdscr.getmaxyx(); nbars=max(16, min(VIZ_MAX_BARS, (w-4)//max(1,VIZ_BAR_W)))
                raw=np.zeros((nbars,), dtype=np.float32)
                for i,(start,end) in enumerate(_bin_ranges(nbars)):
                    raw[i]=float(norm[start:end].max(initial=0.0))
                raw=np.power(raw, 1.0/max(0.1,VIZ_GAMMA))
                if levels is None or levels.shape != raw.shape:
                    levels=raw.copy()
                else:
                    dt=max(frame_interval, time.time()-tnow)
                    attack=1.0-np.exp(-dt/max(0.001,VIZ_ATTACK_TC))
                    release=1.0-np.exp(-dt/max(0.001,VIZ_RELEASE_TC))
                    rising=raw>levels
                    levels[rising] += (raw[rising]-levels[rising])*attack
                    levels[~rising] += (raw[~rising]-levels[~rising])*release
                display_levels = smooth_visualizer_levels(levels)
                if hasattr(display_levels, "tolist"):
                    display_levels = display_levels.tolist()
                self.draw_visualizer_bars(display_levels, "System audio", (np_text or display), head)
                next_frame_at = _sleep_until_next_frame(next_frame_at, frame_interval)
        finally:
            reader.close()
            self.stdscr.nodelay(False)
            self.stdscr.timeout(100)

    def draw_visualizer_bars(self, levels, title_left:str, title_right:str, head_frame:List[str]):
        self.stdscr.erase(); h,w=self.stdscr.getmaxyx()
        for i,line in enumerate(head_frame):
            if i>=h: break
            try: self.stdscr.addstr(i,0,line[:w],self.bold_green)
            except curses.error: pass
        top=min(len(head_frame)+1, h-4)
        try:
            self.stdscr.addstr(top,0,"-"*max(0,w-1),self.green)
            self.stdscr.addstr(top+1,0,f"[VISUALIZER] {title_left} — {title_right}"[:max(0,w-1)], self.bold_green)
        except curses.error: pass
        max_h=max(3, h-(top+6)); bar_w=max(1,VIZ_BAR_W); lv=levels or [0.0]*max(10, min(VIZ_MAX_BARS,(w-4)//bar_w))
        heights=[]
        for level in lv:
            try:
                value=float(level)
            except Exception:
                value=0.0
            if not math.isfinite(value):
                value=0.0
            heights.append(clamp(int(math.ceil(clamp(value, 0.0, 1.0)*max_h)), 0, max_h))

        heights=fill_visualizer_height_gaps(heights, max_h)

        fill_attr=getattr(self, "green_fill", self.bold_green)
        fill_ch=" " if getattr(self, "green_fill_uses_background", False) else "█"
        dark_line=" "*max(0,w-1)
        for y in range(max_h):
            threshold=max_h-y
            row_y=top+3+y
            try:
                self.stdscr.addstr(row_y, 0, dark_line)
            except curses.error:
                pass
            run_start=None
            for i,height in enumerate(heights):
                if height>=threshold:
                    if run_start is None:
                        run_start=i
                elif run_start is not None:
                    start_x=run_start*bar_w
                    width=max(0, min((i-run_start)*bar_w, w-1-start_x))
                    if width>0:
                        try: self.stdscr.addstr(row_y, start_x, fill_ch*width, fill_attr)
                        except curses.error: pass
                    run_start=None
            if run_start is not None:
                start_x=run_start*bar_w
                width=max(0, min((len(heights)-run_start)*bar_w, w-1-start_x))
                if width>0:
                    try: self.stdscr.addstr(row_y, start_x, fill_ch*width, fill_attr)
                    except curses.error: pass
        try:
            self.stdscr.addstr(h-3,0,"-"*max(0,w-1),self.green)
            shuffle_label = self._shuffle_mode_label() if hasattr(self, "_shuffle_mode_label") else "Shuffle off"
            self.stdscr.addstr(h-2,0,f"[h/v/q/Esc] Exit  [x] {shuffle_label}  [SPACE] Play/Pause  [>] Next  [<] Prev"[:max(0,w-1)], self.green)
            self.stdscr.addstr(h-1,0,"Using your sink's MONITOR; set SKULLIFY_PULSE_SOURCE to force a specific one.", self.bold_green)
        except curses.error: pass
        self.stdscr.refresh()

    # ---- main loop ----
    def run(self):
        curses.curs_set(0); self.view_home(remember=False)
        while True:
            self.draw()
            ch=self.stdscr.getch()
            if ch==-1: continue
            if ch==ord('q'):
                if self._is_ascii_view():
                    self._finish_ascii_picker()
                break
            elif self._is_back_key(ch):
                self.go_back(message="Cancelled." if self.view=="choose_playlist" else None)
            elif ch in (curses.KEY_UP, ord('k')):
                self._move_selection(-1)
                self.preview_selected_ascii_art()
            elif ch in (curses.KEY_DOWN, ord('j')):
                self._move_selection(1)
                self.preview_selected_ascii_art()
            elif ch==curses.KEY_PPAGE:
                self._page_selection(-1)
                self.preview_selected_ascii_art()
            elif ch==curses.KEY_NPAGE:
                self._page_selection(1)
                self.preview_selected_ascii_art()
            elif ch==curses.KEY_HOME:
                self._jump_selection(False)
                self.preview_selected_ascii_art()
            elif ch==curses.KEY_END:
                self._jump_selection(True)
                self.preview_selected_ascii_art()
            elif ch==curses.KEY_RESIZE: pass
            elif ch==ord('h'): self.go_home()
            elif ch==ord('?'): self.view_help()
            elif ch in (ord('s'), ord('/')):
                if self.view=="ascii_art": self.search_ascii_art()
                else: self.view_search()
            elif ch==ord('p'):
                if self.view=="ascii_art": self.preview_ascii_art()
                else: self.view_playlists()
            elif ch==ord('a'):
                if self.view=="ascii_art": self.view_ascii_art("", remember=False)
                else: self.view_ascii_art()
            elif ch==ord('n'):
                if self.view=="playlists": self.create_playlist()
            elif ch==ord('l'):
                if self.view=="artist" and self.current_artist: self.view_albums(self.current_artist.get("id"))
            elif ch==ord('A'):
                if self.view in ("artist","album_tracks","tracks","playlist_tracks","liked_songs") and 0<=self.selected<len(self.items):
                    it=self.items[self.selected]
                    if it.get("kind")=="track": self.select_playlist_and_add(it["uri"])
            elif ch==ord('r'):
                if self.view=="playlist_tracks": self.remove_selected_playlist_track()
            elif ch==ord('v'): self.open_visualizer()
            elif ch==ord('d'): self.view_devices(remember=self.view!="devices")
            elif ch==ord(' '): self.toggle_play_pause()
            elif ch==ord('>'): self.next_track()
            elif ch==ord('<'): self.prev_track()
            elif ch==ord('x'): self.cycle_shuffle_mode()
            elif ch in (ord('='), ord('+')): self.anim_interval=clamp(self.anim_interval*0.8,0.02,0.5); self.message=f"Animation speed: {self.anim_interval:.2f}s/frame (faster)"
            elif ch==ord('-'): self.anim_interval=clamp(self.anim_interval*1.25,0.02,0.5); self.message=f"Animation speed: {self.anim_interval:.2f}s/frame (slower)"
            elif ch==ord('0'): self.anim_interval=DEFAULT_ANIM_INTERVAL; self.message=f"Animation speed reset: {self.anim_interval:.2f}s/frame"
            elif ch==ord(']'): self.head_scale=clamp(self.head_scale*1.10,0.2,2.0); self.message=f"Header scale: {self.head_scale:.2f}"
            elif ch==ord('['): self.head_scale=clamp(self.head_scale/1.10,0.2,2.0); self.message=f"Header scale: {self.head_scale:.2f}"
            elif ch==ord('\\'): self.head_layout={"auto":"right","right":"below","below":"auto"}[self.head_layout]; self.message=f"Layout: {self.head_layout}"
            elif ch in (10,13):
                if not (0<=self.selected<len(self.items)): continue
                item=self.items[self.selected]; kind=item.get("kind")
                if kind=="action":
                    act=item.get("action")
                    if act=="search": self.view_search()
                    elif act=="playlists": self.view_playlists()
                    elif act=="visualizer_system": self.open_visualizer()
                    elif act=="devices": self.view_devices()
                    elif act=="ascii_art": self.view_ascii_art()
                    elif act=="help": self.view_help()
                    elif act=="new_playlist": self.create_playlist()
                elif kind=="artist": self.view_artist(item["id"])
                elif kind=="album": self.current_album=item["raw"]; self.view_album_tracks(item["id"])
                elif kind=="track": self.start_playback_for_track(item["uri"])
                elif kind=="liked_songs": self.view_liked_tracks()
                elif kind=="playlist": self.view_playlist_tracks(item["id"])
                elif kind=="device": self.use_device(item)
                elif kind=="ascii_art": self.set_ascii_art(item)
                elif kind=="add_playlist": self.add_pending_track_to_playlist(item)

def main(stdscr): Skullify(stdscr).run()
import webbrowser

# Now-playing context browser helpers
def _fmt_ms(ms):
    try:
        s = int((ms or 0)//1000)
        return f"{s//60}:{s%60:02d}"
    except Exception:
        return "0:00"
def _sp_client(self):
    for n in ('sp','spotify','api','client'):
        if hasattr(self, n) and getattr(self, n) is not None:
            return getattr(self, n)
    raise RuntimeError('Spotify client not found on self.')
def np_context_browser(self):
    import curses
    h, w = self.stdscr.getmaxyx()
    banner, data = _np_banner(self, w)
    title, rows = _np_fetch_context_items(self, data)
    sel = 0; top = 0
    while True:
        self.stdscr.erase()
        self.stdscr.addnstr(0, 0, title.ljust(w), w, curses.A_BOLD)
        vis = max(1, h - 3)
        if sel < top: top = sel
        if sel >= top + vis: top = sel - vis + 1
        for i in range(top, min(len(rows), top+vis)):
            y = 1 + (i - top)
            txt = rows[i][0]
            attr = curses.A_REVERSE if i == sel else 0
            self.stdscr.addnstr(y, 0, txt.ljust(w), w, attr)
        self.stdscr.addnstr(h-1, 0, '[q] Quit  [Enter] Play  ↑/↓ Move'.ljust(w), w)
        self.stdscr.refresh()
        ch = self.stdscr.getch()
        if ch in (ord('q'), ord('Q')): break
        elif ch in (curses.KEY_UP, ord('k')):   sel = max(0, sel-1)
        elif ch in (curses.KEY_DOWN, ord('j')): sel = min(len(rows)-1, sel+1) if rows else 0
        elif ch in (10, 13):
            try:
                sp = _sp_client(self)
                tid = rows[sel][1] if (rows and 0 <= sel < len(rows)) else None
                if tid:
                    dev_id = None
                    try:
                        pb = sp.current_playback()
                        if pb and pb.get('device'): dev_id = pb['device'].get('id')
                    except Exception: pass
                    sp.start_playback(device_id=dev_id, uris=[f'spotify:track:{tid}'])
                    self.message = 'Playing selection…'
                    break
            except Exception:
                break
def _np_open_context(self):
    data = getattr(self,'_np_cache', None)
    try:
        if not data:
            data = _np_fetch(self)
            setattr(self,'_np_cache', data)
    except Exception:
        data = None
    if not data:
        self.message = 'No context for current track.'
        return
    try:
        np_context_browser(self)
    except Exception:
        uri = data.get('ctx_uri') or ''
        if uri.startswith('spotify:'):
            url = 'https://open.' + uri.replace(':','/').replace('spotify/','spotify.com/')
            try:
                import webbrowser; webbrowser.open(url); self.message = 'Opened in browser.'
            except Exception:
                self.message = url
def _np_fetch(self):
    # Prefer current_playback(); fall back to currently_playing()
    try:
        sp = _sp_client(self)
    except Exception:
        return None
    pb = None
    for fn in ('current_playback','currently_playing'):
        try:
            if hasattr(sp, fn):
                pb = getattr(sp, fn)()
                if pb: break
        except Exception:
            pb = None
    if not isinstance(pb, dict):
        return None
    item = pb.get('item') or pb.get('track') or None
    if not item:
        return None
    name    = item.get('name','') or ''
    artists = ', '.join((a or {}).get('name','') for a in (item.get('artists') or []))
    dur     = item.get('duration_ms') or 0
    prog    = pb.get('progress_ms') or 0
    ctx     = pb.get('context') or {}
    ctx_uri = ctx.get('uri') or ''
    ctx_type= (ctx.get('type') or '').lower()
    ctx_name = ''
    try:
        if ctx_uri.startswith('spotify:playlist:') and hasattr(sp,'playlist'):
            pid = ctx_uri.split(':')[-1]
            ctx_name = (sp.playlist(pid, fields='name') or {}).get('name','')
        elif ctx_uri.startswith('spotify:album:') and hasattr(sp,'album'):
            aid = ctx_uri.split(':')[-1]
            ctx_name = (sp.album(aid) or {}).get('name','')
    except Exception:
        pass
    device = ''
    try:
        if pb.get('device'):
            device = pb['device'].get('name','')
    except Exception:
        pass
    return {
        'track': name, 'artists': artists, 'dur': dur, 'prog': prog,
        'ctx_type': ctx_type, 'ctx_uri': ctx_uri, 'ctx_name': ctx_name,
        'device': device, 'track_id': item.get('id'), 'from_recent': False
    }

def _np_banner(self, w):
    import time as _t
    now = int(_t.time())
    last = getattr(self,'_np_last', 0)
    if now != last:
        setattr(self,'_np_last', now)
        try:
            setattr(self,'_np_cache', _np_fetch(self))
        except Exception:
            setattr(self,'_np_cache', None)
    data = getattr(self,'_np_cache', None)
    if not data:
        return ('Now Playing: (unknown)', None)
    left = (f"{data.get('artists','')} — {data.get('track','')}").strip(' —')
    prog = _fmt_ms(data.get('prog',0))
    dur  = _fmt_ms(data.get('dur',0))
    src  = ''
    if data.get('ctx_type') == 'playlist' and data.get('ctx_uri'):
        src = f" • from Playlist {data.get('ctx_name','')}"
    elif data.get('ctx_type') == 'album' and data.get('ctx_uri'):
        src = f" • from Album {data.get('ctx_name','')}"
    line = f"Now Playing: {left or '(unknown)'}  [{prog}/{dur}]{src}"
    return (line[:max(0,w)], data)

def _np_fetch_context_items(self, data):
    '''Return (title, rows) where rows are (display_text, track_id).'''
    rows = []; title = ''
    try:
        sp = _sp_client(self)
    except Exception:
        return ('(no Spotify client)', rows)
    uri = (data or {}).get('ctx_uri') or ''
    if not uri:
        tid = (data or {}).get('track_id')
        if not tid:
            return ('(no context)', rows)
        try:
            tr = sp.track(tid) or {}
            alb = (tr.get('album') or {}).get('id')
            if not alb: return ('(no context)', rows)
            uri = f'spotify:album:{alb}'
        except Exception:
            return ('(no context)', rows)
    if uri.startswith('spotify:playlist:'):
        pid = uri.split(':')[-1]
        pl = sp.playlist(pid)
        title = f"Playlist: {pl.get('name','')}"
        items = (pl.get('tracks') or {}).get('items') or []
        while True:
            for it in items:
                t = (it.get('track') or {})
                if not t: continue
                name = t.get('name',''); artists = ', '.join(a['name'] for a in (t.get('artists') or []))
                dur = _fmt_ms(t.get('duration_ms') or 0)
                rows.append((f"{artists} — {name} [{dur}]", t.get('id')))
            nxt = (pl.get('tracks') or {}).get('next')
            if nxt:
                pl = sp.next(pl.get('tracks')); items = (pl.get('tracks') or {}).get('items') or []
            else:
                break
    elif uri.startswith('spotify:album:'):
        aid = uri.split(':')[-1]
        al = sp.album(aid); title = f"Album: {al.get('name','')}"
        offs = 0
        while True:
            at = sp.album_tracks(aid, offset=offs, limit=50)
            items = at.get('items') or []
            for t in items:
                name = t.get('name',''); artists = ', '.join(a['name'] for a in (t.get('artists') or []))
                dur = _fmt_ms(t.get('duration_ms') or 0)
                rows.append((f"{artists} — {name} [{dur}]", t.get('id')))
            if at.get('next'): offs += 50
            else: break
    else:
        title = '(unsupported context)'
    return (title, rows)

def _track_uri_from_text(text: str) -> Optional[str]:
    text = (text or "").strip()
    if text.startswith("spotify:track:"):
        return text
    m = re.search(r"open\.spotify\.com/track/([A-Za-z0-9]+)", text)
    if m:
        return f"spotify:track:{m.group(1)}"
    return None

def _track_display(track: Dict[str, Any]) -> str:
    artists = ", ".join(a.get("name", "") for a in track.get("artists", []) if a.get("name"))
    name = track.get("name") or "Unknown track"
    return f"{name} - {artists}" if artists else name

def _find_track_for_query(sp, query: str) -> Optional[Dict[str, Any]]:
    uri = _track_uri_from_text(query)
    if uri:
        try:
            track = sp.track(uri)
            if track:
                track["uri"] = uri
                return track
        except Exception:
            return {"uri": uri, "name": uri, "artists": []}

    res = sp.search(q=query, type="track", limit=1)
    tracks = ((res or {}).get("tracks") or {}).get("items") or []
    return tracks[0] if tracks else None

def quick_play_query(
    query: str,
    preferred_name: str = "",
    prefer_terminal: bool = PREFER_TERMINAL_DEVICE,
    prefer_desktop: bool = PREFER_DESKTOP_DEVICE,
    launch_if_needed: bool = True,
) -> int:
    sp = make_spotify_client()
    track = _find_track_for_query(sp, query)
    if not track or not track.get("uri"):
        print(f"No Spotify track found for: {query}", file=sys.stderr)
        return 1

    device = ensure_spotify_device(
        sp,
        preferred_name=preferred_name,
        prefer_terminal=prefer_terminal,
        prefer_desktop=prefer_desktop,
        launch_if_needed=launch_if_needed,
        transfer=True,
        force_play_on_transfer=False,
    )
    ok, err, used_device = start_spotify_track(
        sp,
        track["uri"],
        device,
        prefer_terminal=prefer_terminal,
        prefer_desktop=prefer_desktop,
    )
    if ok:
        device_name = (used_device or {}).get("name") or TERMINAL_DEVICE_NAME
        print(f"Playing: {_track_display(track)} on {device_name}")
        return 0

    print(f"Playback failed for {_track_display(track)}: {err}", file=sys.stderr)
    print("Try `spotifyd authenticate`, then `skullify --devices`.", file=sys.stderr)
    return 2

def list_devices_cli(
    preferred_name: str = "",
    prefer_terminal: bool = PREFER_TERMINAL_DEVICE,
    prefer_desktop: bool = PREFER_DESKTOP_DEVICE,
    launch_if_needed: bool = True,
) -> int:
    sp = make_spotify_client()
    devices = fetch_spotify_devices(sp)
    picked = pick_spotify_device(
        devices,
        preferred_name=preferred_name,
        prefer_terminal=prefer_terminal,
        prefer_desktop=prefer_desktop,
        allow_fallback=not prefer_terminal,
    )
    if launch_if_needed and prefer_terminal and not picked:
        if launch_terminal_spotify():
            wait_for_spotify_device(
                sp,
                preferred_name=preferred_name,
                prefer_terminal=prefer_terminal,
                prefer_desktop=prefer_desktop,
            )
        devices = fetch_spotify_devices(sp)
        picked = pick_spotify_device(
            devices,
            preferred_name=preferred_name,
            prefer_terminal=prefer_terminal,
            prefer_desktop=prefer_desktop,
            allow_fallback=not prefer_terminal,
        )
    if not devices:
        print("No Spotify Connect devices found. Run `spotifyd authenticate`, then try again.")
        return 1
    devices.sort(key=lambda d: (not _is_terminal_device(d), not d.get("is_active", False), d.get("name","").lower()))
    for d in devices:
        marker = "*" if picked and d.get("id") == picked.get("id") else " "
        print(f"{marker} {spotify_device_label(d)}")
    return 0

def _ascii_details(row: Dict[str, str]) -> str:
    title = row.get("title") or row["name"]
    artist = row.get("artist", "")
    details = f"{title}"
    if artist:
        details += f" by {artist}"
    return details

def list_ascii_cli(search: str = "") -> int:
    rows = search_ascii_animations(search)
    print("Available ASCII animations")
    if search:
        print(f"Search: {search}")
    print(f"Local pack folder: {_redact_text(str(ASCII_DIR))}")
    print()
    if not rows:
        print("No matching ASCII animations found.")
        return 1
    for row in rows:
        marker = "*" if row["name"] == ASCII_ANIMATION_NAME else "-"
        print(f"{marker} {row['name']}: {_ascii_details(row)}")
        if row.get("source_url"):
            print(f"  source: {row['source_url']}")
        if row.get("license"):
            print(f"  license: {row['license']}")
        if row.get("tags"):
            print(f"  tags: {row['tags']}")
    return 0

def preview_ascii_cli(spec: str) -> int:
    loaded = try_load_ascii_animation(spec)
    if not loaded:
        print(f"ASCII animation not found or could not be loaded: {spec}", file=sys.stderr)
        return 1
    frames, name, path = loaded
    print(f"{name} ({len(frames)} frame{'s' if len(frames) != 1 else ''})")
    print(f"Source: {_redact_text(str(path))}")
    print()
    for line in frames[0]:
        print(line)
    return 0

def save_ascii_cli(spec: str) -> int:
    loaded = try_load_ascii_animation(spec)
    if not loaded:
        print(f"ASCII animation not found or could not be loaded: {spec}", file=sys.stderr)
        print(f"Put licensed .json, .js, .txt, or .asc packs in {_redact_text(str(ASCII_DIR))}.", file=sys.stderr)
        return 1

    _, name, path = loaded
    config = _load_config()
    config["ascii_animation"] = (spec or "jellyfish").strip() or "jellyfish"
    _save_config(config)
    set_active_ascii_animation(spec)
    print(f"Saved ASCII animation: {name}")
    print(f"Source: {_redact_text(str(path))}")
    print(f"Config: {_redact_text(str(CONFIG_PATH))}")
    return 0

def select_ascii_cli(initial_search: str = "") -> int:
    search = (initial_search or "").strip()
    while True:
        rows = search_ascii_animations(search)
        print()
        print("Select ASCII animation")
        if search:
            print(f"Search: {search}")
        if rows:
            for index, row in enumerate(rows[:25], start=1):
                print(f"{index:2}. {row['name']} - {_ascii_details(row)}")
            if len(rows) > 25:
                print(f"... {len(rows) - 25} more matches. Type a narrower search.")
        else:
            print("No matches.")
        print()
        answer = input("Number to save, search text, or blank to cancel: ").strip()
        if not answer:
            print("No ASCII animation changed.")
            return 0
        if answer.isdigit():
            choice = int(answer)
            if 1 <= choice <= min(len(rows), 25):
                return save_ascii_cli(rows[choice - 1]["name"])
            print("That number is not in the current list.")
            continue
        search = answer

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skullify",
        description="Skullify Spotify CLI/TUI. With a song query, plays the best track match through the terminal Spotify Connect player.",
    )
    parser.add_argument("query", nargs="*", help="song, artist, Spotify track URI, or Spotify track URL to play")
    parser.add_argument("-d", "--device", dest="device_name", help="prefer a Spotify device by name substring")
    parser.add_argument("--devices", action="store_true", help="list available Spotify Connect devices and exit")
    parser.add_argument("--allow-non-terminal", action="store_true", help="allow playback on a non-terminal Spotify Connect device")
    parser.add_argument("--no-launch", action="store_true", help="do not try to start the terminal Spotify player")
    parser.add_argument("--tui", action="store_true", help="open the interactive Skullify browser even when options are passed")
    parser.add_argument("--setup", action="store_true", help="run the first-time Spotify and terminal-player setup wizard")
    parser.add_argument("--doctor", action="store_true", help="check local config, auth cache paths, and optional system dependencies")
    parser.add_argument("--list-ascii", action="store_true", help="list bundled and local ASCII animations")
    parser.add_argument("--search-ascii", metavar="SEARCH", help="search bundled and local ASCII animations")
    parser.add_argument("--select-ascii", nargs="?", const="", metavar="SEARCH", help="search and choose the default ASCII animation")
    parser.add_argument("--preview-ascii", metavar="NAME_OR_PATH", help="print the first frame of an ASCII animation")
    parser.add_argument("--ascii", metavar="NAME_OR_PATH", help="use an ASCII animation for this run")
    parser.add_argument("--set-ascii", metavar="NAME_OR_PATH", help="save the default ASCII animation")
    parser.add_argument("--reset-auth", action="store_true", help="delete Skullify's local Spotify token cache")
    parser.add_argument("--logout", action="store_true", help="alias for --reset-auth")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser

def run_entrypoint(argv: Optional[List[str]] = None) -> int:
    global PREF_DEVICE_NAME, PREFER_DESKTOP_DEVICE, PREFER_TERMINAL_DEVICE

    args = build_arg_parser().parse_args(argv)
    if args.list_ascii:
        return list_ascii_cli()
    if args.search_ascii:
        return list_ascii_cli(args.search_ascii)
    if args.select_ascii is not None:
        return select_ascii_cli(args.select_ascii)
    if args.preview_ascii:
        return preview_ascii_cli(args.preview_ascii)
    if args.set_ascii:
        return save_ascii_cli(args.set_ascii)
    if args.ascii and not set_active_ascii_animation(args.ascii):
        print(f"ASCII animation not found or could not be loaded: {args.ascii}", file=sys.stderr)
        print(f"Run `skullify --list-ascii` or place packs in {_redact_text(str(ASCII_DIR))}.", file=sys.stderr)
        return 1
    if args.setup:
        return run_setup()
    if args.doctor:
        return run_doctor()
    if args.reset_auth or args.logout:
        return reset_auth()

    query = " ".join(args.query).strip()
    preferred_name = (args.device_name or PREF_DEVICE_NAME).strip().lower()
    prefer_terminal = not args.allow_non_terminal
    prefer_desktop = False if prefer_terminal else PREFER_DESKTOP_DEVICE
    launch_if_needed = not args.no_launch

    if _missing_python_packages():
        return show_runtime_setup_guidance()

    if args.device_name:
        PREF_DEVICE_NAME = preferred_name
    PREFER_DESKTOP_DEVICE = prefer_desktop
    PREFER_TERMINAL_DEVICE = prefer_terminal

    try:
        if args.devices:
            return list_devices_cli(
                preferred_name=preferred_name,
                prefer_terminal=prefer_terminal,
                prefer_desktop=prefer_desktop,
                launch_if_needed=launch_if_needed,
            )
        if query and not args.tui:
            return quick_play_query(
                query,
                preferred_name=preferred_name,
                prefer_terminal=prefer_terminal,
                prefer_desktop=prefer_desktop,
                launch_if_needed=launch_if_needed,
            )

        curses.wrapper(main)
        return 0
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 2

if __name__=="__main__":
    raise SystemExit(run_entrypoint())
