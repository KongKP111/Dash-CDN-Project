#!/usr/bin/env python3
"""
dash_cdn_comparison.py — DASH baseline vs CDN baseline comparison plot
X-axis: vehicle position x (m) — both share the same physical layout
Panels: QoE | RSSI | Bandwidth | Packet Loss

Usage:
    python3 dash_cdn_comparison.py                        # sit 1 & 2, speed 20
    python3 dash_cdn_comparison.py --sit 1 --speed 20 25 30
    python3 dash_cdn_comparison.py --out /custom/path
"""

import csv, os, sys, argparse
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.lines as mlines

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import baseline_model as M

# ── Paths ──────────────────────────────────────────────────────────────────
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
PROJECT     = os.path.dirname(SCRIPT_DIR)
CDN_BASE    = os.path.join(PROJECT, "results", "cdn_baseline")
DASH_BASE   = os.path.join(PROJECT, "results", "dash_baseline")  # or override via CLI

# ── Colors ─────────────────────────────────────────────────────────────────
C_DASH  = "#e67e22"   # orange — DASH series
C_CDN   = "#1baf7a"   # green  — CDN SDN series
C_HO    = "#e89c00"   # orange — handover lines

# Zone background colors (RSU/AP 1-4)
ZONE_BAND = {
    1: ('#2a78d6', 0.07),
    2: ('#1baf7a', 0.07),
    3: ('#eda100', 0.08),
    4: ('#e34948', 0.07),
}

RSU_X    = [0.0, 500.0, 1000.0, 1500.0]   # same as CDN AP positions
MIDPOINT = [
    (RSU_X[i] + RSU_X[i+1]) / 2 for i in range(len(RSU_X) - 1)
]

# QoE (Yin et al., SIGCOMM'15 linear model — see TEAMMATE_SETUP.md #5):
#   QoE = sum(q(R_k)) - mu * sum(|q(R_k+1) - q(R_k)|) - sum(T_k)
# q(R_k) = bitrate (Mbps) of the rendition, NOT the old toy 1-5 MOS scale.
# mu=1 (standard default). T_k = rebuffer seconds before segment k, taken
# directly from the 'stall' flag, scaled by this row's REAL elapsed time
# (see compute_dash_qoe() below) -- NOT a fixed SAMPLE_DT=1s per stalled
# row as this comment used to claim. This file actually pairs with
# dash-baseline/baseline_4rsu_topo.py's output (results/dash_baseline/,
# confirmed via that dir's own /tmp/dash_4rsu/results_4rsu/ fallback path
# below), whose real SAMPLE_DT is 0.5s (baseline_4rsu_model.py), not 1.0s
# (baseline_model.py) -- a fixed 1.0 overcounted every stalled row's
# rebuffer penalty by ~2x. And since that arm's run loop is now wall-clock
# driven (Option 2, ported alongside this fix), a tick's real duration
# isn't even a fixed 0.5s anymore -- it genuinely varies (handover
# struggles run long), so a real per-row dt is required either way, not
# just a smaller fixed constant.
_BITRATE_MBPS = {"360p": 1.0, "720p": 2.5, "1080p": 5.0}
_MU = 1.0

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

# ── Helpers ────────────────────────────────────────────────────────────────
def load_csv(path):
    with open(path) as f:
        return list(csv.DictReader(f))

def col(rows, key, cast=float):
    return [cast(r[key]) for r in rows]

def compute_dash_qoe(rows):
    """Per-row Yin et al. QoE term: q(R_k) - mu*|q(R_k)-q(R_k-1)| - T_k.
    Summing the returned list gives the run's total QoE from the formula;
    plotted as-is it's the per-sample QoE contribution over position/time.

    T_k = this row's real elapsed seconds (dt, from consecutive rows' own
    't' column) if 'stall' is set, else 0 -- NOT a fixed 1.0 regardless of
    dt (see the module-level comment above for why that was wrong for the
    data this function actually processes). First row has no previous
    timestamp to diff against, so its dt is 0.0 -- same minor, unavoidable
    edge case as baseline_model.py's compute_cdn_qoe()."""
    qoes, prev_bitrate, prev_t = [], None, None
    for r in rows:
        bitrate = _BITRATE_MBPS.get(r['quality'], 0.0)
        switch_penalty = (_MU * abs(bitrate - prev_bitrate)
                           if prev_bitrate is not None else 0.0)
        t = float(r['t'])
        dt = (t - prev_t) if prev_t is not None else 0.0
        rebuf_s = dt if int(r.get('stall', 0)) else 0.0
        qoes.append(bitrate - switch_penalty - rebuf_s)
        prev_bitrate = bitrate
        prev_t = t
    return qoes

