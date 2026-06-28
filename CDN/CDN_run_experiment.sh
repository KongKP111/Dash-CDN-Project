#!/usr/bin/env bash
# =============================================================================
#  CDN_run_experiment.sh  —  Run ONE CDN experiment situation and save logs
# -----------------------------------------------------------------------------
#  Usage:
#    sudo bash CDN_run_experiment.sh --sit 1 --round 1
#
#  Situations (SDN_Test_Case_Scenarios.pdf):
#    1 — Normal / Baseline          30 km/h  3 Mbps stable        (no stress)
#    2 — Light Handover (Urban)     30 km/h  2 Mbps constant      (mild drop)
#    3 — Heavy Handover (Suburban)  30 km/h  250 kbps at handover (heavy HO)
#    4 — Sudden Bandwidth Drop      30 km/h  100 kbps instant drop (dead zone)
#    5 — High Mobility (Highway)    60 km/h  3 Mbps stable        (high speed)
#    6 — Combined Stress Worst Case 60 km/h  100 kbps drop        (speed+drop)
#
#  Total runs: 6 situations × 3 speeds × 10 rounds = 180 CDN runs
# =============================================================================

set -euo pipefail

ARCH="cdn"
SIT=1
SPEED=30
ROUND=1

while [[ $# -gt 0 ]]; do
    case "$1" in
        --sit)   SIT="$2";   shift 2 ;;
        --speed) SPEED="$2"; shift 2 ;;
        --round) ROUND="$2"; shift 2 ;;
        *) echo "[ERROR] Unknown arg: $1"; exit 1 ;;
    esac
done

if [[ "$SIT" -lt 1 || "$SIT" -gt 6 ]]; then
    echo "[ERROR] --sit must be 1-6"; exit 1
fi

# ---------------------------------------------------------------------------
#  Paths
# ---------------------------------------------------------------------------
PROJECT="/home/kongpop/PSU_Project/Dash-CDN-Project"
TOPO="$PROJECT/CDN/topology/real_campus_live.py"
RYU_CTRL="$PROJECT/Ryu-SDN-Controller/cdn_switch_13.py"
RUN_ID="${ARCH}_sit${SIT}_spd${SPEED}_r${ROUND}"
LOG_DIR="/tmp/cdn_logs/${RUN_ID}"
RESULTS_DIR="$PROJECT/results/${ARCH}/sit${SIT}/speed${SPEED}/${RUN_ID}"

echo ""
echo "============================================================"
echo "  RUN: $RUN_ID"
echo "  arch=$ARCH  sit=$SIT  speed=$SPEED km/h  round=$ROUND"
echo "============================================================"

# ---------------------------------------------------------------------------
#  Cleanup helper
# ---------------------------------------------------------------------------
cleanup() {
    echo "[CLEANUP] Stopping all components..."
    sudo mn -c > /dev/null 2>&1 || true
    # Kill nginx instances launched inside Mininet namespaces
    for pid_file in /tmp/nginx_edge*.pid /tmp/nginx_origin.pid; do
        [ -f "$pid_file" ] && kill "$(cat "$pid_file")" 2>/dev/null || true
    done
    # Stop Ryu controller
    if [[ -n "${RYU_PID:-}" ]]; then
        kill "$RYU_PID" 2>/dev/null || true
    fi
    echo "[CLEANUP] Done."
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
#  Step 1: Pre-run cleanup
# ---------------------------------------------------------------------------
echo "[1/4] Pre-run cleanup..."
sudo mn -c > /dev/null 2>&1 || true
for pid_file in /tmp/nginx_edge*.pid /tmp/nginx_origin.pid; do
    [ -f "$pid_file" ] && kill "$(cat "$pid_file")" 2>/dev/null || true
done
sleep 2

# ---------------------------------------------------------------------------
#  Step 2: Start Ryu SDN controller (local, background)
# ---------------------------------------------------------------------------
RYU_PYTHON="/home/kongpop/PSU_Project/mininet-wifi/ryu-venv/bin/python3.8"
echo "[2/4] Starting Ryu CDN controller (python3.8 venv)..."
(cd "$PROJECT/Ryu-SDN-Controller" && \
 "$RYU_PYTHON" -m ryu.cmd.manager cdn_switch_13.py \
 --ofp-tcp-listen-port 6653) \
    > /tmp/ryu_${RUN_ID}.log 2>&1 &
RYU_PID=$!

for i in $(seq 1 30); do
    if sudo ss -tlnp 2>/dev/null | grep -q ':6653'; then
        echo "      Ryu is up! (${i}s)"
        break
    fi
    if [[ $i -eq 30 ]]; then
        echo "[ERROR] Ryu failed to start within 30 s. Check /tmp/ryu_${RUN_ID}.log"
        exit 1
    fi
    sleep 1
done

# ---------------------------------------------------------------------------
#  Step 3: Run topology + client (automated, no interactive CLI)
# ---------------------------------------------------------------------------
echo "[3/4] Running topology + client (this takes several minutes)..."
mkdir -p "$LOG_DIR"

sudo python3 "$TOPO" \
    --sit    "$SIT" \
    --speed  "$SPEED" \
    --round  "$ROUND" \
    --run-id "$RUN_ID" \
    --out-dir "$LOG_DIR" \
    --run-client \
    --no-gui \
    2>&1 | tee /tmp/topo_${RUN_ID}.log

# ---------------------------------------------------------------------------
#  Step 4: Save results
# ---------------------------------------------------------------------------
echo "[4/4] Saving results..."
mkdir -p "$RESULTS_DIR"
cp "$LOG_DIR"/cdn_measurements_*.csv "$RESULTS_DIR/" 2>/dev/null || true
cp "$LOG_DIR"/rssi_*.csv             "$RESULTS_DIR/" 2>/dev/null || true
cp /tmp/topo_${RUN_ID}.log           "$RESULTS_DIR/" 2>/dev/null || true
cp /tmp/ryu_${RUN_ID}.log            "$RESULTS_DIR/" 2>/dev/null || true
# Grab default RSSI log from topology working directory
cp "$PROJECT/CDN/topology/rssi_real_campus_cdn.csv" \
   "$RESULTS_DIR/rssi_raw_${RUN_ID}.csv" 2>/dev/null || true

if ls "$RESULTS_DIR"/cdn_measurements_*.csv 1>/dev/null 2>&1; then
    echo ""
    echo "[OK] Run complete: $RUN_ID"
    echo "     Results: $RESULTS_DIR"
    wc -l "$RESULTS_DIR"/cdn_measurements_*.csv
else
    echo "[WARN] No measurements CSV found — check /tmp/topo_${RUN_ID}.log"
fi

echo "============================================================"
echo "  DONE: $RUN_ID"
echo "============================================================"
