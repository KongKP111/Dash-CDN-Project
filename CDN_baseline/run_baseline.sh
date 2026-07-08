#!/usr/bin/env bash
# =============================================================================
#  run_baseline.sh — Run ONE CDN Baseline experiment (NO SDN)
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
RUN_ID="cdn_baseline_sit${SIT}_spd${SPEED}_r${ROUND}"
OUT_DIR="$PROJECT/results/cdn_baseline/no_sdn/sit${SIT}/speed${SPEED}/${RUN_ID}"
mkdir -p "$OUT_DIR"

echo "============================================================"
echo "  CDN Baseline (No SDN) RUN: $RUN_ID"
echo "============================================================"

mn -c > /dev/null 2>&1 || true
rm -f /tmp/nginx_cdn_baseline*.pid /tmp/cdn_baseline_ping.log
rm -rf /tmp/cdn_baseline_cache
sleep 2

python3 "$BASELINE/cdn_baseline_topo.py" \
    --sit   "$SIT"   \
    --speed "$SPEED" \
    --round "$ROUND" \
    --out-dir "$OUT_DIR" \
    --auto \
    --no-gui

if ls "$OUT_DIR"/*.csv 1>/dev/null 2>&1; then
    echo "[OK] $RUN_ID"
    wc -l "$OUT_DIR"/*.csv
else
    echo "[WARN] No CSV found"
fi

echo "============================================================"
echo "  DONE: $RUN_ID  →  $OUT_DIR"
echo "============================================================"