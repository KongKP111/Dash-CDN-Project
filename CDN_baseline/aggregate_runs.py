#!/usr/bin/env python3
"""
aggregate_runs.py — CDN Baseline
==================================
Aggregate 10 rounds per (sit, speed) → summary stats + plots.
Supports no_sdn / sdn / both (comparison) modes.

Usage:
  python3 aggregate_runs.py results/cdn_baseline --mode no_sdn
  python3 aggregate_runs.py results/cdn_baseline --mode sdn
  python3 aggregate_runs.py results/cdn_baseline --mode both
"""

import os, sys, glob, csv, json, argparse
from collections import defaultdict

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import baseline_model as M


def load_csv(path):
    rows = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for r in reader:
            try:
                rows.append({
                    't':            float(r['t']),
                    'x':            float(r['x']),
                    'ap':           r['ap'],
                    'rssi':         float(r['rssi']),
                    'bw_mbps':      float(r['bw_mbps']),
                    'cache':        r['cache'],
                    'lat':          float(r['latency_s']),
                    'spd_bps':      float(r['speed_bps']),
                    'loss':         float(r['loss_pct']),
                    'qoe':          float(r['qoe']),
                    'handover':     int(r['handover']),
                    'vehicle_speed_kmh': int(r.get('vehicle_speed_kmh', 0)),
                })
            except (ValueError, KeyError):
                continue
    return rows


def collect_runs(root, mode, sit, speed):
    prefix  = 'cdn_baseline_sdn' if mode == 'sdn' else 'cdn_baseline'
    pattern = os.path.join(root, mode, 'sit%d' % sit, 'speed%d' % speed,
                           '%s_*' % prefix, '*.csv')
    files   = sorted(glob.glob(pattern))
    runs    = []
    for f in files:
        if os.path.getsize(f) > 0:
            rows = load_csv(f)
            if rows:
                runs.append(rows)
    return runs


def compute_stats(all_runs, sit, speed):
    rows = []
    for i, run in enumerate(all_runs, 1):
        n        = len(run)
        hits     = sum(1 for r in run if r['cache'] == 'HIT')
        stall_ev = sum(1 for r in run if r['lat'] >= 3.0)
        stall_s  = sum(r['lat'] for r in run if r['lat'] >= 3.0)
        rows.append({
            'round':               i,
            'n_samples':           n,
            'avg_latency_s':       round(np.mean([r['lat']     for r in run]), 4),
            'avg_throughput_kbps': round(np.mean([r['spd_bps'] for r in run]) / 1000, 2),
            'cache_hit_ratio':     round(hits / n if n else 0, 4),
            'avg_qoe':             round(np.mean([r['qoe']  for r in run]), 4),
            'avg_loss_pct':        round(np.mean([r['loss'] for r in run]), 4),
            'total_stall_s':       round(stall_s, 2),
            'stall_events':        stall_ev,
            'n_handovers':         sum(r['handover'] for r in run),
        })
    def col(k): return [r[k] for r in rows]
    summary = {
        'sit': sit, 'speed_kmh': speed, 'n_rounds': len(rows),
        'avg_latency_s':       round(np.mean(col('avg_latency_s')), 4),
        'std_latency_s':       round(np.std(col('avg_latency_s')),  4),
        'avg_throughput_kbps': round(np.mean(col('avg_throughput_kbps')), 2),
        'avg_cache_hit_ratio': round(np.mean(col('cache_hit_ratio')), 4),
        'avg_qoe':             round(np.mean(col('avg_qoe')), 4),
        'std_qoe':             round(np.std(col('avg_qoe')),  4),
        'avg_loss_pct':        round(np.mean(col('avg_loss_pct')), 4),
        'avg_stall_events':    round(np.mean(col('stall_events')), 2),
        'avg_total_stall_s':   round(np.mean(col('total_stall_s')), 2),
    }
    return rows, summary


