# Changelog

## [Unreleased]

## [0.1.0]

First release on PyPI.

### Added

- MCP server for Pixelcade LED marquees (V2 board) and Pixelcade Pulse lighting through pixelweb: game and console marquees, text, animations, widgets, artwork listing, and Pulse effects, fills and brightness (capped by the server).
- CLI: `serve` (stdio, or `--http` behind Cloudflare Access), `doctor`, `call` and `simulate` (a wire-level fake pixelweb, no hardware needed).
- `deploy/alpine/`: OpenRC service and install script.
