#!/usr/bin/env python3
"""
plot_comparison.py — CDN (SDN) run plots
Outputs: results/cdn_baseline/plots/sit{N}/cdn_sdn_sit{N}_spd{S}.png

Single-series view of the CDN-with-Ryu-SDN arm only (the No-SDN baseline is
no longer plotted here — this script used to compare No-SDN vs SDN, but that
comparison isn't the one that matters: see dash_cdn_comparison.py for the
DASH-vs-CDN comparison that is).

Usage:
    python3 plot_comparison.py                        # all sit x speed
    python3 plot_comparison.py --sit 1 --speed 20    # specific combo
    python3 plot_comparison.py --out /custom/path    # custom output root
"""

import csv, os, sys, argparse
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.lines as mlines
import matplotlib.patches as mpatches

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import baseline_model as M

# ── Paths ──────────────────────────────────────────────────────────────────
# Results live under CDN_baseline/results/ (self-contained, same convention
# as CDN_SIT1/result_multi_car/ and CDN_SIT2/results_hightspeed/) rather
# than the shared top-level results/ tree.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT    = os.path.dirname(SCRIPT_DIR)
BASE       = os.path.join(SCRIPT_DIR, "results")

# ── Colors ─────────────────────────────────────────────────────────────────
C_SDN   = "#1baf7a"   # green — SDN series
C_HO    = "#e89c00"   # orange — handover lines
C_HIT   = "#0ca30c"   # green — HIT status
C_MISS  = "#e34948"   # red   — MISS status
C_UNK   = "#888888"   # gray  — UNKNOWN status

# 'LOSS' replaces the old 'UNKNOWN' tier (cdn_baseline_topo[_sdn].py's
# outage tracking) -- cache HIT/MISS is strictly an edge-content question; a
# request that got no answer at all (outage or a timed-out probe) is a
# connection LOSS, not a third cache state. 'UNKNOWN' kept as a fallback key
# too for any older CSV that still has literal 'UNKNOWN' rows.
SDN_DOT = {'HIT': C_HIT, 'MISS': C_MISS, 'LOSS': C_UNK, 'UNKNOWN': C_UNK}
CV_MAP  = {'HIT': 1, 'MISS': 0, 'LOSS': 0.5, 'UNKNOWN': 0.5}

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

def ap_transitions(x, ap):
    return [
        (x[i], f"{ap[i-1].upper()}→{ap[i].upper()}")
        for i in range(1, len(ap)) if ap[i] != ap[i-1]
    ]

def ap_spans(x, ap, xmax):
    """Return list of (x_start, x_end, ap_name) for each AP zone."""
    spans = []
    i = 0
    while i < len(x):
        a = ap[i]; j = i
        while j < len(x) and ap[j] == a:
            j += 1
        x_end = x[j] if j < len(x) else xmax
        spans.append((x[i], x_end, a))
        i = j
    return spans

def add_ap_bands(ax, x, ap, xmax, ylim):
    """Draw AP zone background bands + centered AP label at bottom."""
    for x0, x1, a in ap_spans(x, ap, xmax):
        color, alpha = AP_BAND.get(a, ('#aaaaaa', 0.07))
        ax.axvspan(x0, x1, color=color, alpha=alpha, zorder=0, linewidth=0)
        ax.text((x0 + x1) / 2, ylim[0] + (ylim[1] - ylim[0]) * 0.03,
                a.upper(), ha='center', va='bottom',
                fontsize=7, color=color, alpha=0.85, zorder=1)

