#!/usr/bin/env bash
# Phase 5G Step 4 (AGENT.md, 2026-10-07): outer supervisor for the daemon
# process itself. recall.run's own per-loop supervisor (_supervised in
# recall/run.py) keeps one bad loop from taking down the asyncio process --
# this script is the next layer out, for the case the whole Python process
# dies anyway (e.g. an OS-level kill, a Ctrl-C, an unhandled error during
# startup before the loops even start). Restarts `python -m recall.run`
# 10s after any exit and logs every exit (time, code) to
# logs/recall-restarts.log.
#
# Usage: ./scripts/run_forever.sh (run from the repo root, venv active)

set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT_DIR/logs"
RESTART_LOG="$LOG_DIR/recall-restarts.log"
RESTART_DELAY_S=10

mkdir -p "$LOG_DIR"

cd "$ROOT_DIR"

while true; do
    start_ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    python -m recall.run
    code=$?
    end_ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "$end_ts  exited with code $code (started $start_ts) -- restarting in ${RESTART_DELAY_S}s" >> "$RESTART_LOG"
    sleep "$RESTART_DELAY_S"
done