def zone_spans(xmin, xmax):
    edges = [xmin] + MIDPOINT + [xmax]
    return [(edges[i], edges[i+1], i+1) for i in range(4)]

def add_zone_bands(ax, xmin, xmax, ylim):
    for x0, x1, zone in zone_spans(xmin, xmax):
        color, alpha = ZONE_BAND[zone]
        ax.axvspan(x0, x1, color=color, alpha=alpha, zorder=0, linewidth=0)
        ax.text((x0+x1)/2, ylim[0] + (ylim[1]-ylim[0])*0.03,
                'RSU%d/AP%d' % (zone, zone),
                ha='center', va='bottom', fontsize=7,
                color=color, alpha=0.85, zorder=1)

def add_handover_lines(ax, ho_xs, ylim):
    for hx in ho_xs:
        ax.axvline(hx, color=C_HO, lw=1.2, ls='--', alpha=0.75, zorder=3)
        ax.text(hx + 5, ylim[1]*0.97, 'HO', rotation=90,
                va='top', ha='left', fontsize=7, color=C_HO, alpha=0.9)

# ── Main plot ──────────────────────────────────────────────────────────────
def make_plot(sit, spd, dash_csv, cdn_csv, out_root):
    rd = load_csv(dash_csv)
    rc = load_csv(cdn_csv)

    # DASH series
    xd      = col(rd, 'x')
    rssi_d  = col(rd, 'rssi')
    bw_d    = col(rd, 'bw_mbps')
    loss_d  = col(rd, 'loss')
    qoe_d   = compute_dash_qoe(rd)
    ho_xd   = [float(r['x']) for r in rd if int(r.get('handover', 0))]

    # CDN series
    xc      = col(rc, 'x')
    rssi_c  = col(rc, 'rssi')
    bw_c    = col(rc, 'bw_mbps')
    loss_c  = col(rc, 'loss_pct')
    qoe_c   = M.compute_cdn_qoe(rc)
    ho_xc   = [float(r['x']) for r in rc if int(r.get('handover', 0))]

    xmin = min(min(xd), min(xc))
    xmax = max(max(xd), max(xc))
    ho_xs = sorted(set(ho_xd + ho_xc))

    fig, axes = plt.subplots(4, 1, figsize=(13, 12), facecolor='white')
    fig.subplots_adjust(hspace=0.45, top=0.94)

    sit_label = "Popular Content" if sit == 1 else "Unpopular Content"
    fig.suptitle(
        f"DASH vs CDN (SDN) — Situation {sit} ({sit_label}),  Speed {spd} km/h",
        fontsize=13, fontweight='bold', color='#1a1a1a', y=0.97
    )

    # ── 1. QoE ──────────────────────────────────────────────────────────
    ax = axes[0]
    ax.plot(xd, qoe_d, color=C_DASH, lw=1.8, label='DASH', zorder=4)
    ax.fill_between(xd, qoe_d, alpha=0.12, color=C_DASH)
    ax.plot(xc, qoe_c, color=C_CDN,  lw=1.8, label='CDN (SDN)',  zorder=4)
    ax.fill_between(xc, qoe_c, alpha=0.12, color=C_CDN)
    ax.set_ylim(0, 5.5); ax.set_xlim(xmin, xmax)
    ax.set_ylabel('QoE (0–5)', fontsize=10)
    ax.set_title('Quality of Experience (QoE)', fontsize=10,
                 fontweight='semibold', pad=4)
    add_zone_bands(ax, xmin, xmax, (0, 5.5))
    add_handover_lines(ax, ho_xs, (0, 5.5))
    ax.legend(loc='upper right', fontsize=9, framealpha=0.85)

    # ── 2. RSSI ──────────────────────────────────────────────────────────
    ax = axes[1]
    ax.plot(xd, rssi_d, color=C_DASH, lw=1.8, label='DASH', zorder=4)
    ax.fill_between(xd, rssi_d, alpha=0.12, color=C_DASH)
    ax.plot(xc, rssi_c, color=C_CDN,  lw=1.8, label='CDN (SDN)',  zorder=4)
    ax.fill_between(xc, rssi_c, alpha=0.12, color=C_CDN)
    ax.set_ylim(-85, -20); ax.set_xlim(xmin, xmax)
    ax.set_ylabel('RSSI (dBm)', fontsize=10)
    ax.set_title('Signal Strength (RSSI)', fontsize=10,
                 fontweight='semibold', pad=4)
    add_zone_bands(ax, xmin, xmax, (-85, -20))
    add_handover_lines(ax, ho_xs, (-85, -20))
    ax.legend(loc='upper right', fontsize=9, framealpha=0.85)

    # ── 3. Bandwidth ──────────────────────────────────────────────────────
    ax = axes[2]
    ax.plot(xd, bw_d, color=C_DASH, lw=1.8, label='DASH', zorder=4)
    ax.fill_between(xd, bw_d, alpha=0.12, color=C_DASH)
    ax.plot(xc, bw_c, color=C_CDN,  lw=1.8, label='CDN (SDN)',  zorder=4)
    ax.fill_between(xc, bw_c, alpha=0.12, color=C_CDN)
    ax.set_ylim(0, 11); ax.set_xlim(xmin, xmax)
    ax.set_ylabel('Bandwidth (Mbps)', fontsize=10)
    ax.set_title('Imposed Bandwidth', fontsize=10, fontweight='semibold', pad=4)
    add_zone_bands(ax, xmin, xmax, (0, 11))
    add_handover_lines(ax, ho_xs, (0, 11))
    ax.legend(loc='upper right', fontsize=9, framealpha=0.85)

    # ── 4. Packet Loss ────────────────────────────────────────────────────
    ax = axes[3]
    ax.plot(xd, loss_d, color=C_DASH, lw=1.8, label='DASH', zorder=4)
    ax.fill_between(xd, loss_d, alpha=0.15, color=C_DASH)
    ax.plot(xc, loss_c, color=C_CDN,  lw=1.8, label='CDN (SDN)',  zorder=4)
    ax.fill_between(xc, loss_c, alpha=0.15, color=C_CDN)
    loss_max = max(max(loss_d + loss_c) * 1.2, 5)
    ax.set_ylim(0, loss_max); ax.set_xlim(xmin, xmax)
    ax.set_ylabel('Loss (%)', fontsize=10)
    ax.set_xlabel('Vehicle Position x (m)', fontsize=10)
    ax.set_title('Packet Loss', fontsize=10, fontweight='semibold', pad=4)
    add_zone_bands(ax, xmin, xmax, (0, loss_max))
    add_handover_lines(ax, ho_xs, (0, loss_max))
    ax.legend(loc='upper right', fontsize=9, framealpha=0.85)

    # ── RSU/AP center markers on top panel ────────────────────────────────
    for i, rx in enumerate(RSU_X):
        axes[0].axvline(rx, color='#888888', lw=0.8, ls=':', alpha=0.6, zorder=2)
        axes[0].text(rx, 5.35, 'RSU%d\nAP%d' % (i+1, i+1),
                     ha='center', fontsize=6.5, color='#666666')

    # ── Save ──────────────────────────────────────────────────────────────
    fname   = f"dash_cdn_sit{sit}_spd{spd}.png"
    out_dir = os.path.join(out_root, "comparison", f"sit{sit}")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, fname)
    fig.savefig(out, dpi=150, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    print(f"  saved → {out}")
    plt.close(fig)


# ── CLI ────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='DASH vs CDN comparison plot')
    parser.add_argument('--sit',   type=int, nargs='+', default=[1, 2])
    parser.add_argument('--speed', type=int, nargs='+', default=[20, 25, 30])
    parser.add_argument('--round', type=int, default=1)
    parser.add_argument('--dash-dir', type=str, default=None,
                        help='Path to folder with run_XX.csv (default: results/dash_baseline)')
    parser.add_argument('--out', type=str, default=CDN_BASE)
    args = parser.parse_args()

    dash_dir = args.dash_dir or os.path.join(PROJECT, "results", "dash_baseline")

    for sit in args.sit:
        for spd in args.speed:
            print(f"\n[sit{sit} spd{spd}]")
            r = args.round

            # DASH CSV — try project results first, then /tmp extract
            dash_csv = os.path.join(dash_dir, f"run_{r:02d}.csv")
            if not os.path.exists(dash_csv):
                dash_csv = f"/tmp/dash_4rsu/results_4rsu/run_{r:02d}.csv"
            if not os.path.exists(dash_csv):
                print(f"  [SKIP] DASH CSV not found: {dash_csv}")
                continue

            # CDN CSV (SDN cooperative version)
            cdn_csv = (f"{CDN_BASE}/sdn/sit{sit}/speed{spd}/"
                       f"cdn_baseline_sdn_sit{sit}_spd{spd}_r{r}/"
                       f"cdn_baseline_sdn_sit{sit}_spd{spd}_r{r}.csv")
            if not os.path.exists(cdn_csv):
                print(f"  [SKIP] CDN CSV not found: {cdn_csv}")
                continue

            make_plot(sit, spd, dash_csv, cdn_csv, args.out)

    print("\nDone.")
