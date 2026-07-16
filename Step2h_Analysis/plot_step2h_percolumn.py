#!/usr/bin/env python3
"""
plot_step2h_percolumn.py -- step2h baseline (dash-baseline/results_smoke/
test06_step2h_v3.csv), single column, using the EXACT same 5-row visual
template as Situation2_DASH/plot_situation2_percolumn.py (itself mirroring
Situation1_DASH/plot_situation1_percar.py): QoE / RSSI / Imposed Bandwidth
/ Packet Loss / Quality-Rendition, RSU zone bands, dashed handover lines,
QoE annotation box. Only one column here since this is a single run (no
speed/car axis), all layout/color/QoE-formula code ported verbatim.

Usage:
    python3 plot_step2h_percolumn.py
"""
import csv
import os
import sys
import argparse
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, '..', 'dash-baseline'))
import baseline_4rsu_model as M4

DEFAULT_CSV = os.path.join(_HERE, '..', 'dash-baseline', 'runs',
                            '2026-07-13_step2h-outage-handover-model',
                            'test05_step2h_v2.csv')

C_DASH = '#2a78d6'
C_HO = '#e89c00'
RSU_BAND = {
    'rsu1': ('#2a78d6', 0.08), 'rsu2': ('#1baf7a', 0.08),
    'rsu3': ('#eda100', 0.09), 'rsu4': ('#e34948', 0.08),
}
_BITRATE_MBPS = {'360p': 1.0, '720p': 2.5, '1080p': 5.0}
_MU = 1.0

ROW_TITLES = ['Quality of Experience (QoE)', 'RSSI',
              'Imposed Bandwidth (step2h)', 'Packet Loss',
              'Quality of Rendition']


def load(path):
    return list(csv.DictReader(open(path)))


def col(rows, key, cast=float):
    return [cast(r[key]) for r in rows]


def compute_dash_qoe_tick(rows):
    """Per-tick Yin et al. QoE term -- same formula/shape as
    Situation2_DASH/plot_situation2_percolumn.py's compute_dash_qoe_tick():
    bitrate from the 'quality' label, switch penalty vs the previous tick,
    T_k (rebuffer term) = real elapsed dt for that tick when stall=1."""
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


def handover_events(rows):
    x = col(rows, 'x')
    rsu = col(rows, 'rsu', str)
    ho = col(rows, 'handover', int)
    out = []
    for i in range(len(x)):
        if ho[i] and i > 0:
            out.append((x[i], f'RSU{rsu[i-1]}→RSU{rsu[i]}'))
    return out


def add_rsu_bands(ax, x, rsu, xmax, ylim, show_label=True):
    xrange = (xmax - min(x)) or 1
    last_label_x = None
    for x0, x1, a in zone_spans(x, rsu, xmax):
        color, alpha = RSU_BAND.get('rsu' + a, ('#aaaaaa', 0.07))
        ax.axvspan(x0, x1, color=color, alpha=alpha, zorder=0, linewidth=0)
        if not show_label:
            continue
        cx = (x0 + x1) / 2
        wide_enough = (x1 - x0) / xrange > 0.04
        far_enough = last_label_x is None or (cx - last_label_x) / xrange > 0.08
        if wide_enough and far_enough:
            ax.text(cx, ylim[0] + (ylim[1] - ylim[0]) * 0.03, 'RSU' + a,
                    ha='center', va='bottom', fontsize=6.5, color=color,
                    alpha=0.85, zorder=1)
            last_label_x = cx


def add_handover_lines(ax, events, ylim, x_offset=15, show_label=True):
    for hx, lbl in events:
        ax.axvline(hx, color=C_HO, lw=1.0, ls='--', alpha=0.7, zorder=3)
        if show_label:
            ax.text(hx + x_offset, ylim[1] * 0.97, lbl, rotation=90,
                    va='top', ha='left', fontsize=6, color=C_HO, alpha=0.9)


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


