#!/usr/bin/env python3
"""
============================================================================
  analyze_results.py  —  Per-run correlation/temporal plots for SDN-CDN
----------------------------------------------------------------------------
  Same visual style as the DASH-side analysis (correlation.png + temporal.png):
    correlation.png : RSSI (dBm) vs edge latency (s), trend line, Pearson r
    temporal.png    : (a) latency + throughput vs time, with handover lines
                       (b) RSSI vs time, with handover lines

  Reads the CSVs produced by CDN_run_all.sh / CDN_run_experiment.sh:
    results/cdn/sit<N>/speed<S>/cdn_sit<N>_spd<S>_r<round>/
        cdn_measurements_<run_id>.csv   (time,x,y,ap,edge,edge_ip,video,
                                          cache_status,time_total_s,speed_bps)
        rssi_<run_id>.csv                (time,x,y,target_ap,edge_server,
                                          edge_ip,ap_mac,signal_dBm,speed_kmh)

  Output (this user owns results/cdn/, written under CDN/ instead since
  results/ is root-owned from sudo runs):
    CDN/analysis/sit<N>/speed<S>/correlation.png
    CDN/analysis/sit<N>/speed<S>/temporal.png

  Usage:
    python3 CDN/analyze_results.py                  # sit 1,2 x speed 20,25,30 (defaults)
    python3 CDN/analyze_results.py --sit 1 --speed 30 --round 3
============================================================================
"""
import argparse
import csv
import os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

PROJECT      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_ROOT = os.path.join(PROJECT, 'results', 'cdn')
OUT_ROOT     = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'analysis')


def load_csv(path):
    with open(path, newline='') as fh:
        return list(csv.DictReader(fh))


def load_run(sit, speed, round_):
    run_id  = f'cdn_sit{sit}_spd{speed}_r{round_}'
    run_dir = os.path.join(RESULTS_ROOT, f'sit{sit}', f'speed{speed}', run_id)

    meas_path = os.path.join(run_dir, f'cdn_measurements_{run_id}.csv')
    rssi_path = os.path.join(run_dir, f'rssi_{run_id}.csv')
    if not os.path.isfile(rssi_path):
        rssi_path = os.path.join(run_dir, f'rssi_raw_{run_id}.csv')

    if not os.path.isfile(meas_path) or not os.path.isfile(rssi_path):
        return None

    meas_rows = load_csv(meas_path)
    rssi_rows = load_csv(rssi_path)
    rssi_by_time = {row['time']: row for row in rssi_rows}

    merged = []
    for m in meas_rows:
        r = rssi_by_time.get(m['time'])
        if r is None:
            continue
        merged.append({
            'time':       float(m['time']),
            'ap':         m['ap'],
            'latency_s':  float(m['time_total_s']),
            'speed_kbps': float(m['speed_bps']) / 1000.0,
            'rssi_dbm':   float(r['signal_dBm']),
        })
    merged.sort(key=lambda row: row['time'])
    return run_id, merged


def handover_times(rows):
    times, prev_ap = [], None
    for row in rows:
        if prev_ap is not None and row['ap'] != prev_ap:
            times.append(row['time'])
        prev_ap = row['ap']
    return times


def plot_correlation(rows, sit, speed, out_path):
    rssi    = np.array([r['rssi_dbm'] for r in rows])
    latency = np.array([r['latency_s'] for r in rows])
    r_value = np.corrcoef(rssi, latency)[0, 1] if len(rows) > 1 else float('nan')

    fig, ax = plt.subplots(figsize=(8, 6))
    ax.scatter(rssi, latency, color='#1f3a5f', alpha=0.6)
    if len(rows) > 1:
        slope, intercept = np.polyfit(rssi, latency, 1)
        xs = np.linspace(rssi.min(), rssi.max(), 50)
        ax.plot(xs, slope * xs + intercept, '--', color='black', linewidth=1.5)
    ax.set_title(f'RSSI vs latency  [Sit {sit}, {speed} km/h]  r={r_value:.2f}')
    ax.set_xlabel('RSSI (dBm)')
    ax.set_ylabel('Edge response latency (s)')
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=100)
    plt.close(fig)


def plot_temporal(rows, sit, speed, out_path):
    t       = [r['time'] for r in rows]
    latency = [r['latency_s'] for r in rows]
    thr     = [r['speed_kbps'] for r in rows]
    rssi    = [r['rssi_dbm'] for r in rows]
    ho      = handover_times(rows)

    fig, (ax_a, ax_b) = plt.subplots(2, 1, figsize=(8, 7), sharex=True)

    ax_a.plot(t, latency, '-', color='#1f3a8c', linewidth=2, label='Latency')
    ax_a.set_ylabel('Latency (s)', color='#1f3a8c')
    ax_a.set_title(f'(a) Client reaction to handover  [Sit {sit}, {speed} km/h]')
    ax_thr = ax_a.twinx()
    ax_thr.plot(t, thr, '--', color='#2e8b57', linewidth=2, label='Throughput')
    ax_thr.set_ylabel('Throughput (kbps)', color='#2e8b57')
    for x in ho:
        ax_a.axvline(x, color='orange', linestyle=':', linewidth=1.5)
    h1, l1 = ax_a.get_legend_handles_labels()
    h2, l2 = ax_thr.get_legend_handles_labels()
    ax_a.legend(h1 + h2, l1 + l2, loc='upper right', fontsize=9)

    ax_b.plot(t, rssi, '-', color='#7b3fa0', linewidth=1.5, label='RSSI')
    for x in ho:
        ax_b.axvline(x, color='orange', linestyle=':', linewidth=1.5,
                     label='Handover' if x == ho[0] else None)
    ax_b.set_title('(b) RSSI along route')
    ax_b.set_xlabel('Time (s)')
    ax_b.set_ylabel('RSSI (dBm)')
    ax_b.legend(loc='upper right', fontsize=9)

    fig.tight_layout()
    fig.savefig(out_path, dpi=100)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description='Plot CDN correlation/temporal graphs')
    ap.add_argument('--sit', type=int, nargs='+', default=[1, 2])
    ap.add_argument('--speed', type=int, nargs='+', default=[20, 25, 30])
    ap.add_argument('--round', type=int, default=1)
    args = ap.parse_args()

    for sit in args.sit:
        for speed in args.speed:
            loaded = load_run(sit, speed, args.round)
            if loaded is None:
                print(f'[SKIP] sit{sit}/speed{speed} round{args.round}: CSVs not found')
                continue
            run_id, rows = loaded
            if not rows:
                print(f'[SKIP] {run_id}: no overlapping time samples between '
                      f'measurements and RSSI logs')
                continue

            out_dir = os.path.join(OUT_ROOT, f'sit{sit}', f'speed{speed}')
            os.makedirs(out_dir, exist_ok=True)
            plot_correlation(rows, sit, speed, os.path.join(out_dir, 'correlation.png'))
            plot_temporal(rows, sit, speed, os.path.join(out_dir, 'temporal.png'))
            print(f'[OK] {run_id}: {len(rows)} samples -> {out_dir}/')


if __name__ == '__main__':
    main()
