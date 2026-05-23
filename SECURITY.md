# Security

## Supported Versions

Security fixes are handled on the main branch until a formal release policy exists.

## Reporting A Vulnerability

Please open a private security advisory on GitHub if available. If not, open an issue with minimal details and ask for a private contact path. Do not post access tokens, refresh tokens, client secrets, logs, or screenshots containing account data.

## Secret Handling

Skullify should never ship a Spotify client secret or user token. The default auth flow is PKCE, which only requires each user to provide their own Spotify Client ID.

Local secrets and tokens live outside the repository:

- Config: `~/.config/skullify/config.json`
- Token cache: `~/.cache/skullify/spotify-token-cache.json`
- Logs/state: `~/.local/state/skullify/`

If a token is accidentally committed or shared, delete it from the repo and revoke/rotate it in Spotify. Deleting the file alone is not enough.
