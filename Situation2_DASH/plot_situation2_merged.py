#!/usr/bin/env python3
"""
plot_situation2_merged.py -- Situation 2 (Mobility Speed) SDN+DASH,
all 3 speeds merged into ONE column (5 stacked panels total, one line per
speed in each panel) -- requested as a simpler alternative to
plot_situation2_percolumn.py's one-column-per-speed layout.

QoE row: real per-tick Yin et al. formula, same as
plot_situation2_percolumn.py's compute_dash_qoe_tick() (bitrate from the
'quality' label, switch penalty vs. the previous tick, real elapsed dt as
the rebuffer term on a stall tick).

Usage:
    python3 plot_situation2_merged.py --speeds 80 100 120
"""
import csv
import os
import sys
import glob
import argparse
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import baseline_model as M
import baseline_4rsu_model as M4

NEW_ROOT = os.path.join(_HERE, 'results_hightspeed')

SPEED_COLOR = {20: '#1baf7a', 80: '#2a78d6', 100: '#eda100', 120: '#e34948'}
RSU_BAND = {
    'rsu1': ('#2a78d6', 0.07), 'rsu2': ('#1baf7a', 0.07),
    'rsu3': ('#eda100', 0.08), 'rsu4': ('#e34948', 0.07),
}
_BITRATE_MBPS = {'360p': 1.0, '720p': 2.5, '1080p': 5.0}
_MU = 1.0

ROW_SPECS = [
    ('Quality of Experience (QoE)', 'QoE (score)'),
    ('RSSI', 'RSSI (dBm)'),
    ('Imposed Bandwidth', 'Bandwidth (Mbps)'),
    ('Packet Loss', 'Loss (%)'),
    ('Quality of Rendition', 'Rendition (p)'),
]


def find_run(speed):
    pattern = os.path.join(NEW_ROOT, f'speed{speed}', '*.csv')
    matches = glob.glob(pattern)
    return max(matches, key=os.path.getmtime) if matches else None


def load(path):
    return list(csv.DictReader(open(path))) if path and os.path.exists(path) else []


def col(rows, key, cast=float):
    return [cast(r[key]) for r in rows]


def compute_dash_qoe_tick(rows):
    """Per-tick Yin et al. QoE term -- see plot_situation2_percolumn.py for
    the full rationale (real dt as T_k, not a flat 1.0-per-tick constant)."""
    t = col(rows, 't')
    dt = [t[0]] + [t[i] - t[i - 1] for i in range(1, len(t))]
    qoes, prev_bitrate = [], None
    for r, d in zip(rows, dt):
        bitrate = _BITRATE_MBPS.get(r['quality'], 0.0)
        switch_penalty = (_MU * abs(bitrate - prev_bitrate)
                           if prev_bitrate is not None else 0.0)
        rebuf_s = d if int(r['stall']) else 0.0
        qoes.append(bitrate - switch_penalty - rebuf_s)
        prev_bitrate = bitrate
    return qoes


def zone_spans(x, rsu, xmax):
    spans, i = [], 0
    while i < len(x):
        a = rsu[i]; j = i
        while j < len(x) and rsu[j] == a:
            j += 1
        spans.append((x[i], x[j] if j < len(x) else xmax, a))
        i = j
    return spans


def add_rsu_bands(ax, x, rsu, xmax):
    for x0, x1, a in zone_spans(x, rsu, xmax):
        color, alpha = RSU_BAND.get('rsu' + a, ('#aaaaaa', 0.05))
        ax.axvspan(x0, x1, color=color, alpha=alpha, zorder=0, linewidth=0)


plt.rcParams.update({
    'font.family': 'DejaVu Sans',
    'axes.facecolor': '#f4f4f4',
    'figure.facecolor': 'white',
    'axes.grid': True,
    'grid.color': 'white',
    'grid.linewidth': 1.0,
    'axes.spines.top': False,
    'axes.spines.right': False,
    'axes.edgecolor': '#cccccc',
    'xtick.color': '#555555',
    'ytick.color': '#555555',
    'axes.labelcolor': '#333333',
})


def make_plot(speeds, out_path):
    cases = {}
    for speed in speeds:
        path = find_run(speed)
        rows = load(path)
        if not rows:
            print(f'  [WARN] no run found for speed={speed}')
            continue
        cases[speed] = rows
    if not cases:
        print('  [ERROR] nothing to plot')
        return

    xmin, xmax = M4.START_X, M4.END_X
    fig, axes = plt.subplots(5, 1, figsize=(11, 16), facecolor='white')
    fig.subplots_adjust(hspace=0.55, top=0.94)
    fig.suptitle('Situation 2: Mobility Speed', fontsize=15,
                 fontweight='bold', color='#1a1a1a', y=0.98)

    ref_rows = max(cases.values(), key=lambda r: len(r))
    ref_x, ref_rsu = col(ref_rows, 'x'), col(ref_rows, 'rsu', str)
    for ax in axes:
        add_rsu_bands(ax, ref_x, ref_rsu, xmax)

    ax_qoe, ax_rssi, ax_bw, ax_loss, ax_rend = axes

    for speed in sorted(cases):
        rows = cases[speed]
        color = SPEED_COLOR.get(speed, '#333333')
        label = f'{speed} km/h'
        x = col(rows, 'x')
        qoe = compute_dash_qoe_tick(rows)
        rssi = col(rows, 'rssi')
        bw = col(rows, 'bw_mbps')
        loss = col(rows, 'loss')
        qidx = col(rows, 'quality_idx', int)

        ax_qoe.plot(x, qoe, color=color, lw=1.6, marker='o', markersize=2.4,
                    alpha=0.9, label=label, zorder=4)
        ax_rssi.plot(x, rssi, color=color, lw=1.4, alpha=0.85, zorder=4)
        ax_bw.step(x, bw, color=color, lw=1.6, where='post', alpha=0.85, zorder=4)
        ax_loss.plot(x, loss, color=color, lw=1.4, alpha=0.85, zorder=4)
        ax_rend.step(x, qidx, color=color, lw=1.6, where='post', alpha=0.85, zorder=4)

    for ax, (title, ylabel) in zip(axes, ROW_SPECS):
        ax.set_xlim(xmin, xmax)
        ax.set_title(title, fontsize=11.5, fontweight='semibold', pad=6)
        ax.set_ylabel(ylabel, fontsize=10)
        ax.set_xlabel('Mobility Position (m)', fontsize=10)

    ax_loss.set_ylim(bottom=0)
    ax_rend.set_ylim(-0.4, 2.4)
    ax_rend.set_yticks([0, 1, 2])
    ax_rend.set_yticklabels(['360p', '720p', '1080p'], fontsize=9)

    ax_qoe.legend(loc='upper right', fontsize=10, framealpha=0.9, title='Speed')

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    print(f'  saved -> {out_path}')
    plt.close(fig)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description='Situation 2 (SDN+DASH), 3 speeds merged into one column')
    p.add_argument('--speeds', type=int, nargs='+', default=[80, 100, 120])
    p.add_argument('--out', type=str,
                    default=os.path.join(_HERE, 'plots', 'merged_speed_comparison.png'))
    args = p.parse_args()
    make_plot(args.speeds, args.out)