def save_stats(rows, summary, out_dir, tag):
    csv_path  = os.path.join(out_dir, '%s_perrun.csv' % tag)
    json_path = os.path.join(out_dir, '%s_summary.json' % tag)
    with open(csv_path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader(); w.writerows(rows)
    with open(json_path, 'w') as f:
        json.dump(summary, f, indent=2)
    print('  Stats: %s' % csv_path)
    print('  JSON:  %s' % json_path)


def _by_x(all_runs, key):
    d = defaultdict(list)
    for run in all_runs:
        for r in run:
            d[int(round(r['x'] / 10.0)) * 10].append(r[key])
    return d


def plot_temporal(all_runs, sit, speed, out_dir, label, color):
    lat_bx = _by_x(all_runs, 'lat')
    rs_bx  = _by_x(all_runs, 'rssi')
    ho_bx  = defaultdict(int)
    for run in all_runs:
        for r in run:
            if r['handover']:
                ho_bx[int(round(r['x'] / 10.0)) * 10] += 1

    xs      = sorted(lat_bx.keys())
    med_lat = [np.median(lat_bx[x]) for x in xs]
    p25_lat = [np.percentile(lat_bx[x], 25) for x in xs]
    p75_lat = [np.percentile(lat_bx[x], 75) for x in xs]
    med_rs  = [np.median(rs_bx[x]) for x in xs]
    ho_xs   = [x for x in xs if ho_bx[x] >= len(all_runs) // 2]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
    ax1.plot(xs, med_lat, color=color, lw=2.5, label='Median latency')
    ax1.fill_between(xs, p25_lat, p75_lat, color=color, alpha=0.2, label='25-75%')
    for hx in ho_xs:
        ax1.axvline(hx, color='orange', ls=':', lw=1.5, alpha=0.8)
    ax1.set_ylabel('Request latency (s)', fontsize=12)
    ax1.set_title('(a) CDN latency [%s]  Sit %d, %d km/h  (n=%d rounds)' % (
        label, sit, speed, len(all_runs)), fontsize=13)
    ax1.grid(True, alpha=0.3)
    ax1.legend(loc='upper right', fontsize=10)

    ax2.plot(xs, med_rs, color='#7d5ca0', lw=2, label='Median RSSI')
    for hx in ho_xs:
        lbl = 'Handover' if hx == ho_xs[0] else None
        ax2.axvline(hx, color='orange', ls=':', lw=1.5, alpha=0.8, label=lbl)
    ax2.set_xlabel('Vehicle position (m)', fontsize=12)
    ax2.set_ylabel('RSSI (dBm)', fontsize=12)
    ax2.set_title('(b) RSSI along route', fontsize=13)
    ax2.grid(True, alpha=0.3)
    ax2.legend(loc='lower left', fontsize=10)

    plt.tight_layout()
    out = os.path.join(out_dir, 'temporal.png')
    plt.savefig(out, dpi=120); plt.close()
    print('  Plot: %s' % out)


def plot_correlation(all_runs, sit, speed, out_dir, label, color):
    xs, ys = [], []
    for run in all_runs:
        for r in run:
            xs.append(r['rssi'])
            ys.append(r['lat'] * 1000)
    if not xs: return
    r_val = np.corrcoef(xs, ys)[0, 1]

    fig, ax = plt.subplots(figsize=(8, 6))
    ax.scatter(xs, ys, color=color, alpha=0.35, s=30)
    if len(xs) > 1:
        c = np.polyfit(xs, ys, 1)
        xl = np.array([min(xs), max(xs)])
        ax.plot(xl, c[0]*xl + c[1], 'k--', lw=2)
    ax.set_xlabel('RSSI (dBm)', fontsize=13)
    ax.set_ylabel('Request latency (ms)', fontsize=13)
    ax.set_title('RSSI vs latency [%s]  Sit %d, %d km/h  r=%.2f  (n=%d rounds)' % (
        label, sit, speed, r_val, len(all_runs)), fontsize=13)
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    out = os.path.join(out_dir, 'correlation.png')
    plt.savefig(out, dpi=120); plt.close()
    print('  Plot: %s' % out)


def plot_comparison(runs_n, runs_s, sit, speed, out_dir):
    """No-SDN vs SDN: latency + QoE comparison."""
    lat_n = _by_x(runs_n, 'lat');  lat_s = _by_x(runs_s, 'lat')
    qoe_n = _by_x(runs_n, 'qoe'); qoe_s = _by_x(runs_s, 'qoe')
    xs = sorted(set(lat_n.keys()) & set(lat_s.keys()))

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 9), sharex=True)

    med_n = [np.median(lat_n[x]) for x in xs]
    med_s = [np.median(lat_s[x]) for x in xs]
    p25_n = [np.percentile(lat_n[x], 25) for x in xs]
    p75_n = [np.percentile(lat_n[x], 75) for x in xs]
    p25_s = [np.percentile(lat_s[x], 25) for x in xs]
    p75_s = [np.percentile(lat_s[x], 75) for x in xs]

    ax1.plot(xs, med_n, color='#d62728', lw=2.5, label='No-SDN')
    ax1.fill_between(xs, p25_n, p75_n, color='#d62728', alpha=0.15)
    ax1.plot(xs, med_s, color='#1f77b4', lw=2.5, label='With-SDN')
    ax1.fill_between(xs, p25_s, p75_s, color='#1f77b4', alpha=0.15)
    for xpos in M.AP_POSITIONS[1:]:
        ax1.axvline(xpos, color='orange', ls=':', lw=1.5, alpha=0.6)
    ax1.set_ylabel('Request latency (s)', fontsize=12)
    ax1.set_title('(a) Latency: No-SDN vs SDN  [Sit %d, %d km/h]  n=%d rounds' % (
        sit, speed, len(runs_n)), fontsize=13)
    ax1.grid(True, alpha=0.3)
    ax1.legend(fontsize=11)

    med_qn = [np.median(qoe_n[x]) for x in xs]
    med_qs = [np.median(qoe_s[x]) for x in xs]
    ax2.plot(xs, med_qn, color='#d62728', lw=2.5, label='No-SDN QoE')
    ax2.plot(xs, med_qs, color='#1f77b4', lw=2.5, label='With-SDN QoE')
    for xpos in M.AP_POSITIONS[1:]:
        ax2.axvline(xpos, color='orange', ls=':', lw=1.5, alpha=0.6,
                    label='Handover zone' if xpos == M.AP_POSITIONS[1] else None)
    ax2.set_xlabel('Vehicle position (m)', fontsize=12)
    ax2.set_ylabel('QoE score (1-5)', fontsize=12)
    ax2.set_ylim(0.5, 5.5)
    ax2.set_title('(b) QoE comparison', fontsize=13)
    ax2.grid(True, alpha=0.3)
    ax2.legend(fontsize=11)

    plt.tight_layout()
    out = os.path.join(out_dir, 'comparison_sit%d_spd%d.png' % (sit, speed))
    plt.savefig(out, dpi=120); plt.close()
    print('  Comparison: %s' % out)


