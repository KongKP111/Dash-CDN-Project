#!/usr/bin/env bash
# =============================================================================
#  run_hight_speed.sh — Run Situation 2 (Mobility Speed) SDN+CDN case(s) —
#  headless wrapper around cdn_sdn_hight_speed.py, same pattern as
#  CDN_baseline/run_baseline_sdn.sh (auto-starts Ryu, retries on the known
#  intermittent real-WiFi-firmware crash, reports a cache HIT/MISS/LOSS
#  summary) combined with CDN_SIT1/run_multi_car.sh's batch loop (--speeds
#  accepts a comma-separated list, or "all", to run every case back-to-back
#  in one invocation).
# =============================================================================
#  Usage:
#    sudo bash run_hight_speed.sh                       # default: 80,100,120
#    sudo bash run_hight_speed.sh --speeds 100
#    sudo bash run_hight_speed.sh --speeds 80,100,120
#    sudo bash run_hight_speed.sh --speeds all           # 20,80,100,120
#    sudo bash run_hight_speed.sh --speeds 80 --round 2
# =============================================================================
set -euo pipefail

SPEEDS_ARG="80,100,120"; SIT=1; ROUND=1; PORT=6654

while [[ $# -gt 0 ]]; do
    case "$1" in
        --speeds) SPEEDS_ARG="$2"; shift 2 ;;
        --sit)    SIT="$2";        shift 2 ;;
        --round)  ROUND="$2";      shift 2 ;;
        --port)   PORT="$2";       shift 2 ;;
        *) echo "[ERROR] Unknown: $1"; exit 1 ;;
    esac
done

[[ "$SPEEDS_ARG" == "all" ]] && SPEEDS_ARG="20,80,100,120"
IFS=',' read -ra SPEEDS <<< "$SPEEDS_ARG"

PROJECT="/home/pc1/sdn-cdn-dash-research"
SCRIPT_DIR="$PROJECT/CDN_SIT2"
RYU_CTRL="$PROJECT/Ryu-SDN-Controller/cdn_switch_13.py"
RYU_PYTHON="/usr/bin/python3"

# Preflight: vlc_player.py needs python-vlc — fail fast before burning a
# full Ryu+mininet cycle (same check as CDN_baseline/run_baseline_sdn.sh).
python3 -c "import vlc" 2>/dev/null || {
    echo "[ERROR] python-vlc not installed. Run: pip3 install python-vlc"
    echo "        (or: sudo apt install -y python3-vlc)"
    exit 1
}

declare -A RESULT_DIRS
declare -A CASE_STATUS

for SPEED in "${SPEEDS[@]}"; do
    RUN_ID="cdn_sdn_hightspeed_sit${SIT}_spd${SPEED}_r${ROUND}"
    OUT_DIR="$SCRIPT_DIR/results_hightspeed/sit${SIT}/speed${SPEED}/${RUN_ID}"
    mkdir -p "$OUT_DIR"
    RESULT_DIRS["$SPEED"]="$OUT_DIR"

    echo "============================================================"
    echo "  Situation 2 (Mobility Speed) SDN+CDN RUN: $RUN_ID"
    echo "  Speed: ${SPEED} km/h   Ryu port: $PORT"
    echo "============================================================"

    # Real WiFi hardware bug (Realtek rtw89_8852be, "SER catches error" ->
    # mac80211 subsystem restart) is intermittent, not tied to speed or AP
    # zone -- retry the whole run a few times, same rationale as
    # run_baseline_sdn.sh/run_multi_car.sh.
    MAX_ATTEMPTS=3
    SUCCESS=false

    for ATTEMPT in $(seq 1 "$MAX_ATTEMPTS"); do
        echo "------------------------------------------------------------"
        echo "  [$RUN_ID] Attempt $ATTEMPT/$MAX_ATTEMPTS"
        echo "------------------------------------------------------------"

        # Cleanup
        mn -c > /dev/null 2>&1 || true
        pkill -f ryu 2>/dev/null || true
        pkill -f 'vlc_player.py' 2>/dev/null || true
        rm -f /tmp/nginx_cdn_baseline*.pid /tmp/nginx_baseline*.pid /tmp/cdn_baseline_ping.log
        rm -rf /tmp/cdn_baseline_cache*
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
        echo "[2/3] Running topology (${SPEED} km/h)..."
        if python3 "$SCRIPT_DIR/cdn_sdn_hight_speed.py" \
            --sit     "$SIT"   \
            --speed   "$SPEED" \
            --round   "$ROUND" \
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
            pkill -f 'vlc_player.py' 2>/dev/null || true
            sleep 3
        fi
    done

    if [[ "$SUCCESS" != true ]]; then
        echo "[ERROR] $RUN_ID: all $MAX_ATTEMPTS attempts failed — skipping to next case"
        CASE_STATUS["$SPEED"]="FAILED"
        continue
    fi
    CASE_STATUS["$SPEED"]="OK"

    # Copy Ryu log
    cp /tmp/ryu_${RUN_ID}.log "$OUT_DIR/" 2>/dev/null || true

    # Report
    echo "[3/3] Results for $RUN_ID..."
    CSV_FILE="$OUT_DIR/${RUN_ID}.csv"
    if [[ -f "$CSV_FILE" ]]; then
        HIT_N=$(grep -c ",HIT,"     "$CSV_FILE" 2>/dev/null || true)
        MISS_N=$(grep -c ",MISS,"   "$CSV_FILE" 2>/dev/null || true)
        LOSS_N=$(grep -c ",LOSS,"   "$CSV_FILE" 2>/dev/null || true)
        OUTAGE_N=$(awk -F, 'NR==1{for(i=1;i<=NF;i++) if($i=="outage") c=i; next} c && $c==1' "$CSV_FILE" 2>/dev/null | wc -l || true)
        TOTAL=$(( HIT_N + MISS_N + LOSS_N ))
        echo "[OK] $RUN_ID"
        echo "     Cache  →  HIT:${HIT_N}  MISS:${MISS_N}  LOSS:${LOSS_N}  (total samples: ${TOTAL})"
        echo "     Outage rows: ${OUTAGE_N}"
        if [[ "$SIT" == "1" && "$MISS_N" -gt 0 ]]; then
            echo "[WARN] sit 1 should be all HIT — found ${MISS_N} MISS rows"
        fi
        wc -l "$OUT_DIR"/*.csv
    else
        echo "[WARN] No CSV found in $OUT_DIR"
    fi
done

echo "============================================================"
echo "  BATCH DONE"
for SPEED in "${SPEEDS[@]}"; do
    echo "  ${SPEED} km/h  ->  ${CASE_STATUS[$SPEED]:-FAILED}  (${RESULT_DIRS[$SPEED]:-n/a})"
done
echo "============================================================"

# Non-zero exit if any case failed, so a batch failure isn't silently green.
for SPEED in "${SPEEDS[@]}"; do
    [[ "${CASE_STATUS[$SPEED]:-FAILED}" == "FAILED" ]] && exit 1
done
exit 0
