# Contributing

Thanks for helping improve Skullify.

## Safety Rules

Do not commit:

- `.env` or `.env.*` files other than `.env.example`
- Spotify access tokens, refresh tokens, client secrets, or auth caches
- `launch.log` or other logs
- `.venv`, `__pycache__`, build output, or local scratch files
- screenshots that show account names, private playlists, or tokens

Before opening a pull request, run:

```bash
python -m py_compile skullify/cli.py
scripts/secret_scan.sh
```

Use `skullify --doctor` when debugging local setup, and redact any output before posting it publicly.
