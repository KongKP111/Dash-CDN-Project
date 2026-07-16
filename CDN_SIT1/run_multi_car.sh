#!/usr/bin/env bash
# =============================================================================
#  run_multi_car.sh — Run Situation 1 (Traffic Density) SDN+CDN platoon
#  scenario(s) — headless wrapper around cdn_sdn_multi_car.py, same pattern
#  as CDN_baseline/run_baseline_sdn.sh (auto-starts Ryu, retries on the
#  known intermittent real-WiFi-firmware crash, reports a cache HIT/MISS
#  summary). --cars accepts a comma-separated list (or "all" for 3,5,7) to
#  run every case back-to-back in one invocation.
# =============================================================================
#  Usage:
#    sudo bash run_multi_car.sh --cars 3 --run-id test_3cars
#    sudo bash run_multi_car.sh --cars 5
#    sudo bash run_multi_car.sh --cars 3,5,7            # batch, one command
#    sudo bash run_multi_car.sh --cars all --port 6655   # same as 3,5,7
# =============================================================================
set -euo pipefail

CARS_ARG="3"; RUN_ID_BASE=""; PORT=6654

while [[ $# -gt 0 ]]; do
    case "$1" in
        --cars)   CARS_ARG="$2"; shift 2 ;;
        --run-id) RUN_ID_BASE="$2"; shift 2 ;;
        --port)   PORT="$2";   shift 2 ;;
        *) echo "[ERROR] Unknown: $1"; exit 1 ;;
    esac
done

[[ "$CARS_ARG" == "all" ]] && CARS_ARG="3,5,7"
IFS=',' read -ra CAR_COUNTS <<< "$CARS_ARG"

PROJECT="/home/pc1/sdn-cdn-dash-research"
SCRIPT_DIR="$PROJECT/CDN_SIT1"
RYU_CTRL="$PROJECT/Ryu-SDN-Controller/cdn_switch_13.py"
RYU_PYTHON="/usr/bin/python3"

# Preflight: vlc_player.py needs python-vlc — fail fast before burning a
# full Ryu+mininet cycle (same check as CDN_baseline/run_baseline_sdn.sh).
python3 -c "import vlc" 2>/dev/null || {
    echo "[ERROR] python-vlc not installed. Run: pip3 install python-vlc"
    echo "        (or: sudo apt install -y python3-vlc)"
    exit 1
}

# Real WiFi hardware bug (Realtek rtw89_8852be, "SER catches error" ->
# mac80211 subsystem restart) is INTERMITTENT with <5 simulated stations
# but essentially GUARANTEED with 5+ (see Situation1_DASH/README.md's
# "Hardware gotcha"). Decide ONCE for the whole batch — if any requested
# count is >=5, block real WiFi for the entire batch (not per-count) so a
# "3,5,7" run doesn't toggle the real card on/off between cases; always
# restored on exit (even on Ctrl-C/crash) via the trap below.
BLOCK_WIFI=false
for n in "${CAR_COUNTS[@]}"; do
    [[ "$n" -ge 5 ]] && BLOCK_WIFI=true
done
if [[ "$BLOCK_WIFI" == true ]]; then
    echo "=== batch includes 5+ cars: blocking real WiFi for the whole batch ==="
    rfkill block wifi
    trap 'echo "=== unblocking real WiFi ==="; rfkill unblock wifi' EXIT
fi

declare -A RESULT_DIRS
declare -A CASE_STATUS

