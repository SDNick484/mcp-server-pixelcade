# Changelog

## [Unreleased]

### Added

- `GET /pixelcade/mcp/healthz`, the health check under the MCP path (the only one a tunnel routing `pixelcade/*` reaches), and both health routes report the installed `commit` and `repo` from `/opt/mcp-server-pixelcade/COMMIT`, which `deploy/alpine/install.sh` now writes (pinning the default source to `main`'s current commit first). `remote.py` and the new `deploy/alpine/commit.sh` synced from mcp-server-shared a48209b; the install needs `git` (it already installed it).
- Every tool now has an authorization tier (`read`, `power-off`, `everyday` or `costly`). None is costly here; no tool asks for confirmation. Costly tools, where a server has them, ask the person for a yes and refuse where the app can't show the prompt.
- `MCP_ALLOWED_SERVICE_TOKENS` and `MCP_AUTOMATION_TIERS` settings (see the README).

### Changed

- A Cloudflare Access service token now gets 403 unless its client ID is in `MCP_ALLOWED_SERVICE_TOKENS`. An admitted token (an automated caller) may only call the tiers in `MCP_AUTOMATION_TIERS` (default `read,power-off`).
- `remote.py` and its tests synced from mcp-server-shared f4719e3 (also `ratelimit.py` and its tests).

## [0.1.0]

First release on PyPI.

### Added

- MCP server for Pixelcade LED marquees (V2 board) and Pixelcade Pulse lighting through pixelweb: game and console marquees, text, animations, widgets, artwork listing, and Pulse effects, fills and brightness (capped by the server).
- CLI: `serve` (stdio, or `--http` behind Cloudflare Access), `doctor`, `call` and `simulate` (a wire-level fake pixelweb, no hardware needed).
- `deploy/alpine/`: OpenRC service and install script.
