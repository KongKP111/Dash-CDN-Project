#!/usr/bin/env bash
# =============================================================================
#  run_all.sh  —  Loop all experiment runs
# -----------------------------------------------------------------------------
#  Usage:
#    sudo bash run_all.sh [--arch dash|cdn] [--sit 1-6|all] [--resume]
# =============================================================================

set -euo pipefail

ARCH_LIST=("dash")
SIT_LIST=(1 2)
SPEED_LIST=(20 25 30)
ROUND_LIST=(1 2 3 4 5 6 7 8 9 10)
RESUME=false
PROJECT="/home/diz/sdn-cdn-dash-research"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --arch)
            case "$2" in
                both) ARCH_LIST=("dash" "cdn") ;;
                *)    ARCH_LIST=("$2") ;;
            esac
            shift 2 ;;
        --sit)
            if [[ "$2" == "all" ]]; then
                SIT_LIST=(1 2)
            else
                SIT_LIST=("$2")
            fi
            shift 2 ;;
        --resume) RESUME=true; shift ;;
        *) echo "[ERROR] Unknown arg: $1"; exit 1 ;;
    esac
done

TOTAL=$(( ${#ARCH_LIST[@]} * ${#SIT_LIST[@]} * ${#SPEED_LIST[@]} * ${#ROUND_LIST[@]} ))
CURRENT=0
SKIPPED=0
FAILED=0

echo "============================================================"
echo "  run_all.sh — SDN Experiment Runner"
echo "  Architectures : ${ARCH_LIST[*]}"
echo "  Situations    : ${SIT_LIST[*]}"
echo "  Speeds        : ${SPEED_LIST[*]} km/h"
echo "  Rounds        : ${#ROUND_LIST[@]} per combination"
echo "  Total runs    : $TOTAL"
echo "  Resume mode   : $RESUME"
echo "  Started at    : $(date)"
echo "============================================================"

START_TIME=$(date +%s)

for ARCH in "${ARCH_LIST[@]}"; do
for SIT in "${SIT_LIST[@]}"; do
for SPEED in "${SPEED_LIST[@]}"; do
for ROUND in "${ROUND_LIST[@]}"; do

    CURRENT=$(( CURRENT + 1 ))
    RUN_ID="${ARCH}_sit${SIT}_spd${SPEED}_r${ROUND}"
    RESULTS_DIR="$PROJECT/results/${ARCH}/sit${SIT}/speed${SPEED}/${RUN_ID}"

    echo ""
    echo "--------------------------------------------------------------"
    echo "  [$CURRENT/$TOTAL] $RUN_ID"
    echo "--------------------------------------------------------------"

    if [[ "$RESUME" == true ]] && ls "$RESULTS_DIR"/*.json 1>/dev/null 2>&1; then
        echo "  [SKIP] Already done"
        SKIPPED=$(( SKIPPED + 1 ))
        continue
    fi

    if sudo bash "$SCRIPT_DIR/run_experiment.sh" \
        --arch  "$ARCH"  \
        --sit   "$SIT"   \
        --speed "$SPEED" \
        --round "$ROUND"; then
        echo "  [OK] $RUN_ID"
    else
        echo "  [FAIL] $RUN_ID"
        FAILED=$(( FAILED + 1 ))
        echo "$RUN_ID" >> "$PROJECT/results/failed_runs.txt"
    fi

    sleep 5

done
done
done
done

END_TIME=$(date +%s)
TOTAL_TIME=$(( END_TIME - START_TIME ))
HOURS=$(( TOTAL_TIME / 3600 ))
MINS=$(( (TOTAL_TIME % 3600) / 60 ))

echo ""
echo "============================================================"
echo "  ALL DONE"
echo "  Total: $TOTAL | Skipped: $SKIPPED | Failed: $FAILED"
echo "  Succeeded: $(( TOTAL - SKIPPED - FAILED ))"
echo "  Time: ${HOURS}h ${MINS}m"
echo "============================================================"
