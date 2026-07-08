#!/usr/bin/env bash
# =============================================================================
#  run_cooperative_sdn.sh — Run CDN Cooperative SDN experiment
# =============================================================================
#  Compares two modes using 4 per-AP edge caches:
#    nocoop: cold cache at each AP zone → MISS on first request after handover
#    coop:   Ryu-triggered pre-warm    → HIT immediately after handover
#
#  Usage:
#    sudo bash run_cooperative_sdn.sh --sit 1 --speed 20 --round 1 --mode coop
#    sudo bash run_cooperative_sdn.sh --sit 1 --speed 20 --round 1 --mode nocoop
#    sudo bash run_cooperative_sdn.sh --sit 1 --speed 20 --round 1 --mode both
# =============================================================================
set -euo pipefail

SIT=1; SPEED=20; ROUND=1; MODE=both

while [[ $# -gt 0 ]]; do
    case "$1" in
        --sit)   SIT="$2";   shift 2 ;;
        --speed) SPEED="$2"; shift 2 ;;
        --round) ROUND="$2"; shift 2 ;;
        --mode)  MODE="$2";  shift 2 ;;
        *) echo "[ERROR] Unknown: $1"; exit 1 ;;
    esac
done

PROJECT="/home/kongpop/Vault_PSU_Project/wiki/PSU_Project/Dash-CDN-Project"
BASELINE="$PROJECT/CDN_baseline"
RYU_CTRL="$PROJECT/Ryu-SDN-Controller/cdn_switch_13.py"
RYU_PYTHON="/home/kongpop/Vault_PSU_Project/wiki/PSU_Project/mininet-wifi/ryu-venv/bin/python3.8"

start_ryu() {
    local run_id="$1"
    local out_dir="$2"
    echo "[RYU] Starting Ryu controller for $run_id ..."
    pkill -f ryu 2>/dev/null || true
    sleep 1
    (cd "$PROJECT/Ryu-SDN-Controller" && \
     RUN_ID="$run_id" \
     HANDOVER_CSV_PATH="$out_dir/ryu_ho_${run_id}.csv" \
     "$RYU_PYTHON" -m ryu.cmd.manager cdn_switch_13.py \
     --ofp-tcp-listen-port 6653) \
        > /tmp/ryu_${run_id}.log 2>&1 &
    echo $!
}

wait_ryu() {
    for i in $(seq 1 30); do
        if ss -tlnp 2>/dev/null | grep -q ':6653'; then
            echo "[RYU] Ready after ${i}s — settling 3s..."
            sleep 3
            return 0
        fi
        sleep 1
    done
    echo "[ERROR] Ryu did not start within 30s"
    return 1
}

run_experiment() {
    local mode_flag="$1"    # "coop" or "nocoop"
    local coop_arg=""
    [[ "$mode_flag" == "coop" ]] && coop_arg="--cooperative"

    local run_id="cdn_${mode_flag}_sit${SIT}_spd${SPEED}_r${ROUND}"
    local out_dir="$PROJECT/results/cdn_cooperative/${mode_flag}/sit${SIT}/speed${SPEED}/${run_id}"
    mkdir -p "$out_dir"

    echo ""
    echo "============================================================"
    echo "  MODE: ${mode_flag^^}   RUN: $run_id"
    echo "============================================================"

    # Cleanup
    mn -c > /dev/null 2>&1 || true
    rm -f /tmp/nginx_coop*.pid /tmp/cdn_coop_ping.log /tmp/ryu_coop_signal
    rm -rf /tmp/cdn_coop_cache_*
    sleep 2

    local ryu_pid
    ryu_pid=$(start_ryu "$run_id" "$out_dir")
    wait_ryu || { kill "$ryu_pid" 2>/dev/null; return 1; }

    echo "[TOPO] Running topology ($mode_flag mode)..."
    python3 "$BASELINE/cdn_cooperative_topo_sdn.py" \
        --sit   "$SIT"   \
        --speed "$SPEED" \
        --round "$ROUND" \
        --out-dir "$out_dir" \
        --auto \
        --no-gui \
        $coop_arg

    kill "$ryu_pid" 2>/dev/null || true
    cp /tmp/ryu_${run_id}.log "$out_dir/" 2>/dev/null || true

    # Report
    local csv="$out_dir/${run_id}.csv"
    if [[ -f "$csv" ]]; then
        local hit miss unk
        hit=$(grep -c ",HIT,"     "$csv" 2>/dev/null || true)
        miss=$(grep -c ",MISS,"   "$csv" 2>/dev/null || true)
        unk=$(grep -c ",UNKNOWN," "$csv" 2>/dev/null || true)
        echo ""
        echo "[RESULT] $run_id"
        echo "   Cache → HIT:${hit}  MISS:${miss}  UNKNOWN:${unk}  total:$(( hit + miss + unk ))"
        if [[ "$mode_flag" == "coop" && "$SIT" == "1" && "$miss" -gt 0 ]]; then
            echo "[WARN] cooperative sit1 should be all HIT — found ${miss} MISS"
        fi
        if [[ "$mode_flag" == "nocoop" && "$SIT" == "1" && "$miss" -eq 0 ]]; then
            echo "[WARN] non-cooperative sit1 expected some MISS (cold cache) — found none"
        fi
    else
        echo "[WARN] CSV not found: $csv"
    fi
    echo "   Files: $out_dir"
    echo "============================================================"
}

echo "============================================================"
echo "  CDN Cooperative SDN experiment"
echo "  sit=${SIT}  speed=${SPEED}  round=${ROUND}  mode=${MODE}"
echo "============================================================"

case "$MODE" in
    coop)   run_experiment coop ;;
    nocoop) run_experiment nocoop ;;
    both)
        run_experiment nocoop
        run_experiment coop
        echo ""
        echo "=== COMPARISON SUMMARY ==="
        for m in nocoop coop; do
            csv="$PROJECT/results/cdn_cooperative/${m}/sit${SIT}/speed${SPEED}/cdn_${m}_sit${SIT}_spd${SPEED}_r${ROUND}/cdn_${m}_sit${SIT}_spd${SPEED}_r${ROUND}.csv"
            if [[ -f "$csv" ]]; then
                h=$(grep -c ",HIT,"     "$csv" 2>/dev/null || true)
                mi=$(grep -c ",MISS,"   "$csv" 2>/dev/null || true)
                u=$(grep -c ",UNKNOWN," "$csv" 2>/dev/null || true)
                avg_lat=$(awk -F',' 'NR>1 {sum+=$7; n++} END {if(n>0) printf "%.3f", sum/n}' "$csv")
                printf "  %-8s  HIT:%-3d  MISS:%-3d  UNKNOWN:%-3d  avg_lat=%ss\n" \
                    "$m" "$h" "$mi" "$u" "$avg_lat"
            fi
        done
        echo "=========================="
        ;;
    *) echo "[ERROR] --mode must be coop|nocoop|both"; exit 1 ;;
esac

echo "DONE"
