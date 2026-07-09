#!/usr/bin/env bash
# =============================================================================
#  run_baseline_sdn.sh — Run ONE CDN Baseline experiment WITH Ryu SDN
# =============================================================================
#  Usage:
#    sudo bash run_baseline_sdn.sh --sit 1 --speed 20 --round 1
# =============================================================================
set -euo pipefail

SIT=1; SPEED=20; ROUND=1; PORT=6653

while [[ $# -gt 0 ]]; do
    case "$1" in
        --sit)   SIT="$2";   shift 2 ;;
        --speed) SPEED="$2"; shift 2 ;;
        --round) ROUND="$2"; shift 2 ;;
        --port)  PORT="$2";  shift 2 ;;
        *) echo "[ERROR] Unknown: $1"; exit 1 ;;
    esac
done

PROJECT="/home/pc1/sdn-cdn-dash-research"
BASELINE="$PROJECT/CDN_baseline"
RYU_CTRL="$PROJECT/Ryu-SDN-Controller/cdn_switch_13.py"
RYU_PYTHON="/usr/bin/python3"
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

# This machine's real WiFi hardware (Realtek rtw89_8852be) has a firmware
# bug that can fire at any time and force the kernel to restart the whole
# mac80211 subsystem -- that drags down mac80211_hwsim (the *simulated*
# radios mininet-wifi/wmediumd depend on) along with the real card, killing
# the run mid-flight with a BrokenPipeError. It's an intermittent hardware
# timing issue (roughly once per 12-15 min of runtime in practice), not
# something in this project's code, and not tied to any particular AP zone
# or bandwidth tier -- so rather than touching real WiFi config (which
# would cost you connectivity for the run's duration), just retry the
# whole run a few times. A retry is very likely to land clean since the
# bug is intermittent, not deterministic.
MAX_ATTEMPTS=3
SUCCESS=false

for ATTEMPT in $(seq 1 "$MAX_ATTEMPTS"); do
    echo "------------------------------------------------------------"
    echo "  Attempt $ATTEMPT/$MAX_ATTEMPTS"
    echo "------------------------------------------------------------"

    # Cleanup
    mn -c > /dev/null 2>&1 || true
    pkill -f ryu 2>/dev/null || true
    rm -f /tmp/nginx_cdn_baseline*.pid /tmp/nginx_baseline*.pid /tmp/cdn_baseline_ping.log
    rm -rf /tmp/cdn_baseline_cache
    sleep 2

    # Start Ryu controller
    echo "[1/3] Starting Ryu controller (port $PORT)..."
    (cd "$PROJECT/Ryu-SDN-Controller" && \
     RUN_ID="$RUN_ID" \
     HANDOVER_CSV_PATH="$OUT_DIR/ryu_ho_${RUN_ID}.csv" \
     "$RYU_PYTHON" -m ryu.cmd.manager cdn_switch_13.py \
     --ofp-tcp-listen-port "$PORT") \
        > /tmp/ryu_${RUN_ID}.log 2>&1 &
    RYU_PID=$!

    # Wait for Ryu to be ready (port open + extra settle time)
    RYU_UP=false
    for i in $(seq 1 30); do
        if ss -tlnp 2>/dev/null | grep -q ":${PORT}"; then
            echo "      Ryu is up! (${i}s) — waiting 3s for OpenFlow handler..."
            sleep 3
            RYU_UP=true
            break
        fi
        sleep 1
    done
    if [[ "$RYU_UP" == false ]]; then
        echo "[ERROR] Ryu did not start within 30s — retrying"
        kill "$RYU_PID" 2>/dev/null || true
        continue
    fi

    # Run topology
    echo "[2/3] Running topology..."
    if python3 "$BASELINE/cdn_baseline_topo_sdn.py" \
        --sit   "$SIT"   \
        --speed "$SPEED" \
        --round "$ROUND" \
        --out-dir "$OUT_DIR" \
        --ryu-port "$PORT" \
        --auto \
        --no-gui; then
        SUCCESS=true
        kill "$RYU_PID" 2>/dev/null || true
        break
    else
        echo "[WARN] Topology run crashed on attempt $ATTEMPT/$MAX_ATTEMPTS"
        echo "       (likely the real-WiFi firmware bug, not this project's code)"
        kill "$RYU_PID" 2>/dev/null || true
        sleep 3
    fi
done

if [[ "$SUCCESS" != true ]]; then
    echo "[ERROR] All $MAX_ATTEMPTS attempts failed — giving up"
    exit 1
fi

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