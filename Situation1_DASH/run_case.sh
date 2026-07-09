#!/usr/bin/env bash
# Run the Situation 1 (Traffic Density) SDN+DASH platoon scenario N times
# for a given car count -- headless wrapper around platoon_topology.py,
# same pattern as dash-baseline/run_4rsu_multi.sh.
#
#   ./run_case.sh <n_runs> <car_count> [out_dir]
#   ./run_case.sh 1 3                  # single smoke test, 3-car case (do this first)
#   ./run_case.sh 10 3 results_3cars   # 10-run batch, only after the smoke test looks right
#
# Reuses the SAME long-running ryu-ctrl Docker container as the frozen
# dash-baseline arm (Ryu-SDN-Controller/sdn_controller.py is a generic
# MAC-learning + handover switch, unmodified -- it already works for any
# number of stations, so no second controller/port is needed here).
#
# Blocks the real onboard WiFi (rfkill) for the duration of the batch: on
# pc1, running 7+ simultaneous simulated stations has been observed to
# crash the real Realtek rtw89_8852be card's firmware (dmesg: "SER catches
# error", forcing a kernel mac80211 subsystem restart) -- since Mininet-WiFi's
# virtual radios (mac80211_hwsim) share that same mac80211 subsystem with the
# real card, the crash breaks every simulated vehicle's association for the
# whole run (100% failure, not intermittent). Unblocked again at the end.
set -u
N=${1:-1}
CARS=${2:-3}
OUTDIR=${3:-results_${CARS}cars}
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CTRL_FILE="$SCRIPT_DIR/../Ryu-SDN-Controller/sdn_controller.py"
cd "$SCRIPT_DIR"
mkdir -p "$OUTDIR"

if ! docker ps --format '{{.Names}}' | grep -qx ryu-ctrl; then
    echo "=== starting ryu-ctrl container ==="
    docker rm -f ryu-ctrl >/dev/null 2>&1
    docker run -d --restart=always --name ryu-ctrl --network host \
        -v /tmp:/tmp -v "$CTRL_FILE":/sdn_controller.py \
        osrg/ryu ryu-manager /sdn_controller.py --ofp-tcp-listen-port 6653
    sleep 3
else
    echo "=== reusing already-running ryu-ctrl container ==="
fi

echo "=== blocking real WiFi for the duration of this batch (see header note) ==="
sudo rfkill block wifi

for i in $(seq 1 "$N"); do
    run_id=$(printf "case_%scars_run%02d" "$CARS" "$i")
    echo "================  $run_id  /  $N  ================"
    sudo mn -c >/dev/null 2>&1
    sudo python3 platoon_topology.py --cars "$CARS" --run-client \
        --run-id "$run_id" --out-dir "/tmp/platoon_logs_${run_id}"
    pkill -9 -u "$USER" -f 'dash_client.py' 2>/dev/null
    sleep 3
done
sudo mn -c >/dev/null 2>&1

echo "=== unblocking real WiFi ==="
sudo rfkill unblock wifi

echo
echo "Done. Per-vehicle results saved under results_raw/<run_id>/:"
echo "  <run_id>_carN_segments.csv   -- per-segment quality/bitrate/stall (from dash_client.py)"
echo "  <run_id>_carN_summary.json   -- per-vehicle session summary (QoE inputs)"
echo "  <run_id>_carN_network.csv    -- per-vehicle x,y,RSU,RSSI,allocated_bw,ICMP loss over time"
