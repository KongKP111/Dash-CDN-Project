#!/usr/bin/env bash
# Run the baseline N times (headless), one CSV per run, with mn cleanup between.
#   sudo ./run_baseline_multi.sh 10 results
set -u
N=${1:-10}
OUTDIR=${2:-results}
cd "$(dirname "$0")"
mkdir -p "$OUTDIR"
for i in $(seq 1 "$N"); do
    run=$(printf "run_%02d" "$i")
    echo "================  $run  /  $N  ================"
    sudo mn -c >/dev/null 2>&1
    sudo python3 baseline_topo.py --headless --out "$OUTDIR/$run.csv"
    sleep 3
done
sudo mn -c >/dev/null 2>&1
echo
echo "All $N runs done.  Aggregate with:"
echo "    python3 aggregate_runs.py $OUTDIR"
