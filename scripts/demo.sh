#!/usr/bin/env bash
# A two-minute guided tour of what this project does.
# Usage: ./scripts/demo.sh   (or: make demo)
set -euo pipefail
cd "$(dirname "$0")/.."

pause() { echo; read -rp "── press enter ──" _ < /dev/tty || true; echo; }
title() { printf '\n\033[1m%s\033[0m\n%s\n\n' "$1" "$(printf '─%.0s' $(seq 1 ${#1}))"; }

title "1. One cluster, ten seconds of its life, reproduced from the number 12"
python3 -m raftlab run --seed 12 --profile churn --timeline
pause

title "2. The same seed always gives the same run"
for _ in 1 2; do python3 -m raftlab run --seed 12 --profile churn | head -1; done
pause

title "3. Plant a real Raft bug and watch the harness catch it"
echo "\$ python3 -m raftlab fuzz --bug blind_truncate --seeds 20"
python3 -m raftlab fuzz --bug blind_truncate --seeds 20 || true
pause

title "4. Turn a failing seed into a minimal reproduction"
python3 -m raftlab shrink --seed 6 --bug commit_index_unclamped
pause

title "5. A bug no internal check can see: only linearizability finds it"
echo "\$ python3 -m raftlab fuzz --bug no_session_dedup --seeds 12"
python3 -m raftlab fuzz --bug no_session_dedup --seeds 12 || true
pause

title "6. What the fuzzer actually reaches"
python3 -m raftlab coverage --seeds 60 --profiles hostile | head -22

printf '\n\033[1mFull results:\033[0m RESULTS.md   ·   \033[1mHow it works:\033[0m docs/ARCHITECTURE.md\n\n'
