# Privacy

Skullify talks to Spotify's API using your own Spotify app credentials and OAuth consent.

## Data Accessed

Depending on the feature used, Skullify may read:

- Your Spotify profile display name or user id.
- Your playlists and playlist tracks.
- Your liked songs.
- Your current playback state, active devices, and now-playing track.

Skullify may also modify playback state, create playlists, add tracks to playlists, and remove tracks from playlists when you use those controls.

## Local Storage

Skullify stores local configuration at `~/.config/skullify/config.json`. Spotify OAuth tokens are cached at `~/.cache/skullify/spotify-token-cache.json`. Terminal-player logs are stored at `~/.local/state/skullify/terminal-player.log`.

Optional local ASCII animation packs can be stored at `~/.config/skullify/ascii/`. Animation pack metadata may include artist, source URL, and license text that you provide.

The repository should not contain real `.env` files, token caches, logs, or local scratch files.

## Logs

Skullify redacts obvious secrets from terminal-player logs and rotates the log when it grows too large. Logs can still contain private context such as device names or local paths, so review them before sharing.
