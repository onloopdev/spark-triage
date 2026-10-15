#!/usr/bin/env bash
# One command: ./run.sh
# Works in Git Bash on Windows, and on macOS / Linux.
# Sets up a virtualenv, pulls the open-weights model, writes the synthetic
# intake cards, runs the deterministic tests, then opens the nurse's desk.
set -euo pipefail
cd "$(dirname "$0")"

MODEL="${TRIAGE_MODEL:-qwen2.5:3b}"

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing: $1. $2"; exit 1; }; }
need npx "Install Node.js 18+ from https://nodejs.org (the borrowed filesystem MCP server runs with npx)."
need ollama "Install Ollama from https://ollama.com and start it."

# Find a real Python 3.11+ (on Windows 'python3' is often a Store shortcut).
PY=""
for cand in python3 python "py -3"; do
  if $cand -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then PY="$cand"; break; fi
done
[ -n "$PY" ] || { echo "Missing: Python 3.11+. Install it from https://python.org and tick 'Add to PATH'."; exit 1; }

[ -d .venv ] || $PY -m venv .venv
if [ -x .venv/Scripts/python.exe ]; then VPY=.venv/Scripts/python.exe; else VPY=.venv/bin/python; fi

$VPY -m pip install -q --upgrade pip
$VPY -m pip install -q -e ".[dev]"

ollama pull "$MODEL"
$VPY data/make_synthetic.py
$VPY -m pytest -q tests

export TRIAGE_MODEL="$MODEL"
export PYTHONUTF8=1
exec $VPY -m agent.cli "${@:-web}"