def add_handover_lines(ax, trans, ylim, x_offset=10):
    for x_ho, lbl in trans:
        ax.axvline(x_ho, color=C_HO, lw=1.2, ls='--', alpha=0.75, zorder=3)
        ax.text(x_ho + x_offset, ylim[1] * 0.97, lbl, rotation=90,
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
    path = (f"{BASE}/sdn/sit{sit}/speed{spd}/"
            f"cdn_baseline_sdn_sit{sit}_spd{spd}_r1/"
            f"cdn_baseline_sdn_sit{sit}_spd{spd}_r1.csv")

    # Default --sit/--speed sweep every combo (sit 1/2 x speed 20/25/30) --
    # skip whichever ones haven't actually been run yet instead of crashing
    # the whole sweep on the first missing one.
    if not os.path.isfile(path):
        print(f"  [WARN] no run found for sit{sit} speed{spd} -- skipping "
              f"({path})")
        return

    rows = load_csv(path)

    x     = col(rows, 'x')
    qoe   = M.compute_cdn_qoe(rows)   # post-hoc — CSV no longer bakes this in
    lat   = col(rows, 'latency_s')
    rssi  = col(rows, 'rssi')
    bw    = col(rows, 'bw_mbps')
    loss  = col(rows, 'loss_pct')
    ap    = col(rows, 'ap', str)
    cache = col(rows, 'cache', str)
    xmin, xmax = min(x), max(x)
    trans = ap_transitions(x, ap)

    fig, axes = plt.subplots(6, 1, figsize=(13, 16.5), facecolor='white')
    fig.subplots_adjust(hspace=0.5, top=0.95)

    sit_label = "Popular Content" if sit == 1 else "Unpopular Content"
    fig.suptitle(
        f"CDN (SDN) — Situation {sit} ({sit_label}),  Speed {spd} km/h",
        fontsize=13, fontweight='bold', color='#1a1a1a', y=0.97
    )

    # ── 1. QoE ──────────────────────────────────────────────────────────
    # Net QoE = Yin et al.'s sum(q(R_k) - mu*|switch| - T_k) across every
    # sample in the run -- one aggregate number for the whole run, not a
    # per-position value, so it's reported as an annotation rather than
    # plotted as its own line (a running cumulative sum would just be a
    # near-straight climbing curve here, since every term is positive --
    # not informative to look at).
    net_qoe = sum(qoe)
    ax = axes[0]
    ax.plot(x, qoe, color=C_SDN, lw=1.8, zorder=4)
    ax.fill_between(x, qoe, alpha=0.15, color=C_SDN)
    ax.set_xlim(xmin, xmax)
    ax.set_xlabel('Position (m)', fontsize=10)
    ax.set_ylabel('QoE (score)', fontsize=10)
    ax.set_title('Quality of Experience (QoE)', fontsize=10, fontweight='semibold', pad=4)
    ax.text(0.99, 0.06, f'Net QoE = {net_qoe:.1f}  ({len(qoe)} samples, avg {net_qoe/len(qoe):.3f})',
            transform=ax.transAxes, ha='right', va='bottom', fontsize=8.5,
            color='#1a1a1a', fontweight='semibold',
            bbox=dict(boxstyle='round,pad=0.3', facecolor='white', edgecolor='#cccccc', alpha=0.85))
    add_ap_bands(ax, x, ap, xmax, ax.get_ylim())
    add_handover_lines(ax, trans, ax.get_ylim())

    # ── 2. Latency ───────────────────────────────────────────────────────
    ax = axes[1]
    ax.plot(x, lat, color=C_SDN, lw=1.8, zorder=4)
    ax.fill_between(x, lat, alpha=0.12, color=C_SDN)
    ax.set_ylim(0, 3.5); ax.set_xlim(xmin, xmax)
    ax.set_xlabel('Position (m)', fontsize=10)
    ax.set_ylabel('Latency (s)', fontsize=10)
    ax.set_title('CDN Latency over Position', fontsize=10, fontweight='semibold', pad=4)
    add_ap_bands(ax, x, ap, xmax, (0, 3.5))
    add_handover_lines(ax, trans, (0, 3.5))

    # ── 3. RSSI ──────────────────────────────────────────────────────────
    ax = axes[2]
    ax.plot(x, rssi, color=C_SDN, lw=1.6, zorder=3, alpha=0.6)
    ax.scatter(x, rssi, s=20, color=C_SDN, zorder=5, marker='o', edgecolors='none')
    # Dynamic range, not a hardcoded band: with live RSSI (see parse_rssi()
    # in cdn_baseline_topo_sdn.py) the real signal at AP2-4 can run well
    # below a fixed -75 dBm floor -- a fixed ylim silently clips those
    # points off the chart instead of erroring, so it's an easy way to lose
    # data without noticing.
    rssi_ylim = (min(rssi) - 5, max(rssi) + 5)
    ax.set_ylim(*rssi_ylim); ax.set_xlim(xmin, xmax)
    ax.set_xlabel('Position (m)', fontsize=10)
    ax.set_ylabel('RSSI (dBm)', fontsize=10)
    ax.set_title('Signal Strength (RSSI)', fontsize=10, fontweight='semibold', pad=4)
    add_ap_bands(ax, x, ap, xmax, rssi_ylim)
    add_handover_lines(ax, trans, rssi_ylim)

    # ── 4. Imposed Bandwidth (RSSI -> Mbps via the step2h mapper) ─────────
    # Deliberately a step plot, not a smooth line -- unlike RSSI (a
    # continuous physical quantity), bw_mbps is discretized into tiers by
    # Step2HysteresisMapper (see baseline_model.py), so this panel should
    # visibly look like a staircase, not a curve.
    ax = axes[3]
    bw_max = max(max(bw) * 1.15, 1)
    ax.step(x, bw, color=C_SDN, lw=1.8, where='post', zorder=4)
    ax.fill_between(x, bw, step='post', alpha=0.15, color=C_SDN)
    ax.set_ylim(0, bw_max); ax.set_xlim(xmin, xmax)
    ax.set_xlabel('Position (m)', fontsize=10)
    ax.set_ylabel('Bandwidth (Mbps)', fontsize=10)
    ax.set_title('Imposed Bandwidth (step2h)', fontsize=10, fontweight='semibold', pad=4)
    add_ap_bands(ax, x, ap, xmax, (0, bw_max))
    add_handover_lines(ax, trans, (0, bw_max))

    # ── 5. Packet Loss ───────────────────────────────────────────────────
    ax = axes[4]
    ax.plot(x, loss, color=C_SDN, lw=1.8, zorder=4)
    ax.fill_between(x, loss, alpha=0.15, color=C_SDN)
    loss_max = max(max(loss) * 1.2, 5)
    ax.set_ylim(0, loss_max); ax.set_xlim(xmin, xmax)
    ax.set_xlabel('Position (m)', fontsize=10)
    ax.set_ylabel('Loss (%)', fontsize=10)
    ax.set_title('Packet Loss over Position', fontsize=10, fontweight='semibold', pad=4)
    add_ap_bands(ax, x, ap, xmax, (0, loss_max))
    add_handover_lines(ax, trans, (0, loss_max))

    # ── 6. Cache Hit / Miss ───────────────────────────────────────────────
    ax = axes[5]
    cv = [CV_MAP.get(c, 0.5) for c in cache]

    ax.step(x, cv, color=C_SDN, lw=1.0, where='post', alpha=0.35, zorder=2)
    for x2, v2, c2 in zip(x, cv, cache):
        ax.scatter(x2, v2, s=34, marker='s',
                   color=SDN_DOT[c2], edgecolors='none', zorder=5)

    ax.set_ylim(-0.4, 1.4); ax.set_xlim(xmin, xmax)
    ax.set_yticks([0, 0.5, 1])
    ax.set_yticklabels(['MISS', 'LOSS', 'HIT'], fontsize=9)
    ax.set_ylabel('Cache Status', fontsize=10)
    ax.set_xlabel('Position (m)', fontsize=10)
    ax.set_title('Cache Hit / Miss over Position', fontsize=10, fontweight='semibold', pad=4)
    add_ap_bands(ax, x, ap, xmax, (-0.4, 1.4))
    add_handover_lines(ax, trans, (-0.4, 1.4))

    leg = [
        mlines.Line2D([],[],marker='s',ls='',color=C_HIT,
                      markerfacecolor=C_HIT, markersize=5, label='HIT'),
        mpatches.Patch(color=C_MISS, label='MISS'),
        mpatches.Patch(color=C_UNK,  label='UNK'),
    ]
    ax.legend(handles=leg, loc='upper right', fontsize=7,
              handlelength=0.7, handletextpad=0.3, borderpad=0.35,
              labelspacing=0.15, framealpha=0.75, ncol=3,
              markerscale=0.8)

    # ── Save ─────────────────────────────────────────────────────────────
    fname = f"cdn_sdn_sit{sit}_spd{spd}.png"
    out_dir = os.path.join(out_root, "plots", f"sit{sit}")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, fname)
    fig.savefig(out, dpi=150, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    print(f"  saved → {out}")
    print(f"  Net QoE = {net_qoe:.2f}  ({len(qoe)} samples, avg {net_qoe/len(qoe):.3f})")
    plt.close(fig)


# ── CLI ────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Generate CDN (SDN) run plots')
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
