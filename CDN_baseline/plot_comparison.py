#!/usr/bin/env python3
"""
plot_comparison.py — Generate No-SDN vs SDN comparison plots
Outputs: results/cdn_baseline/{no_sdn,sdn}/sit{N}/speed{S}/comparison_sit{N}_spd{S}.png

Usage:
    python3 plot_comparison.py                        # all sit x speed
    python3 plot_comparison.py --sit 1 --speed 20    # specific combo
    python3 plot_comparison.py --out /custom/path    # custom output root
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
C_NOSDN = "#2a78d6"   # blue  — No-SDN series
C_SDN   = "#1baf7a"   # green — SDN series
C_HO    = "#e89c00"   # orange — handover lines
C_HIT   = "#0ca30c"   # green — HIT status
C_MISS  = "#e34948"   # red   — MISS status
C_UNK   = "#888888"   # gray  — UNKNOWN status

# No-SDN dots: HIT=blue (series color), MISS/UNKNOWN = status color
NOSDN_DOT = {'HIT': C_NOSDN, 'MISS': C_MISS, 'UNKNOWN': C_UNK}
# SDN dots:   HIT=green (status), MISS/UNKNOWN = status color
SDN_DOT   = {'HIT': C_HIT,   'MISS': C_MISS, 'UNKNOWN': C_UNK}

CV_MAP = {'HIT': 1, 'MISS': 0, 'UNKNOWN': 0.5}

# AP zone background colors + label colors
AP_BAND = {
    'ap1': ('#2a78d6', 0.08),   # blue
    'ap2': ('#1baf7a', 0.08),   # green
    'ap3': ('#eda100', 0.09),   # yellow
    'ap4': ('#e34948', 0.08),   # red
}

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

def ap_spans(tn, ap_n, tmax):
    """Return list of (t_start, t_end, ap_name) for each AP zone."""
    spans = []
    i = 0
    while i < len(tn):
        ap = ap_n[i]; j = i
        while j < len(tn) and ap_n[j] == ap:
            j += 1
        t_end = tn[j] if j < len(tn) else tmax
        spans.append((tn[i], t_end, ap))
        i = j
    return spans

def add_ap_bands(ax, tn, ap_n, tmax, ylim):
    """Draw AP zone background bands + centered AP label at bottom."""
    for t0, t1, ap in ap_spans(tn, ap_n, tmax):
        color, alpha = AP_BAND.get(ap, ('#aaaaaa', 0.07))
        ax.axvspan(t0, t1, color=color, alpha=alpha, zorder=0, linewidth=0)
        ax.text((t0 + t1) / 2, ylim[0] + (ylim[1] - ylim[0]) * 0.03,
                ap.upper(), ha='center', va='bottom',
                fontsize=7, color=color, alpha=0.85, zorder=1)

def add_handover_lines(ax, trans, ylim):
    for t_ho, lbl in trans:
        ax.axvline(t_ho, color=C_HO, lw=1.2, ls='--', alpha=0.75, zorder=3)
        ax.text(t_ho + 0.3, ylim[1] * 0.97, lbl, rotation=90,
                va='top', ha='left', fontsize=7, color=C_HO, alpha=0.9)

# ── Style ──────────────────────────────────────────────────────────────────
plt.rcParams.update({
    'font.family':        'DejaVu Sans',
    'axes.facecolor':     '#f4f4f4',
    'figure.facecolor':   'white',
    'axes.grid':          True,
    'grid.color':         'white',
    'grid.linewidth':     1.0,
    'axes.spines.top':    False,
    'axes.spines.right':  False,
    'axes.edgecolor':     '#cccccc',
    'xtick.color':        '#555555',
    'ytick.color':        '#555555',
    'axes.labelcolor':    '#333333',
})

# ── Main plot function ─────────────────────────────────────────────────────
def make_plot(sit, spd, out_root):
    path_n = (f"{BASE}/no_sdn/sit{sit}/speed{spd}/"
              f"cdn_baseline_sit{sit}_spd{spd}_r1/"
              f"cdn_baseline_sit{sit}_spd{spd}_r1.csv")
    path_s = (f"{BASE}/sdn/sit{sit}/speed{spd}/"
              f"cdn_baseline_sdn_sit{sit}_spd{spd}_r1/"
              f"cdn_baseline_sdn_sit{sit}_spd{spd}_r1.csv")

    rn = load_csv(path_n)
    rs = load_csv(path_s)

    tn     = col(rn, 't');           ts     = col(rs, 't')
    qoe_n  = col(rn, 'qoe');         qoe_s  = col(rs, 'qoe')
    lat_n  = col(rn, 'latency_s');   lat_s  = col(rs, 'latency_s')
    rssi_n = col(rn, 'rssi');        rssi_s = col(rs, 'rssi')
    loss_n = col(rn, 'loss_pct');    loss_s = col(rs, 'loss_pct')
    ap_n   = col(rn, 'ap', str)
    cache_n = col(rn, 'cache', str); cache_s = col(rs, 'cache', str)
    tmax   = max(max(tn), max(ts))
    trans  = ap_transitions(tn, ap_n)

    fig, axes = plt.subplots(5, 1, figsize=(13, 14), facecolor='white')
    fig.subplots_adjust(hspace=0.45, top=0.94)

    sit_label = "Popular Content" if sit == 1 else "Unpopular Content"
    fig.suptitle(
        f"No-SDN vs SDN — Situation {sit} ({sit_label}),  Speed {spd} km/h",
        fontsize=13, fontweight='bold', color='#1a1a1a', y=0.97
    )

    # ── 1. QoE ──────────────────────────────────────────────────────────
    ax = axes[0]
    ax.plot(tn, qoe_n, color=C_NOSDN, lw=1.8, label='No-SDN', zorder=4)
    ax.fill_between(tn, qoe_n, alpha=0.12, color=C_NOSDN)
    ax.plot(ts, qoe_s, color=C_SDN,   lw=1.8, label='SDN',    zorder=4)
    ax.fill_between(ts, qoe_s, alpha=0.12, color=C_SDN)
    ax.set_ylim(0, 5.5); ax.set_xlim(0, tmax)
    ax.set_ylabel('QoE (0–5)', fontsize=10)
    ax.set_title('Quality of Experience (QoE)', fontsize=10, fontweight='semibold', pad=4)
    add_ap_bands(ax, tn, ap_n, tmax, (0, 5.5))
    add_handover_lines(ax, trans, (0, 5.5))
    ax.legend(loc='upper right', fontsize=9, framealpha=0.85)

    # ── 2. Latency ───────────────────────────────────────────────────────
    ax = axes[1]
    ax.plot(tn, lat_n, color=C_NOSDN, lw=1.8, label='No-SDN', zorder=4)
    ax.fill_between(tn, lat_n, alpha=0.12, color=C_NOSDN)
    ax.plot(ts, lat_s, color=C_SDN,   lw=1.8, label='SDN',    zorder=4)
    ax.fill_between(ts, lat_s, alpha=0.12, color=C_SDN)
    ax.set_ylim(0, 3.5); ax.set_xlim(0, tmax)
    ax.set_ylabel('Latency (s)', fontsize=10)
    ax.set_title('CDN Latency over Time', fontsize=10, fontweight='semibold', pad=4)
    add_ap_bands(ax, tn, ap_n, tmax, (0, 3.5))
    add_handover_lines(ax, trans, (0, 3.5))
    ax.legend(loc='upper right', fontsize=9, framealpha=0.85)

    # ── 3. RSSI ──────────────────────────────────────────────────────────
    ax = axes[2]
    ax.plot(tn, rssi_n, color=C_NOSDN, lw=1.6, zorder=3, alpha=0.6)
    ax.plot(ts, rssi_s, color=C_SDN,   lw=1.6, zorder=3, alpha=0.6)
    ax.scatter(tn, rssi_n, s=20, color=C_NOSDN, zorder=5, marker='o',
               edgecolors='none', label='No-SDN')
    ax.scatter(ts, rssi_s, s=20, color=C_SDN,   zorder=5, marker='s',
               edgecolors='none', label='SDN')
    ax.set_ylim(-75, -22); ax.set_xlim(0, tmax)
    ax.set_ylabel('RSSI (dBm)', fontsize=10)
    ax.set_title('Signal Strength (RSSI)', fontsize=10, fontweight='semibold', pad=4)
    add_ap_bands(ax, tn, ap_n, tmax, (-75, -22))
    add_handover_lines(ax, trans, (-75, -22))
    ax.legend(loc='upper right', fontsize=9, framealpha=0.85)

    # ── 4. Packet Loss ───────────────────────────────────────────────────
    ax = axes[3]
    ax.plot(tn, loss_n, color=C_NOSDN, lw=1.8, label='No-SDN', zorder=4)
    ax.fill_between(tn, loss_n, alpha=0.15, color=C_NOSDN)
    ax.plot(ts, loss_s, color=C_SDN,   lw=1.8, label='SDN',    zorder=4)
    ax.fill_between(ts, loss_s, alpha=0.15, color=C_SDN)
    loss_max = max(max(loss_n + loss_s) * 1.2, 5)
    ax.set_ylim(0, loss_max); ax.set_xlim(0, tmax)
    ax.set_ylabel('Loss (%)', fontsize=10)
    ax.set_title('Packet Loss over Time', fontsize=10, fontweight='semibold', pad=4)
    add_ap_bands(ax, tn, ap_n, tmax, (0, loss_max))
    add_handover_lines(ax, trans, (0, loss_max))
    ax.legend(loc='upper right', fontsize=9, framealpha=0.85)

    # ── 5. Cache Hit / Miss ───────────────────────────────────────────────
    ax = axes[4]
    cv_n = [CV_MAP.get(c, 0.5) for c in cache_n]
    cv_s = [CV_MAP.get(c, 0.5) for c in cache_s]

    # faint step lines
    ax.step(tn, cv_n, color=C_NOSDN, lw=1.0, where='post', alpha=0.35, zorder=2)
    ax.step(ts, cv_s, color=C_SDN,   lw=1.0, where='post', alpha=0.35, zorder=2)

    # No-SDN circles: HIT=blue, MISS=red, UNKNOWN=gray
    for t2, v2, c2 in zip(tn, cv_n, cache_n):
        ax.scatter(t2, v2 - 0.07, s=34, marker='o',
                   color=NOSDN_DOT[c2], edgecolors='none', zorder=5)

    # SDN squares: HIT=green, MISS=red, UNKNOWN=gray
    for t2, v2, c2 in zip(ts, cv_s, cache_s):
        ax.scatter(t2, v2 + 0.07, s=34, marker='s',
                   color=SDN_DOT[c2], edgecolors='none', zorder=5)

    ax.set_ylim(-0.4, 1.4); ax.set_xlim(0, tmax)
    ax.set_yticks([0, 0.5, 1])
    ax.set_yticklabels(['MISS', 'UNKNOWN', 'HIT'], fontsize=9)
    ax.set_ylabel('Cache Status', fontsize=10)
    ax.set_xlabel('Time (s)', fontsize=10)
    ax.set_title('Cache Hit / Miss over Time', fontsize=10, fontweight='semibold', pad=4)
    add_ap_bands(ax, tn, ap_n, tmax, (-0.4, 1.4))
    add_handover_lines(ax, trans, (-0.4, 1.4))

    leg = [
        mlines.Line2D([],[],marker='o',ls='',color=C_NOSDN,
                      markerfacecolor=C_NOSDN,markersize=5,label='No-SDN HIT'),
        mlines.Line2D([],[],marker='s',ls='',color=C_SDN,
                      markerfacecolor=C_HIT,  markersize=5,label='SDN HIT'),
        mpatches.Patch(color=C_MISS, label='MISS'),
        mpatches.Patch(color=C_UNK,  label='UNK'),
    ]
    ax.legend(handles=leg, loc='upper right', fontsize=6.5,
              handlelength=0.7, handletextpad=0.3, borderpad=0.35,
              labelspacing=0.15, framealpha=0.75, ncol=2,
              markerscale=0.7)

    # ── Save ─────────────────────────────────────────────────────────────
    fname = f"comparison_sit{sit}_spd{spd}.png"
    out_dir = os.path.join(out_root, "comparison", f"sit{sit}")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, fname)
    fig.savefig(out, dpi=150, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    print(f"  saved → {out}")
    plt.close(fig)


# ── CLI ────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Generate comparison plots')
    parser.add_argument('--sit',   type=int, nargs='+', default=[1, 2])
    parser.add_argument('--speed', type=int, nargs='+', default=[20, 25, 30])
    parser.add_argument('--out',   type=str, default=BASE,
                        help='Output root directory (default: results/cdn_baseline)')
    args = parser.parse_args()

    for sit in args.sit:
        for spd in args.speed:
            print(f"\n[sit{sit} spd{spd}]")
            make_plot(sit, spd, args.out)

    print("\nDone.")
