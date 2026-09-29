#!/system/bin/sh
# Runs inside the Android (termux-docker) container with most of the Termux prefix
# hidden, so only what the app ships and what Android provides is available.
# (The container's own toybox needs Termux libs, so only shell builtins are used here.)
# Pass 1 uses LD_LIBRARY_PATH like the app on arm64 phones; pass 2 relies on the
# $ORIGIN RUNPATHs alone (what x86 devices with ARM translation get).
S=/data/data/com.termux/files/home/sim
R=$S/files/hermes-agent
RC=0
first() { read -r line; echo "$1 -> $line"; while read -r _; do :; done; }
dump() { while IFS= read -r line; do echo "$line"; done < "$1"; }
PORT=9119
for mode in ld-path runpath-only; do
  if [ "$mode" = runpath-only ]; then export NO_LD_LIBRARY_PATH=1; PORT=9120; fi
  export SMOKE_PORT=$PORT
  echo "===== pass: $mode"
  "$S/files/run.sh" --exec "$R/venv/bin/python" --version 2>&1 | first python
  "$S/files/run.sh" --exec "$R/tools/node/bin/node" --version 2>&1 | first node
  "$S/files/run.sh" --exec "$R/tools/ripgrep/rg" --version 2>&1 | first rg
  "$S/files/run.sh" --exec "$R/tools/ffmpeg/bin/ffmpeg" -version 2>&1 | first ffmpeg
  "$S/files/run.sh" > "$S/server-$mode.log" 2>&1 &
  PID=$!
  if ! "$S/files/run.sh" --py "$S/smoke_client.py" "$PORT" smoke-token; then
    RC=1
    echo "== server log ($mode)"
    dump "$S/server-$mode.log"
  fi
  kill "$PID" 2>/dev/null
done
exit $RC
