#!/bin/sh
# CI: install in a stock python:3.12-alpine container and drive the command on PATH
# against the simulator. Proves the dependencies install on musl, re-running
# upgrades the code, the layout and file modes, that the command drops to user
# mcp-pixelcade, that it picks up /etc/mcp-server-pixelcade/env, and that the
# caller's environment beats that file.
#   docker run --rm -v "$PWD:/src" -w /src python:3.12-alpine sh deploy/alpine/smoke-test.sh
set -eux

# On failure, say where as a GitHub annotation (the job log isn't always at hand; annotations are).
report() {
	status=$?
	if [ "$status" != 0 ]; then
		tail_log=$(tail -n 30 "${STEP_LOG:-/dev/null}" 2>/dev/null | sed ':a;N;$!ba;s/%/%25/g;s/\n/%0A/g')
		echo "::error title=Alpine smoke test failed (exit $status)::last step: ${STEP:-?}%0A$tail_log"
	fi
}
trap report EXIT

STEP='install'
sh deploy/alpine/install.sh /src

STEP='upgrade'
# Re-running install.sh must replace the code even though the version number hasn't changed
# (pip would otherwise keep the installed copy). Install a copy with a marker, then look for it.
cp -r /src /tmp/src2
echo '# smoke-test upgrade marker' >>/tmp/src2/src/pixelcade_mcp/__init__.py
sh /tmp/src2/deploy/alpine/install.sh /tmp/src2
grep -q 'smoke-test upgrade marker' /opt/mcp-server-pixelcade/venv/lib/python3*/site-packages/pixelcade_mcp/__init__.py

STEP='pytest on musl'
/opt/mcp-server-pixelcade/venv/bin/pip install --quiet "/src[dev]"
STEP_LOG=/tmp/pytest.log
cd /src
/opt/mcp-server-pixelcade/venv/bin/pytest -q -p no:cacheprovider -rf >"$STEP_LOG" 2>&1 || { cat "$STEP_LOG"; false; }
tail -n 3 "$STEP_LOG"
cd /
STEP_LOG=''

ETC=/etc/mcp-server-pixelcade
STATE=/var/lib/mcp-server-pixelcade
STEP='file modes'
[ "$(stat -c '%a %U:%G' $ETC)" = "750 root:mcp-pixelcade" ]
[ "$(stat -c '%a %U:%G' $ETC/env)" = "640 root:mcp-pixelcade" ]
[ "$(stat -c '%a %U' $STATE)" = "700 mcp-pixelcade" ]
[ "$(command -v mcp-server-pixelcade)" = /usr/local/bin/mcp-server-pixelcade ]

STEP='simulator, as the user'
# Started through the command on PATH: the config it writes must belong to mcp-pixelcade.
STEP_LOG=/tmp/sim.log
mcp-server-pixelcade simulate --port 8090 --write-config "$STATE/sim" >"$STEP_LOG" 2>&1 &
for _ in $(seq 100); do [ -e "$STATE/sim/config.json" ] && break; sleep 0.1; done
[ "$(stat -c %U "$STATE/sim/config.json")" = mcp-pixelcade ]

STEP='doctor, settings from env'
sed -i 's|^PIXELCADE_URL=.*|PIXELCADE_URL=http://127.0.0.1:8090|' $ETC/env
mcp-server-pixelcade doctor --timeout 5

STEP='call'
out=$(mcp-server-pixelcade call show_text text=HI)
echo "$out" | grep -q '"outcome": "done"'
grep -q 'GET /text?t=HI' "$STEP_LOG"

STEP='caller beats env'
# Nothing listens on port 9, so doctor must fail if the caller's URL won.
if PIXELCADE_URL=http://127.0.0.1:9 mcp-server-pixelcade doctor --timeout 2; then false; fi
out=$(PIXELCADE_DRY_RUN=1 mcp-server-pixelcade call blank_display)
echo "$out" | grep -q '"outcome": "dry_run"'

STEP='run as the user directly'
su-exec mcp-pixelcade mcp-server-pixelcade call get_status | grep -q '"reachable": true'

echo "Alpine smoke test passed"