for CARS in "${CAR_COUNTS[@]}"; do
    if [[ -n "$RUN_ID_BASE" ]]; then
        RUN_ID="${RUN_ID_BASE}_${CARS}cars"
    else
        RUN_ID="cdn_sdn_${CARS}cars_$(date +%Y%m%d_%H%M%S)"
    fi

    OUT_DIR="/tmp/cdn_multi_car_logs_${RUN_ID}"
    RESULT_DIR="$SCRIPT_DIR/result_multi_car/${RUN_ID}"
    mkdir -p "$OUT_DIR" "$RESULT_DIR"
    RESULT_DIRS["$CARS"]="$RESULT_DIR"

    echo "============================================================"
    echo "  Situation 1 (Traffic Density) SDN+CDN RUN: $RUN_ID"
    echo "  Cars: $CARS   Ryu port: $PORT"
    echo "============================================================"

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
        rm -f /tmp/nginx_cdn_baseline*.pid /tmp/nginx_baseline*.pid
        rm -rf /tmp/cdn_baseline_cache*
        sleep 2

        # Start Ryu controller
        #
        # RUN_ID/HANDOVER_CSV_PATH are required here, not optional: cdn_switch_13.py
        # falls back to a FIXED path (Ryu-SDN-Controller/handover_times.csv,
        # opened in 'w'/truncate mode) when HANDOVER_CSV_PATH isn't set -- every
        # run (single-vehicle or multi-car alike) would silently overwrite the
        # same file instead of landing in this run's own result_multi_car/
        # <run_id>/ folder. Writing it straight into RESULT_DIR means no
        # separate copy step is needed afterward either.
        echo "[1/3] Starting Ryu controller (port $PORT)..."
        (cd "$PROJECT/Ryu-SDN-Controller" && \
         RUN_ID="$RUN_ID" \
         HANDOVER_CSV_PATH="$RESULT_DIR/ryu_handover_${RUN_ID}.csv" \
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
        echo "[2/3] Running topology ($CARS cars)..."
        if python3 "$SCRIPT_DIR/cdn_sdn_multi_car.py" \
            --cars "$CARS" \
            --run-id "$RUN_ID" \
            --out-dir "$OUT_DIR" \
            --ryu-port "$PORT" \
            --run-client; then
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
        CASE_STATUS["$CARS"]="FAILED"
        continue
    fi
    CASE_STATUS["$CARS"]="OK"

    # Copy Ryu log alongside the per-vehicle CSVs
    cp /tmp/ryu_${RUN_ID}.log "$RESULT_DIR/" 2>/dev/null || true

    # Report — cdn_sdn_multi_car.py's own build() already copies the
    # per-vehicle CSVs into CDN_SIT1/result_multi_car/<run_id>/ itself;
    # this just summarises what landed there.
    echo "[3/3] Results for $RUN_ID..."
    if compgen -G "$RESULT_DIR"/*_network.csv > /dev/null; then
        for csv in "$RESULT_DIR"/*_network.csv; do
            car_name=$(basename "$csv" | sed -E "s/${RUN_ID}_(car[0-9]+)_network.csv/\1/")
            HIT_N=$(grep -c ",HIT,"     "$csv" 2>/dev/null || true)
            MISS_N=$(grep -c ",MISS,"   "$csv" 2>/dev/null || true)
            # 'LOSS' replaced the old 'UNKNOWN' cache tier (see
            # cdn_sdn_multi_car.py's outage tracking) -- a request that got
            # no answer at all (outage or timed-out probe) is a connection
            # LOSS, not an ambiguous cache state.
            LOSS_N=$(grep -c ",LOSS,"   "$csv" 2>/dev/null || true)
            TOTAL=$(( HIT_N + MISS_N + LOSS_N ))
            echo "     $car_name  →  HIT:${HIT_N}  MISS:${MISS_N}  LOSS:${LOSS_N}  (samples: ${TOTAL})"
        done
        echo "[OK] $RUN_ID"
    else
        echo "[WARN] No per-vehicle CSVs found in $RESULT_DIR"
    fi
done

echo "============================================================"
echo "  BATCH DONE"
for CARS in "${CAR_COUNTS[@]}"; do
    echo "  ${CARS} cars  ->  ${CASE_STATUS[$CARS]:-FAILED}  (${RESULT_DIRS[$CARS]:-n/a})"
done
echo "============================================================"

# Non-zero exit if any case failed, so a batch failure isn't silently green.
for CARS in "${CAR_COUNTS[@]}"; do
    [[ "${CASE_STATUS[$CARS]:-FAILED}" == "FAILED" ]] && exit 1
done
exit 0
