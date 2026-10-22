#!/usr/bin/env bash
# Run a CI step, keep its full output in build/logs/<name>.log, and on failure surface
# the tail of the log as a workflow annotation (readable without log-download access).
# Usage: scripts/ci_run.sh <name> <command...>
set -uo pipefail
name="$1"; shift
mkdir -p build/logs
log="build/logs/$name.log"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# The dashboard is built from the upstream Hermes checkout.  Before compiling
# it for the Android payload, apply our CSS-only phone/Chinese compatibility
# layer.  Keeping this hook here means every CI build gets the patch even as
# the upstream source revision changes.
{
  if [ "$name" = "web-build" ]; then
    python3 "$script_dir/patch_dashboard_android.py" "$PWD" || exit $?
  fi
  "$@"
} 2>&1 | tee "$log"
status=${PIPESTATUS[0]}
annotate() {  # level title file lines
  local body
  body="$(tail -n "$4" "$3" | sed 's/%/%25/g; s/\r//g' | awk '{printf "%s%%0A", $0}')"
  echo "::$1 title=$2::$body"
}
if [ "$status" -ne 0 ]; then
  annotate error "$name failed (exit $status)" "$log" 80
fi
exit "$status"