def make_plot(csv_path, out_path):
    rows = load(csv_path)
    if not rows:
        print('  [ERROR] no rows loaded')
        return

    # A4 portrait, 210x297mm = 8.27x11.69in
    fig, axes = plt.subplots(5, 1, figsize=(8.27, 11.69),
                              facecolor='white', squeeze=False)
    fig.subplots_adjust(hspace=0.75, wspace=0.32, top=0.94, bottom=0.045,
                         left=0.09, right=0.97)
    fig.suptitle('DASH Baseline 4RSU', fontsize=15, fontweight='bold',
                 color='#1a1a1a', ha='center', y=0.985)

    xmin_global = M4.START_X
    xmax_global = M4.END_X

    col_i = 0
    x = col(rows, 'x')
    rssi = col(rows, 'rssi')
    bw = col(rows, 'bw_mbps')
    loss = col(rows, 'loss')
    rsu = col(rows, 'rsu', str)
    qidx = col(rows, 'quality_idx', int)
    qoe = compute_dash_qoe_tick(rows)
    net_qoe = sum(qoe)
    events = handover_events(rows)

    # 1. QoE
    ax = axes[0, col_i]
    ax.plot(x, qoe, color=C_DASH, lw=1.8, marker='o', markersize=2.6, zorder=4)
    ax.fill_between(x, qoe, alpha=0.15, color=C_DASH)
    ax.set_xlim(xmin_global, xmax_global)
    ax.set_title(ROW_TITLES[0], fontsize=9.5, fontweight='semibold', pad=4)
    ax.set_ylabel('QoE', fontsize=9)
    ax.set_xlabel('Mobility Position (m)', fontsize=9)
    ax.text(0.99, 0.06, f'Net={net_qoe:.1f} (avg {net_qoe/len(qoe):.3f})',
            transform=ax.transAxes, ha='right', va='bottom', fontsize=7,
            fontweight='semibold',
            bbox=dict(boxstyle='round,pad=0.25', facecolor='white',
                      edgecolor='#cccccc', alpha=0.85))
    add_rsu_bands(ax, x, rsu, xmax_global, ax.get_ylim())
    add_handover_lines(ax, events, ax.get_ylim())

    # 2. RSSI
    ax = axes[1, col_i]
    ax.plot(x, rssi, color=C_DASH, lw=1.4, zorder=3, alpha=0.6)
    ax.scatter(x, rssi, s=14, color=C_DASH, zorder=5, marker='o', edgecolors='none')
    rssi_ylim = (min(rssi) - 5, max(rssi) + 5)
    ax.set_ylim(*rssi_ylim); ax.set_xlim(xmin_global, xmax_global)
    ax.set_title(ROW_TITLES[1], fontsize=9.5, fontweight='semibold', pad=4)
    ax.set_ylabel('RSSI (dBm)', fontsize=9)
    ax.set_xlabel('Mobility Position (m)', fontsize=9)
    add_rsu_bands(ax, x, rsu, xmax_global, rssi_ylim, show_label=False)
    add_handover_lines(ax, events, rssi_ylim, show_label=False)

    # 3. Bandwidth
    ax = axes[2, col_i]
    bw_max = max(max(bw) * 1.15, 1)
    ax.step(x, bw, color=C_DASH, lw=1.8, where='post', zorder=4)
    ax.fill_between(x, bw, step='post', alpha=0.15, color=C_DASH)
    ax.set_ylim(0, bw_max); ax.set_xlim(xmin_global, xmax_global)
    ax.set_title(ROW_TITLES[2], fontsize=9.5, fontweight='semibold', pad=4)
    ax.set_ylabel('Bandwidth (Mbps)', fontsize=9)
    ax.set_xlabel('Mobility Position (m)', fontsize=9)
    add_rsu_bands(ax, x, rsu, xmax_global, (0, bw_max), show_label=False)
    add_handover_lines(ax, events, (0, bw_max), show_label=False)

    # 4. Packet loss
    ax = axes[3, col_i]
    ax.plot(x, loss, color=C_DASH, lw=1.8, marker='o', markersize=2.6, zorder=4)
    ax.fill_between(x, loss, alpha=0.15, color=C_DASH)
    loss_max = max(max(loss) * 1.2, 5)
    ax.set_ylim(0, loss_max); ax.set_xlim(xmin_global, xmax_global)
    ax.set_title(ROW_TITLES[3], fontsize=9.5, fontweight='semibold', pad=4)
    ax.set_ylabel('Loss (%)', fontsize=9)
    ax.set_xlabel('Mobility Position (m)', fontsize=9)
    add_rsu_bands(ax, x, rsu, xmax_global, (0, loss_max), show_label=False)
    add_handover_lines(ax, events, (0, loss_max), show_label=False)

    # 5. Quality / Rendition
    ax = axes[4, col_i]
    ax.step(x, qidx, color=C_DASH, lw=1.8, where='post', zorder=4)
    ax.fill_between(x, qidx, step='post', alpha=0.15, color=C_DASH)
    ax.set_ylim(-0.4, 2.4); ax.set_xlim(xmin_global, xmax_global)
    ax.set_yticks([0, 1, 2])
    ax.set_yticklabels(['360p', '720p', '1080p'], fontsize=8)
    ax.set_ylabel('Rendition', fontsize=9)
    ax.set_xlabel('Mobility Position (m)', fontsize=9)
    ax.set_title(ROW_TITLES[4], fontsize=9.5, fontweight='semibold', pad=4)
    add_rsu_bands(ax, x, rsu, xmax_global, (-0.4, 2.4), show_label=False)
    add_handover_lines(ax, events, (-0.4, 2.4), show_label=False)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    # no bbox_inches='tight' here -- that crops to content and would break
    # the exact A4 (8.27x11.69in) page size set on the figure above.
    fig.savefig(out_path, dpi=200, facecolor='white', edgecolor='none')
    print(f'  saved -> {out_path}  Net QoE={net_qoe:.1f} (avg {net_qoe/len(qoe):.3f}, n={len(rows)})')
    plt.close(fig)


if __name__ == '__main__':
    p = argparse.ArgumentParser(
        description='Step2h baseline single run, mirrors plot_situation2_percolumn.py template')
    p.add_argument('--csv', type=str, default=DEFAULT_CSV)
    p.add_argument('--out', type=str,
                    default=os.path.join(_HERE, 'plots', 'step2h_percolumn_test05.png'))
    args = p.parse_args()
    make_plot(args.csv, args.out)
