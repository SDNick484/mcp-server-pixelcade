#!/bin/sh
# Runs mcp-server-pixelcade with the settings in /etc/mcp-server-pixelcade/env.
# /usr/local/bin/mcp-server-pixelcade calls this as user mcp-pixelcade, so
#   mcp-server-pixelcade doctor
#   mcp-server-pixelcade call show_text text=HI
# work from any shell on this machine. With no arguments the CLI's default is
# `serve` over stdio, which is how a client could reach it over SSH later.
#
# A variable already in the caller's environment beats the file, so one-off
# overrides work as they do elsewhere:
#   PIXELCADE_URL=http://127.0.0.1:8090 mcp-server-pixelcade doctor   (a simulator)
set -eu
ENV_FILE="${MCP_PIXELCADE_ENV:-/etc/mcp-server-pixelcade/env}"
if [ -r "$ENV_FILE" ]; then
	caller=$(export -p)  # the caller's exports, quoted so the shell can re-read them
	set -a               # export every variable the file sets
	# shellcheck disable=SC1090
	. "$ENV_FILE"
	set +a
	eval "$caller"       # ...then put the caller's back on top
fi
exec /opt/mcp-server-pixelcade/venv/bin/mcp-server-pixelcade "$@"
