#!/usr/bin/env python3
"""
plot_situation1_scalability.py -- Situation 1 (Traffic Density) SDN+DASH,
4th summary chart: how each of the 4 measurable rows degrades as vehicle
count (density) goes 3 -> 5 -> 7. One column, 4 rows, A4 portrait,
matching the visual style of plot_situation1_avg.py.

Per case value = mean across that case's own vehicles:
  - QoE:            mean of each vehicle's own compute_dash_qoe() average
  - Bandwidth:      mean of each vehicle's own network.csv allocated_bw_mbps
  - Packet Loss:    mean of each vehicle's own network.csv icmp_loss_pct
  - Rebuffering:    mean of each vehicle's own summary.json rebuffering_ratio (%)

Usage:
    python3 plot_situation1_scalability.py \
        --cases smoke_3cars_v5:3 smoke_5cars_v2:5 smoke_7cars_v5:7 \
        --out-dir graphs
"""
import csv
import os
import json
import argparse
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

CASE_COLOR = {3: '#1baf7a', 5: '#eda100', 7: '#e34948'}
_MU = 1.0
A4_W, A4_H = 8.27, 11.69


def load(path):
    return list(csv.DictReader(open(path))) if os.path.exists(path) else []


def col(rows, key, cast=float):
    return [cast(r[key]) for r in rows]


def compute_dash_qoe(seg_rows):
    qoes, prev_bitrate = [], None
    for r in seg_rows:
        bitrate = float(r['bitrate_kbps']) / 1000.0
        switch_penalty = (_MU * abs(bitrate - prev_bitrate)
                          if prev_bitrate is not None else 0.0)
        rebuf_s = float(r['stall_duration_s'])
        qoes.append(bitrate - switch_penalty - rebuf_s)
        prev_bitrate = bitrate
    return qoes


def case_metrics(run_dir, run_id, n_cars):
    qoe_means, bw_means, loss_means, rebuf_pcts = [], [], [], []
    for i in range(1, n_cars + 1):
        net = load(os.path.join(run_dir, f'{run_id}_car{i}_network.csv'))
        seg = load(os.path.join(run_dir, f'{run_id}_car{i}_segments.csv'))
        summ_path = os.path.join(run_dir, f'{run_id}_car{i}_summary.json')
        if not net:
            continue
        bw_means.append(np.mean(col(net, 'allocated_bw_mbps')))
        loss_means.append(np.mean(col(net, 'icmp_loss_pct')))
        if seg:
            qoe = compute_dash_qoe(seg)
            if qoe:
                qoe_means.append(np.mean(qoe))
        if os.path.exists(summ_path):
            with open(summ_path) as f:
                rebuf_pcts.append(json.load(f)['rebuffering_ratio'] * 100.0)
    return {
        'qoe': np.mean(qoe_means) if qoe_means else np.nan,
        'bw': np.mean(bw_means) if bw_means else np.nan,
        'loss': np.mean(loss_means) if loss_means else np.nan,
        'rebuf': np.mean(rebuf_pcts) if rebuf_pcts else np.nan,
    }


def make_scalability_plot(cases, out_path):
    """cases: list of (run_dir, run_id, n_cars) sorted by n_cars."""
    xs = [n for _, _, n in cases]
    metrics = [case_metrics(d, r, n) for d, r, n in cases]
    colors = [CASE_COLOR.get(n, '#2a78d6') for n in xs]

    rows = [
        ('Quality of Experience (QoE)', 'qoe', 'QoE (score)'),
        ('Allocated Bandwidth (step2h + contention)', 'bw', 'Bandwidth (Mbps)'),
        ('Packet Loss', 'loss', 'Packet Loss (%)'),
        ('Rebuffering Ratio', 'rebuf', 'Rebuffering Ratio (%)'),
    ]

    fig, axes = plt.subplots(4, 1, figsize=(A4_W, A4_H), facecolor='white')
    fig.subplots_adjust(left=0.13, right=0.95, top=0.92, bottom=0.06, hspace=0.5)
    fig.suptitle('Situation 1: Traffic Density — Scalability Summary (3 vs 5 vs 7 cars)',
                 fontsize=13.5, fontweight='bold', color='#1a1a1a', y=0.975)
    fig.text(0.5, 0.945, 'SDN+DASH, mean per case across all vehicles in that case',
              ha='center', fontsize=9.5, color='#666666')

    for ax, (title, key, ylabel) in zip(axes, rows):
        ys = [m[key] for m in metrics]
        ax.plot(xs, ys, color='#999999', lw=1.6, ls='--', zorder=2)
        ax.scatter(xs, ys, s=110, color=colors, zorder=4, edgecolors='white', linewidths=1.2)
        for x, y in zip(xs, ys):
            ax.annotate(f'{y:.2f}', (x, y), textcoords='offset points',
                        xytext=(0, 9), ha='center', fontsize=8.5, fontweight='semibold')
        ax.set_title(title, fontsize=11, fontweight='semibold', pad=6)
        ax.set_ylabel(ylabel, fontsize=9.5)
        ax.set_xlabel('Number of Vehicles', fontsize=9)
        ax.set_xticks(xs)
        ax.set_xticklabels([f'{x} cars' for x in xs], fontsize=9)
        span = max(ys) - min(ys) if max(ys) != min(ys) else max(abs(max(ys)), 1)
        ax.set_ylim(min(ys) - span * 0.25, max(ys) + span * 0.3)
        ax.set_xlim(min(xs) - 1, max(xs) + 1)
        ax.set_facecolor('#f4f4f4')
        ax.grid(True, color='white', lw=1.0)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['left'].set_color('#cccccc')
        ax.spines['bottom'].set_color('#cccccc')
        ax.tick_params(colors='#555555')

    os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
    fig.savefig(out_path, dpi=200, facecolor='white', edgecolor='none')
    print(f'  saved -> {out_path}')
    plt.close(fig)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description='Situation 1 scalability summary (3/5/7 cars), A4 layout')
    p.add_argument('--cases', nargs='+', required=True,
                    help='run_id:n_cars, e.g. smoke_3cars_v5:3 smoke_5cars_v2:5 smoke_7cars_v5:7')
    p.add_argument('--results-dir', default='results_raw')
    p.add_argument('--out-dir', default='graphs')
    args = p.parse_args()

    parsed = []
    for c in args.cases:
        run_id, n = c.split(':')
        parsed.append((os.path.join(args.results_dir, run_id), run_id, int(n)))
    parsed.sort(key=lambda x: x[2])

    out_path = os.path.join(args.out_dir, 'scalability_summary_3_5_7cars.png')
    make_scalability_plot(parsed, out_path)