def process_mode(root, mode, sits, speeds, out_root):
    color = '#d62728' if mode == 'no_sdn' else '#1f77b4'
    label = 'No-SDN' if mode == 'no_sdn' else 'With-SDN'
    for sit in sits:
        for speed in speeds:
            print('\n[%s] sit%d/speed%d' % (label.upper(), sit, speed))
            runs = collect_runs(root, mode, sit, speed)
            if not runs:
                print('  No data — skip')
                continue
            print('  Loaded %d rounds' % len(runs))
            out_dir = os.path.join(out_root, mode, 'sit%d' % sit, 'speed%d' % speed)
            os.makedirs(out_dir, exist_ok=True)
            tag = 'cdn_baseline_%s_sit%d_spd%d' % (mode, sit, speed)
            rows, summary = compute_stats(runs, sit, speed)
            save_stats(rows, summary, out_dir, tag)
            plot_temporal(runs, sit, speed, out_dir, label, color)
            plot_correlation(runs, sit, speed, out_dir, label, color)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('root', help='results/cdn_baseline directory')
    p.add_argument('--mode', choices=['no_sdn', 'sdn', 'both'], default='both')
    args = p.parse_args()

    out_root = os.path.join(args.root, 'summary')
    os.makedirs(out_root, exist_ok=True)
    sits = [1, 2]; speeds = [20, 25, 30]

    if args.mode in ('no_sdn', 'both'):
        process_mode(args.root, 'no_sdn', sits, speeds, out_root)
    if args.mode in ('sdn', 'both'):
        process_mode(args.root, 'sdn', sits, speeds, out_root)

    if args.mode == 'both':
        print('\n[COMPARISON] Generating comparison plots...')
        comp_dir = os.path.join(out_root, 'comparison')
        os.makedirs(comp_dir, exist_ok=True)
        for sit in sits:
            for speed in speeds:
                rn = collect_runs(args.root, 'no_sdn', sit, speed)
                rs = collect_runs(args.root, 'sdn',    sit, speed)
                if rn and rs:
                    plot_comparison(rn, rs, sit, speed, comp_dir)
                else:
                    print('  [SKIP] sit%d/speed%d — missing data' % (sit, speed))

    print('\n' + '='*60)
    print('Done. Results in %s/' % out_root)
    print('='*60)


if __name__ == '__main__':
    main()