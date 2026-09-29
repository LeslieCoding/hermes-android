#!/system/bin/sh
# Runs inside the Android (termux-docker) container with the Termux prefix masked,
# so only what the app ships and what Android provides is available.
S=/data/data/com.termux/files/home/sim
echo "== id: $(id)"
echo "== hermes --version"
"$S/files/run.sh" --version 2>&1 | tail -n 20
"$S/files/run.sh" > "$S/server.log" 2>&1 &
PID=$!
"$S/files/run.sh" --py "$S/smoke_client.py" 9119 smoke-token
RC=$?
echo "== server log (tail)"
tail -n 60 "$S/server.log"
kill "$PID" 2>/dev/null
exit $RC
