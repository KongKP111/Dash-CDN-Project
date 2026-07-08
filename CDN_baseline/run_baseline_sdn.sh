#!/usr/bin/env bash
# =============================================================================
#  run_baseline_sdn.sh — Run ONE CDN Baseline experiment WITH Ryu SDN
# =============================================================================
#  Usage:
#    sudo bash run_baseline_sdn.sh --sit 1 --speed 20 --round 1
# =============================================================================
set -euo pipefail

SIT=1; SPEED=20; ROUND=1

while [[ $# -gt 0 ]]; do
    case "$1" in
        --sit)   SIT="$2";   shift 2 ;;
        --speed) SPEED="$2"; shift 2 ;;
        --round) ROUND="$2"; shift 2 ;;
        *) echo "[ERROR] Unknown: $1"; exit 1 ;;
    esac
done

PROJECT="/home/kongpop/Vault_PSU_Project/wiki/PSU_Project/Dash-CDN-Project"
BASELINE="$PROJECT/CDN_baseline"
RYU_CTRL="$PROJECT/Ryu-SDN-Controller/cdn_switch_13.py"
RYU_PYTHON="/home/kongpop/Vault_PSU_Project/wiki/PSU_Project/mininet-wifi/ryu-venv/bin/python3.8"
RUN_ID="cdn_baseline_sdn_sit${SIT}_spd${SPEED}_r${ROUND}"
OUT_DIR="$PROJECT/results/cdn_baseline/sdn/sit${SIT}/speed${SPEED}/${RUN_ID}"
mkdir -p "$OUT_DIR"

echo "============================================================"
echo "  CDN Baseline SDN RUN: $RUN_ID"
echo "============================================================"

# Preflight: vlc_player.py needs python-vlc — fail fast before burning a
# full Ryu+mininet cycle (this script chains up to 60x via run_baseline_multi_sdn.sh)
python3 -c "import vlc" 2>/dev/null || {
    echo "[ERROR] python-vlc not installed. Run: pip3 install python-vlc"
    echo "        (or: sudo apt install -y python3-vlc)"
    exit 1
}

# Cleanup
mn -c > /dev/null 2>&1 || true
pkill -f ryu 2>/dev/null || true
rm -f /tmp/nginx_cdn_baseline*.pid /tmp/nginx_baseline*.pid /tmp/cdn_baseline_ping.log
rm -rf /tmp/cdn_baseline_cache
sleep 2

# Start Ryu controller
echo "[1/3] Starting Ryu controller..."
(cd "$PROJECT/Ryu-SDN-Controller" && \
 RUN_ID="$RUN_ID" \
 HANDOVER_CSV_PATH="$OUT_DIR/ryu_ho_${RUN_ID}.csv" \
 "$RYU_PYTHON" -m ryu.cmd.manager cdn_switch_13.py \
 --ofp-tcp-listen-port 6653) \
    > /tmp/ryu_${RUN_ID}.log 2>&1 &
RYU_PID=$!

# Wait for Ryu to be ready (port open + extra settle time)
RYU_UP=false
for i in $(seq 1 30); do
    if ss -tlnp 2>/dev/null | grep -q ':6653'; then
        echo "      Ryu is up! (${i}s) — waiting 3s for OpenFlow handler..."
        sleep 3
        RYU_UP=true
        break
    fi
    sleep 1
done
if [[ "$RYU_UP" == false ]]; then
    echo "[ERROR] Ryu did not start within 30s — aborting"
    exit 1
fi

# Run topology
echo "[2/3] Running topology..."
python3 "$BASELINE/cdn_baseline_topo_sdn.py" \
    --sit   "$SIT"   \
    --speed "$SPEED" \
    --round "$ROUND" \
    --out-dir "$OUT_DIR" \
    --auto \
    --no-gui

# Stop Ryu
kill "$RYU_PID" 2>/dev/null || true

# Copy Ryu log
cp /tmp/ryu_${RUN_ID}.log "$OUT_DIR/" 2>/dev/null || true

# Report
echo "[3/3] Results..."
CSV_FILE="$OUT_DIR/${RUN_ID}.csv"
if [[ -f "$CSV_FILE" ]]; then
    HIT_N=$(grep -c ",HIT,"     "$CSV_FILE" 2>/dev/null || true)
    MISS_N=$(grep -c ",MISS,"   "$CSV_FILE" 2>/dev/null || true)
    UNK_N=$(grep -c ",UNKNOWN," "$CSV_FILE" 2>/dev/null || true)
    TOTAL=$(( HIT_N + MISS_N + UNK_N ))
    echo "[OK] $RUN_ID"
    echo "     Cache  →  HIT:${HIT_N}  MISS:${MISS_N}  UNKNOWN:${UNK_N}  (total samples: ${TOTAL})"
    # Warn if unexpected MISS for sit 1
    if [[ "$SIT" == "1" && "$MISS_N" -gt 0 ]]; then
        echo "[WARN] sit 1 should be all HIT — found ${MISS_N} MISS rows"
    fi
    wc -l "$OUT_DIR"/*.csv
else
    echo "[WARN] No CSV found in $OUT_DIR"
fi

echo "============================================================"
echo "  DONE: $RUN_ID"
echo "  Results: $OUT_DIR"
echo "============================================================"