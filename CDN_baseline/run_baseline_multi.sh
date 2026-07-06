#!/usr/bin/env bash
# =============================================================================
#  run_baseline_multi.sh — Run ALL CDN Baseline experiments (NO SDN)
#  2 sit × 3 speeds × 10 rounds = 60 runs
# =============================================================================
set -euo pipefail

SIT_LIST=(1 2)
SPEED_LIST=(20 25 30)
ROUND_LIST=(1 2 3 4 5 6 7 8 9 10)
RESUME=false
PROJECT="/home/kongpop/PSU_Project/Dash-CDN-Project"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --sit)
            shift; SIT_LIST=()
            while [[ $# -gt 0 && "$1" =~ ^[0-9]+$ ]]; do
                SIT_LIST+=("$1"); shift
            done ;;
        --rounds)
            shift; N="$1"; shift
            ROUND_LIST=(); for i in $(seq 1 "$N"); do ROUND_LIST+=("$i"); done ;;
        --resume) RESUME=true; shift ;;
        *) echo "[ERROR] Unknown: $1"; exit 1 ;;
    esac
done

TOTAL=$(( ${#SIT_LIST[@]} * ${#SPEED_LIST[@]} * ${#ROUND_LIST[@]} ))
CURRENT=0; SKIPPED=0; FAILED=0

echo "============================================================"
echo "  CDN Baseline (No SDN) Experiment Runner"
echo "  Situations : ${SIT_LIST[*]}"
echo "  Speeds     : ${SPEED_LIST[*]} km/h"
echo "  Rounds     : ${#ROUND_LIST[@]} per speed"
echo "  Total      : $TOTAL runs"
echo "  Started at : $(date)"
echo "============================================================"

START_TIME=$(date +%s)

for SIT in "${SIT_LIST[@]}"; do
for SPEED in "${SPEED_LIST[@]}"; do
for ROUND in "${ROUND_LIST[@]}"; do

    CURRENT=$(( CURRENT + 1 ))
    RUN_ID="cdn_baseline_sit${SIT}_spd${SPEED}_r${ROUND}"
    OUT_DIR="$PROJECT/results/cdn_baseline/no_sdn/sit${SIT}/speed${SPEED}/${RUN_ID}"

    echo ""
    echo "--------------------------------------------------------------"
    echo "  [$CURRENT/$TOTAL] $RUN_ID"
    echo "--------------------------------------------------------------"

    if [[ "$RESUME" == true ]] && ls "$OUT_DIR"/*.csv 1>/dev/null 2>&1; then
        echo "  [SKIP] Already done"
        SKIPPED=$(( SKIPPED + 1 ))
        continue
    fi

    if sudo bash "$PROJECT/CDN_baseline/run_baseline.sh" \
           --sit   "$SIT"   \
           --speed "$SPEED" \
           --round "$ROUND"; then
        echo "  [OK] $RUN_ID"
    else
        echo "  [FAIL] $RUN_ID"
        FAILED=$(( FAILED + 1 ))
        echo "$RUN_ID" >> "$PROJECT/results/cdn_baseline_failed.txt"
    fi
    sleep 5

done
done
done

END_TIME=$(date +%s)
ELAPSED=$(( END_TIME - START_TIME ))
HOURS=$(( ELAPSED / 3600 ))
MINS=$(( (ELAPSED % 3600) / 60 ))

echo ""
echo "============================================================"
echo "  ALL DONE"
echo "  Total: $TOTAL | Skipped: $SKIPPED | Failed: $FAILED"
echo "  Succeeded: $(( TOTAL - SKIPPED - FAILED ))"
echo "  Time: ${HOURS}h ${MINS}m"
echo "  Results: $PROJECT/results/cdn_baseline/no_sdn/"
echo "============================================================"