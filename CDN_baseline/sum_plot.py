#!/usr/bin/env python3
"""
sum_plot.py — Summary comparison plots (No-SDN vs SDN)
Reduces 12 individual plots → 2 summary figures (one per situation)

Each figure: 4 rows (QoE / Latency / RSSI / Cache) × 3 cols (20 / 25 / 30 km/h)

Usage:
    python3 sum_plot.py                 # both sits
    python3 sum_plot.py --sit 1         # sit 1 only
    python3 sum_plot.py --out /custom   # custom output root
"""

import csv, os, argparse
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.lines as mlines
import matplotlib.patches as mpatches

# ── Paths ──────────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT    = os.path.dirname(SCRIPT_DIR)
BASE       = os.path.join(PROJECT, "results", "cdn_baseline")

# ── Colors ─────────────────────────────────────────────────────────────────
C_NOSDN = "#2a78d6"
C_SDN   = "#1baf7a"
C_HO    = "#e89c00"
C_HIT   = "#0ca30c"
C_MISS  = "#e34948"
C_UNK   = "#888888"
NOSDN_DOT = {'HIT': C_NOSDN, 'MISS': C_MISS, 'UNKNOWN': C_UNK}
SDN_DOT   = {'HIT': C_HIT,   'MISS': C_MISS, 'UNKNOWN': C_UNK}
CV_MAP    = {'HIT': 1, 'MISS': 0, 'UNKNOWN': 0.5}

SPEEDS = [20, 25, 30]

# ── Helpers ────────────────────────────────────────────────────────────────
def load_csv(path):
    with open(path) as f:
        return list(csv.DictReader(f))

def col(rows, key, cast=float):
    return [cast(r[key]) for r in rows]

def ap_transitions(tn, ap_n):
    return [
        (tn[i], f"{ap_n[i-1].upper()}→{ap_n[i].upper()}")
        for i in range(1, len(ap_n)) if ap_n[i] != ap_n[i-1]
    ]

def add_ho(ax, trans, ylim):
    for t_ho, lbl in trans:
        ax.axvline(t_ho, color=C_HO, lw=0.9, ls='--', alpha=0.6, zorder=3)
        ax.text(t_ho + 0.3, ylim[1] * 0.96, lbl, rotation=90,
                va='top', ha='left', fontsize=6, color=C_HO, alpha=0.85)

# ── Draw helpers per cell ──────────────────────────────────────────────────
def draw_qoe(ax, tn, ts, qoe_n, qoe_s, tmax, trans, show_ylabel):
    ax.plot(tn, qoe_n, color=C_NOSDN, lw=1.4, zorder=4)
    ax.fill_between(tn, qoe_n, alpha=0.12, color=C_NOSDN)
    ax.plot(ts, qoe_s, color=C_SDN,   lw=1.4, zorder=4)
    ax.fill_between(ts, qoe_s, alpha=0.12, color=C_SDN)
    ax.set_ylim(0, 5.5); ax.set_xlim(0, tmax)
    if show_ylabel: ax.set_ylabel('QoE (0–5)', fontsize=9)
    add_ho(ax, trans, (0, 5.5))

def draw_lat(ax, tn, ts, lat_n, lat_s, tmax, trans, show_ylabel):
    ax.plot(tn, lat_n, color=C_NOSDN, lw=1.4, zorder=4)
    ax.fill_between(tn, lat_n, alpha=0.12, color=C_NOSDN)
    ax.plot(ts, lat_s, color=C_SDN,   lw=1.4, zorder=4)
    ax.fill_between(ts, lat_s, alpha=0.12, color=C_SDN)
    ax.set_ylim(0, 3.5); ax.set_xlim(0, tmax)
    if show_ylabel: ax.set_ylabel('Latency (s)', fontsize=9)
    add_ho(ax, trans, (0, 3.5))

def draw_rssi(ax, tn, ts, rssi_n, rssi_s, tmax, trans, show_ylabel):
    ax.plot(tn, rssi_n, color=C_NOSDN, lw=1.2, alpha=0.6, zorder=3)
    ax.plot(ts, rssi_s, color=C_SDN,   lw=1.2, alpha=0.6, zorder=3)
    ax.scatter(tn, rssi_n, s=12, color=C_NOSDN, zorder=5, marker='o', edgecolors='none')
    ax.scatter(ts, rssi_s, s=12, color=C_SDN,   zorder=5, marker='s', edgecolors='none')
    ax.set_ylim(-75, -22); ax.set_xlim(0, tmax)
    if show_ylabel: ax.set_ylabel('RSSI (dBm)', fontsize=9)
    add_ho(ax, trans, (-75, -22))

def draw_cache(ax, tn, ts, cache_n, cache_s, tmax, trans, show_ylabel):
    cv_n = [CV_MAP.get(c, 0.5) for c in cache_n]
    cv_s = [CV_MAP.get(c, 0.5) for c in cache_s]
    ax.step(tn, cv_n, color=C_NOSDN, lw=0.8, where='post', alpha=0.3, zorder=2)
    ax.step(ts, cv_s, color=C_SDN,   lw=0.8, where='post', alpha=0.3, zorder=2)
    for t2, v2, c2 in zip(tn, cv_n, cache_n):
        ax.scatter(t2, v2 - 0.07, s=18, marker='o',
                   color=NOSDN_DOT[c2], edgecolors='none', zorder=5)
    for t2, v2, c2 in zip(ts, cv_s, cache_s):
        ax.scatter(t2, v2 + 0.07, s=18, marker='s',
                   color=SDN_DOT[c2], edgecolors='none', zorder=5)
    ax.set_ylim(-0.4, 1.4); ax.set_xlim(0, tmax)
    ax.set_yticks([0, 0.5, 1])
    ax.set_yticklabels(['M', 'U', 'H'], fontsize=7)
    if show_ylabel: ax.set_ylabel('Cache', fontsize=9)
    add_ho(ax, trans, (-0.4, 1.4))

