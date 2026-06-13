#!/usr/bin/env bash
# =============================================================================
#  run_experiment.sh  —  Run ONE experiment and save logs
# -----------------------------------------------------------------------------
#  Usage:
#    sudo bash run_experiment.sh --arch dash --sit 1 --speed 20 --round 1
# =============================================================================

set -euo pipefail

ARCH="dash"
SIT=1
SPEED=20
ROUND=1

while [[ $# -gt 0 ]]; do
    case "$1" in
        --arch)  ARCH="$2";  shift 2 ;;
        --sit)   SIT="$2";   shift 2 ;;
        --speed) SPEED="$2"; shift 2 ;;
        --round) ROUND="$2"; shift 2 ;;
        *) echo "[ERROR] Unknown arg: $1"; exit 1 ;;
    esac
done

PROJECT="/home/diz/sdn-cdn-dash-research"
TOPO="$PROJECT/Dash/topology/dash_topology.py"
RYU_CTRL="$PROJECT/Ryu-SDN-Controller/sdn_controller.py"
RUN_ID="${ARCH}_sit${SIT}_spd${SPEED}_r${ROUND}"
LOG_DIR="/tmp/dash_logs/${RUN_ID}"
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
    docker stop ryu_ctrl > /dev/null 2>&1 || true
    docker rm   ryu_ctrl > /dev/null 2>&1 || true
    echo "[CLEANUP] Done."
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
#  Step 1: Pre-run cleanup
# ---------------------------------------------------------------------------
echo "[1/4] Pre-run cleanup..."
sudo mn -c > /dev/null 2>&1 || true
docker stop ryu_ctrl > /dev/null 2>&1 || true
docker rm   ryu_ctrl > /dev/null 2>&1 || true
sleep 2

# ---------------------------------------------------------------------------
#  Step 2: Start Ryu controller
# ---------------------------------------------------------------------------
echo "[2/4] Starting Ryu controller..."
docker run -d --rm \
    --name ryu_ctrl \
    --network host \
    -v "$PROJECT/Ryu-SDN-Controller":/app \
    osrg/ryu \
    ryu-manager /app/sdn_controller.py \
    > /dev/null 2>&1

for i in $(seq 1 30); do
    if sudo ss -tlnp 2>/dev/null | grep -q ':6653'; then
        echo "      Ryu is up! (${i}s)"
        break
    fi
    if [[ $i -eq 30 ]]; then
        echo "[ERROR] Ryu failed to start"
        exit 1
    fi
    sleep 1
done

# ---------------------------------------------------------------------------
#  Step 3: Run topology + client (all-in-one)
# ---------------------------------------------------------------------------
echo "[3/4] Running topology + client (this takes a few minutes)..."
mkdir -p "$LOG_DIR"

sudo python3 "$TOPO" \
    --sit  "$SIT" \
    --speed "$SPEED" \
    --round "$ROUND" \
    --run-client \
    --run-id "$RUN_ID" \
    --out-dir "$LOG_DIR" \
    2>&1 | tee /tmp/topo_${RUN_ID}.log

# ---------------------------------------------------------------------------
#  Step 4: Save results
# ---------------------------------------------------------------------------
echo "[4/4] Saving results..."
mkdir -p "$RESULTS_DIR"
RAW_DIR="$PROJECT/results_raw/${RUN_ID}"
cp "$RAW_DIR"/*.csv  "$RESULTS_DIR/" 2>/dev/null || true
cp "$RAW_DIR"/*.json "$RESULTS_DIR/" 2>/dev/null || true
cp "$RAW_DIR"/*.log  "$RESULTS_DIR/" 2>/dev/null || true
cp "$LOG_DIR"/*.csv  "$RESULTS_DIR/" 2>/dev/null || true
cp "$LOG_DIR"/*.json "$RESULTS_DIR/" 2>/dev/null || true
cp "/tmp/client_${RUN_ID}.log" "$RESULTS_DIR/" 2>/dev/null || true
cp "/tmp/topo_${RUN_ID}.log"   "$RESULTS_DIR/" 2>/dev/null || true

if ls "$RESULTS_DIR"/*.json 1>/dev/null 2>&1; then
    echo ""
    echo "[OK] Run complete: $RUN_ID"
    cat "$RESULTS_DIR/${RUN_ID}_summary.json" 2>/dev/null || true
else
    echo "[WARN] No summary JSON found — run may have failed"
fi

echo "============================================================"
echo "  DONE: $RUN_ID"
echo "============================================================"
