#!/usr/bin/env bash
# Runs inside WSL Ubuntu. Called by start.ps1 on Windows; also usable directly:
#   bash scripts/run.sh <command>
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$HOME/venvs/lumen-voice"
export PATH="$HOME/.local/bin:$PATH"
export PYTHONUTF8=1
cd "$ROOT"

py() { "$VENV/bin/python" "$@"; }

setup() {
  command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
  [ -d "$VENV" ] || uv venv --python 3.12 "$VENV"
  VIRTUAL_ENV="$VENV" uv pip install -q -r requirements.txt
  "$VENV/bin/python" agent.py download-files
  [ -f .env ] || { cp .env.example .env; echo "created .env from .env.example: fill in your keys"; }
  echo "setup ok ($("$VENV/bin/python" --version))"
}

check() {
  py -m lumen.data | tail -1
  py -m lumen.tools
  py -m lumen.assistant
  py -c "import agent, lumen.smoke, evals.voice_bot; print('agent imports ok')"
}

# Starts a worker on the given pipeline, phones it with the voice bot, stops the worker.
voicebot() {
  local pipeline="$1" script="$2" log="logs/worker-$1.log"
  mkdir -p logs
  # A worker left over from an interrupted run holds port 8081 and would take the job.
  pkill -f "python agent.py start" 2>/dev/null && sleep 1
  PIPELINE="$pipeline" py agent.py start >"$log" 2>&1 &
  WORKER_PID=$!  # global: the EXIT trap runs after this function's locals are gone
  trap 'kill "$WORKER_PID" 2>/dev/null; wait "$WORKER_PID" 2>/dev/null' EXIT
  trap 'exit 130' INT TERM  # route signals through the EXIT trap so the worker is stopped
  for _ in $(seq 60); do grep -q "registered worker" "$log" && break; sleep 0.5; done
  grep -q "registered worker" "$log" || { echo "worker did not register; see $log"; tail -20 "$log"; return 1; }
  py -m evals.voice_bot --pipeline "$pipeline" --script "$script"
}

# The demo: agent worker + the admin call page, one command; Ctrl+C stops both.
app() {
  mkdir -p logs
  pkill -f "python agent.py start" 2>/dev/null && sleep 1
  py agent.py start >logs/worker.log 2>&1 &
  WORKER_PID=$!
  trap 'kill "$WORKER_PID" 2>/dev/null; wait "$WORKER_PID" 2>/dev/null' EXIT
  trap 'exit 130' INT TERM
  for _ in $(seq 60); do grep -q "registered worker" logs/worker.log && break; sleep 0.5; done
  grep -q "registered worker" logs/worker.log || { echo "worker did not register; see logs/worker.log"; return 1; }
  echo "agent worker ready (PIPELINE=$(grep -m1 '^PIPELINE=' .env | cut -d= -f2)); logs in logs/worker.log"
  py web/server.py
}

case "${1:-help}" in
  setup) setup ;;
  check) check ;;
  smoke) py -m lumen.smoke "${2:+--$2}" ;;
  keys) py scripts/check_keys.py ;;
  console) [ -n "${2:-}" ] && export BORROWER="$2"; py agent.py console ;;
  agent) [ -n "${2:-}" ] && export BORROWER="$2"; py agent.py start ;;
  dev) [ -n "${2:-}" ] && export BORROWER="$2"; py agent.py dev ;;
  app) app ;;
  voicebot) voicebot "${2:-ultravox}" "${3:-happy}" ;;
  shell) exec bash --rcfile <(echo "source ~/.bashrc 2>/dev/null; source $VENV/bin/activate; cd $ROOT") -i ;;
  *)
    echo "usage: start.ps1 <command>"
    echo "  app     THE DEMO: agent + call page on http://localhost:8090 (Ctrl+C stops both)"
    echo "  setup   create/update the WSL venv, install requirements, create .env"
    echo "  check   self-checks (no keys needed)"
    echo "  keys    check every key in .env against its provider"
    echo "  smoke [ultravox]  text-mode smoke test (cascade brain by default)"
    echo "  console [B001..B008]  talk to the agent with your laptop mic (PIPELINE from .env)"
    echo "  agent   [B001..B008]  run the worker on LiveKit Cloud; join from the Agents Playground"
    echo "  dev     [B001..B008]  same as agent, with auto-reload on code changes"
    echo "  voicebot [ultravox|cascade] [happy|hindi]  phone the agent with a voice bot: real latency + barge-in"
    echo "  shell   WSL shell in the project with the venv active"
    ;;
esac