# ── Style ──────────────────────────────────────────────────────────────────
plt.rcParams.update({
    'font.family':       'DejaVu Sans',
    'axes.facecolor':    '#f4f4f4',
    'figure.facecolor':  'white',
    'axes.grid':         True,
    'grid.color':        'white',
    'grid.linewidth':    1.0,
    'axes.spines.top':   False,
    'axes.spines.right': False,
    'axes.edgecolor':    '#cccccc',
    'xtick.color':       '#555555',
    'ytick.color':       '#555555',
    'axes.labelcolor':   '#333333',
})

# ── Main ────────────────────────────────────────────────────────────────────
def make_summary(sit, out_root):
    sit_label = "Popular Content" if sit == 1 else "Unpopular Content"

    fig, axes = plt.subplots(
        4, 3, figsize=(16, 11),
        facecolor='white',
        gridspec_kw={'hspace': 0.55, 'wspace': 0.22}
    )
    fig.suptitle(
        f"No-SDN vs SDN — Situation {sit} ({sit_label})"
        f"   |   Speed: 20 / 25 / 30 km/h",
        fontsize=13, fontweight='bold', color='#1a1a1a', y=0.98
    )

    for ci, spd in enumerate(SPEEDS):
        axes[0, ci].set_title(f"{spd} km/h", fontsize=10,
                               fontweight='semibold', pad=6, color='#333')

    for ci, spd in enumerate(SPEEDS):
        path_n = (f"{out_root}/no_sdn/sit{sit}/speed{spd}/"
                  f"cdn_baseline_sit{sit}_spd{spd}_r1/"
                  f"cdn_baseline_sit{sit}_spd{spd}_r1.csv")
        path_s = (f"{out_root}/sdn/sit{sit}/speed{spd}/"
                  f"cdn_baseline_sdn_sit{sit}_spd{spd}_r1/"
                  f"cdn_baseline_sdn_sit{sit}_spd{spd}_r1.csv")

        rn = load_csv(path_n)
        rs = load_csv(path_s)

        tn      = col(rn, 't');           ts      = col(rs, 't')
        qoe_n   = col(rn, 'qoe');         qoe_s   = col(rs, 'qoe')
        lat_n   = col(rn, 'latency_s');   lat_s   = col(rs, 'latency_s')
        rssi_n  = col(rn, 'rssi');        rssi_s  = col(rs, 'rssi')
        ap_n    = col(rn, 'ap', str)
        cache_n = col(rn, 'cache', str);  cache_s = col(rs, 'cache', str)
        tmax    = max(max(tn), max(ts))
        trans   = ap_transitions(tn, ap_n)
        show_y  = (ci == 0)

        draw_qoe(axes[0,ci],   tn, ts, qoe_n,   qoe_s,   tmax, trans, show_y)
        draw_lat(axes[1,ci],   tn, ts, lat_n,   lat_s,   tmax, trans, show_y)
        draw_rssi(axes[2,ci],  tn, ts, rssi_n,  rssi_s,  tmax, trans, show_y)
        draw_cache(axes[3,ci], tn, ts, cache_n, cache_s, tmax, trans, show_y)

        axes[3, ci].set_xlabel('Time (s)', fontsize=8)
        for ri in range(4):
            axes[ri, ci].tick_params(axis='both', labelsize=7.5)

    # shared legend
    leg_handles = [
        mlines.Line2D([],[],color=C_NOSDN,lw=2,label='No-SDN'),
        mlines.Line2D([],[],color=C_SDN,  lw=2,label='SDN'),
        mlines.Line2D([],[],color=C_HO,lw=1.2,ls='--',label='Handover'),
        mlines.Line2D([],[],marker='o',color='w',markerfacecolor=C_NOSDN,
                      markersize=7,label='No-SDN HIT (●)'),
        mlines.Line2D([],[],marker='s',color='w',markerfacecolor=C_HIT,
                      markersize=7,label='SDN HIT (■)'),
        mpatches.Patch(color=C_MISS, label='MISS'),
        mpatches.Patch(color=C_UNK,  label='UNKNOWN'),
    ]
    fig.legend(handles=leg_handles, loc='lower center', ncol=7,
               fontsize=8.5, framealpha=0.9,
               bbox_to_anchor=(0.5, -0.01),
               handlelength=1.4, handletextpad=0.5, columnspacing=1.2)
    fig.subplots_adjust(bottom=0.07)

    fname = f"summary_sit{sit}.png"
    for mode_dir in ['no_sdn', 'sdn']:
        out_dir = os.path.join(out_root, mode_dir, f"sit{sit}")
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, fname)
        fig.savefig(out, dpi=150, bbox_inches='tight',
                    facecolor='white', edgecolor='none')
        print(f"  saved → {out}")
    plt.close(fig)


# ── CLI ────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Generate summary comparison plots')
    parser.add_argument('--sit', type=int, nargs='+', default=[1, 2])
    parser.add_argument('--out', type=str, default=BASE,
                        help='Results root (default: results/cdn_baseline)')
    args = parser.parse_args()

    for sit in args.sit:
        print(f"\n[Summary sit{sit}]")
        make_summary(sit, args.out)

    print("\nDone.")
