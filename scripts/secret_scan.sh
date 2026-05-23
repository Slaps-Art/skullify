#!/usr/bin/env bash
set -euo pipefail

if ! command -v rg >/dev/null 2>&1; then
  echo "ripgrep (rg) is required for this lightweight scan." >&2
  exit 2
fi

bad_files=$(find . \
  -path './.git' -prune -o \
  \( -name 'launch.log' -o -name '*.log' -o -name '.cache' -o -name '.config' -o -name '.local' -o -name '.env' -o -name '.venv' -o -name 'config.json' -o -name 'spotify-token-cache.json' -o -name '*token*' \) \
  -print)

if [ -n "$bad_files" ]; then
  echo "$bad_files"
  echo "Private/generated files found." >&2
  exit 1
fi

patterns='(("?(access_token|refresh_token)"?[[:space:]]*:[[:space:]]*"[^"]{20,}")|(SPOTIPY_CLIENT_SECRET[[:space:]]*=[[:space:]]*[^<[:space:]][^[:space:]]+)|(client_secret[[:space:]]*=[[:space:]]*["'\''][^"'\'']{20,}["'\''])|(Authorization:[[:space:]]*Bearer[[:space:]]+[A-Za-z0-9._-]{20,})|(BQB[A-Za-z0-9_-]{20,})|(AQ[A-Za-z0-9_-]{20,})|(/home/jeremy))'

if rg -n --hidden \
  -g '!.git/**' \
  -g '!dist/**' \
  -g '!build/**' \
  -g '!*.egg-info/**' \
  -g '!scripts/secret_scan.sh' \
  -g '!.env.example' \
  "$patterns" .; then
  echo "Potential secret or private artifact matches found." >&2
  exit 1
else
  status=$?
  if [ "$status" -eq 1 ]; then
    echo "No obvious secrets found."
    exit 0
  fi
  exit "$status"
fi
