#!/usr/bin/env bash
# =============================================================================
#  CDN_run_all.sh  —  Run ALL CDN experiment situations and rounds
# -----------------------------------------------------------------------------
#  Test case design: SDN_Test_Case_Scenarios.pdf (SDN-DASH vs SDN-CDN)
#    Each situation is swept across 3 speeds, 10 rounds each:
#      Speed 20 km/h x 10 rounds, Speed 25 km/h x 10 rounds, Speed 30 km/h x 10 rounds
#    Currently enabled : Situations 1-2 only (more situations added later)
#
#    1 — Normal / Baseline          3 Mbps stable        (no stress)
#    2 — Light Handover (Urban)     2 Mbps constant      (mild drop)
#    3 — Heavy Handover (Suburban)  250 kbps at handover (heavy HO)
#    4 — Sudden Bandwidth Drop      100 kbps instant drop (dead zone)
#    5 — High Mobility (Highway)    3 Mbps stable        (high speed)
#    6 — Combined Stress Worst Case 100 kbps drop        (speed+drop)
# -----------------------------------------------------------------------------
#  Usage:
#    sudo bash CDN_run_all.sh                     # sit 1-2 x 3 speeds x 10 rounds = 60 runs
#    sudo bash CDN_run_all.sh --sit 1             # only situation 1
#    sudo bash CDN_run_all.sh --sit 1 2 3         # situations 1-3
#    sudo bash CDN_run_all.sh --resume            # skip already-completed runs
#    sudo bash CDN_run_all.sh --sit 5 6 --resume
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
            shift
            SIT_LIST=()
            while [[ $# -gt 0 && "$1" =~ ^[0-9]+$ ]]; do
                SIT_LIST+=("$1")
                shift
            done
            ;;
        --resume) RESUME=true; shift ;;
        *) echo "[ERROR] Unknown arg: $1"; exit 1 ;;
    esac
done

TOTAL=$(( ${#SIT_LIST[@]} * ${#SPEED_LIST[@]} * ${#ROUND_LIST[@]} ))
CURRENT=0
SKIPPED=0
FAILED=0

echo "============================================================"
echo "  CDN_run_all.sh — SDN-CDN Experiment Runner"
echo "  Situations : ${SIT_LIST[*]}"
echo "  Speeds     : ${SPEED_LIST[*]} km/h"
echo "  Rounds     : ${#ROUND_LIST[@]} per speed"
echo "  Total runs : $TOTAL"
echo "  Resume     : $RESUME"
echo "  Started at : $(date)"
echo "============================================================"

START_TIME=$(date +%s)

for SIT in "${SIT_LIST[@]}"; do
for SPEED in "${SPEED_LIST[@]}"; do
for ROUND in "${ROUND_LIST[@]}"; do

    CURRENT=$(( CURRENT + 1 ))
    RUN_ID="cdn_sit${SIT}_spd${SPEED}_r${ROUND}"
    RESULTS_DIR="$PROJECT/results/cdn/sit${SIT}/speed${SPEED}/${RUN_ID}"

    echo ""
    echo "--------------------------------------------------------------"
    echo "  [$CURRENT/$TOTAL] $RUN_ID"
    echo "--------------------------------------------------------------"

    # Resume: skip if measurements CSV already exists
    if [[ "$RESUME" == true ]] && \
       ls "$RESULTS_DIR"/cdn_measurements_*.csv 1>/dev/null 2>&1; then
        echo "  [SKIP] Already done — $RESULTS_DIR"
        SKIPPED=$(( SKIPPED + 1 ))
        continue
    fi

    if sudo bash "$PROJECT/CDN/CDN_run_experiment.sh" \
           --sit   "$SIT"   \
           --speed "$SPEED" \
           --round "$ROUND"; then
        echo "  [OK] $RUN_ID"
    else
        echo "  [FAIL] $RUN_ID"
        FAILED=$(( FAILED + 1 ))
        mkdir -p "$PROJECT/results"
        echo "$RUN_ID" >> "$PROJECT/results/cdn_failed_runs.txt"
    fi

    sleep 5

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
echo "  Results: $PROJECT/results/cdn/"
echo "============================================================"
