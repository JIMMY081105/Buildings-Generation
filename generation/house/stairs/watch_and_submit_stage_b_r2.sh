#!/bin/bash
# Poll read-only capacity and submit once when a real GPU becomes available.
set -uo pipefail

ROOT=/data/run01/scvj260/building_batch_20260819
SUBMIT="$ROOT/release_r4/scripts/submit_stage_b_r2_once.sh"
LOG="$ROOT/pre_d_evidence/stage_b_r3_capacity_watch.log"
LOCK="$ROOT/pre_d_evidence/stage_b_r3_capacity_watch.lock"
USER_NAME="$(id -un)"

exec 7>"$LOCK"
if ! flock -n 7; then
  printf '%s watcher already active; exiting\n' "$(date -Is)" >>"$LOG"
  exit 0
fi

while true; do
  existing="$(squeue -u "$USER_NAME" -h -n bldg_b_stepfix2 -o '%A|%T' | head -n1)"
  if [ -n "$existing" ]; then
    printf '%s Stage-B r3 job exists: %s\n' "$(date -Is)" "$existing" >>"$LOG"
    exit 0
  fi
  output="$(bash "$SUBMIT" 2>&1)"
  rc=$?
  printf '%s rc=%s %s\n' "$(date -Is)" "$rc" \
    "$(printf '%s' "$output" | tr '\n' ';')" >>"$LOG"
  [ "$rc" -eq 0 ] && exit 0
  [ "$rc" -eq 5 ] && exit 0
  sleep 15
done
