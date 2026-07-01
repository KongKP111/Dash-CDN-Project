#!/usr/bin/env bash
# Run the 4-RSU baseline N times (headless), one CSV per run, with mn cleanup
# between runs. Starts (or reuses) a long-running Ryu controller container;
# `sudo mn -c` never touches it (--network host, separate process).
#
#   ./run_4rsu_multi.sh 10 results_4rsu
#
# Each run is tagged with its run_id (run_01, run_02, ...) via
# /tmp/current_run_id.txt, written by baseline_4rsu_topo.py itself -- the
# controller reads that file on every handover so /tmp/handover_times.csv
# rows carry the right run_id WITHOUT restarting the container per run.
set -u
N=${1:-10}
OUTDIR=${2:-results_4rsu}
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CTRL_FILE="$SCRIPT_DIR/../Ryu-SDN-Controller/sdn_controller.py"
cd "$SCRIPT_DIR"
mkdir -p "$OUTDIR"

if ! docker ps --format '{{.Names}}' | grep -qx ryu-ctrl; then
    echo "=== starting ryu-ctrl container ==="
    docker rm -f ryu-ctrl >/dev/null 2>&1
    docker run -d --restart=always --name ryu-ctrl --network host \
        -v /tmp:/tmp -v "$CTRL_FILE":/sdn_controller.py \
        osrg/ryu ryu-manager /sdn_controller.py --ofp-tcp-listen-port 6653
    sleep 3
else
    echo "=== reusing already-running ryu-ctrl container ==="
fi

# fresh handover log for THIS batch (the controller re-creates the header
# on its next write even if left running across batches, see sdn_controller.py).
# Root-owned (written by the container as root) -- needs sudo to remove, or a
# plain `rm -f` silently no-ops with "Operation not permitted" and old batches'
# rows keep accumulating under the same run_id labels.
sudo rm -f /tmp/handover_times.csv

for i in $(seq 1 "$N"); do
    run=$(printf "run_%02d" "$i")
    echo "================  $run  /  $N  ================"
    sudo mn -c >/dev/null 2>&1
    sudo python3 baseline_4rsu_topo.py --headless --run-id "$run" --out "$OUTDIR/$run.csv"
    # belt-and-suspenders: the topology script force-kills its own vlc/Xvfb,
    # but confirm nothing survived before the next run starts.
    pkill -9 -u "$USER" -f 'vlc -I dummy' 2>/dev/null
    pkill -9 -u "$USER" -x Xvfb 2>/dev/null
    sleep 3
done
sudo mn -c >/dev/null 2>&1

# snapshot the control-plane handover log alongside this batch's data-plane CSVs
cp -f /tmp/handover_times.csv "$OUTDIR/handover_times.csv" 2>/dev/null

echo
echo "All $N runs done.  Aggregate with:"
echo "    python3 aggregate_4rsu.py $OUTDIR"
echo "Control-plane handover times -> $OUTDIR/handover_times.csv"
