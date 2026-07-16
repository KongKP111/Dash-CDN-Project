#!/usr/bin/env python3
"""
plot_smoke_run_v2.py -- detailed, paper-ready per-vehicle figure for one
Situation 1 smoke-test run. No sudo needed; reads the already-saved
per-vehicle CSVs (segments.csv, network.csv) and summary.json.

Layout: one column per vehicle, four rows per column:
  1. RSSI (dBm) -- the raw wireless signal driving everything else, with
     RSU zone bands and vertical handover markers
  2. Chosen bitrate (quality) vs. hybrid-model allocated bandwidth
     (bw_mbps), with rendition reference lines (360p/720p/1080p) and
     stall markers
  3. ICMP packet loss (%)
  4. Cumulative outage (seconds) -- running total of time spent with any
     measured loss > 0
Each column's title also shows that vehicle's final rebuffer_ratio_pct
(from summary.json). Column set covers the comparison metrics agreed with
the SDN+CDN teammate: bw_mbps, rssi, loss, cum_outage_s, handover, quality
(rebuffer_ratio_pct shown once per column, not as a time series, since
it's a single session-level aggregate).

Time axis is PLATOON-RELATIVE (tau = t - i*LAG_S, i = 0-based vehicle
index, LAG_S = SPACING_M/SPEED_MPS): every vehicle in the platoon follows
the identical route offset by a fixed, constant lag, so at equal tau every
vehicle is at (almost exactly) the same physical position on the loop --
raw wall-clock time would make each column's RSU-zone bands a different
width/position purely from that lag plus incidental handover-retry timing
noise, which looks "unequal" for reasons that have nothing to do with the
density effect being measured. Columns therefore use independent x-limits
(sharex='col', not True) since each vehicle's own tau range is tightly
fit rather than padded to the union of all vehicles' ranges.

Loss axis is a fixed constant (not auto-scaled per run) so the 3/5/7-car
figures are visually comparable to each other, not just internally.

Run: python3 plot_smoke_run_v2.py <run_id> [--dir DIR] [--cars N]
"""
import os
import csv
import json
import argparse
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.lines import Line2D

# Same platoon constants as campus_config.py (SPACING_M, SPEED_KMH) -- not
# imported from there directly since that module pulls in mn_wifi/mininet
# (via dash_topology.py) purely for the RSU-zone helper, and this plotting
# tool should stay runnable with nothing but matplotlib installed.
SPACING_M   = 10.0
SPEED_KMH   = 20.0
LAG_S       = SPACING_M / (SPEED_KMH / 3.6)   # seconds between consecutive vehicles
LOSS_YLIM_DEFAULT = 75.0   # fixed across all car-count figures (real global max seen: 66.67%)
OUTAGE_YLIM_DEFAULT = 40.0  # fixed across all car-count figures (real global max seen: 33.15s, 7-car)

# ---- validated palette (references/palette.md) ----------------------------
INK_PRIMARY   = '#0b0b0b'
INK_SECONDARY = '#52514e'
INK_MUTED     = '#898781'
GRID          = '#e1e0d9'
BASELINE      = '#c3c2b7'
SURFACE       = '#fcfcfb'

SERIES_BITRATE  = '#2a78d6'   # categorical slot 1 (blue)  -- chosen bitrate
SERIES_ALLOC    = '#eb6834'   # categorical slot 8 (orange) -- allocated ceiling
STATUS_WARNING  = '#fab219'   # packet loss
STATUS_CRITICAL = '#d03b3b'   # stall
STATUS_OUTAGE   = '#ec835a'   # cumulative outage (status "serious")
HANDOVER_LINE   = '#9085e9'   # categorical slot 5 (violet) -- handover marker, distinct from RSU tints

RSU_TINTS = {   # light background tints, distinct from the two data-series hues
    'rsu1': '#1baf7a',   # aqua
    'rsu2': '#eda100',   # yellow
    'rsu3': '#008300',   # green
    'rsu4': '#e87ba4',   # magenta
}
RENDITIONS = [(1.0, '360p'), (2.5, '720p'), (5.0, '1080p')]


def load(path):
    return list(csv.DictReader(open(path))) if os.path.exists(path) else []


def load_json(path):
    return json.load(open(path)) if os.path.exists(path) else {}


def rsu_spans(tau_list, rsu_list):
    spans, prev, start = [], None, None
    for tau, rsu in zip(tau_list, rsu_list):
        if rsu != prev:
            if prev is not None:
                spans.append((prev, start, tau))
            prev, start = rsu, tau
    if prev is not None:
        spans.append((prev, start, tau_list[-1]))
    return spans


def handover_times(spans):
    """Handover instants = the start of every span after the first (the
    first span's start is just the recording start, not a real handover)."""
    return [t0 for _, t0, _ in spans[1:]]


def cumulative_outage(tau_list, loss_list):
    """Running total (seconds) of time spent in any sample with measured
    loss > 0. Each sample's dt is charged to outage if THAT sample showed
    loss (i.e. the interval since the previous sample is treated as lossy)."""
    cum, out, prev_tau = [], 0.0, None
    for tau, loss in zip(tau_list, loss_list):
        if prev_tau is not None:
            dt = tau - prev_tau
            if loss > 0:
                out += dt
        cum.append(out)
        prev_tau = tau
    return cum


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('run_id')
    ap.add_argument('--dir', default=None,
                     help='where to READ the per-vehicle CSVs from '
                          '(default: results_raw/<run_id>)')
    ap.add_argument('--out-dir', default='graphs',
                     help='where to WRITE the PNG (default: graphs/, kept '
                          'separate from --dir since results_raw/ is '
                          'root-owned from the sudo mininet run and not '
                          'writable by a normal user)')
    ap.add_argument('--cars', type=int, default=3)
    ap.add_argument('--loss-ylim', type=float, default=LOSS_YLIM_DEFAULT,
                     help='fixed ICMP-loss axis ceiling (%%), same across '
                          'all car-count figures for direct comparability '
                          '(default: %(default)s)')
    ap.add_argument('--outage-ylim', type=float, default=OUTAGE_YLIM_DEFAULT,
                     help='fixed cumulative-outage axis ceiling (seconds), '
                          'same across all car-count figures (default: %(default)s)')
    args = ap.parse_args()
    d = args.dir or os.path.join('results_raw', args.run_id)
    os.makedirs(args.out_dir, exist_ok=True)

    fig, axes = plt.subplots(4, args.cars, figsize=(5.6 * args.cars, 12.0),
                              sharex='col', facecolor=SURFACE,
                              gridspec_kw={'height_ratios': [1.0, 1.5, 0.7, 0.7]})
    if args.cars == 1:
        axes = axes.reshape(4, 1)

    all_seg, all_net, all_summary = {}, {}, {}
    for i in range(args.cars):
        car = f'car{i + 1}'
        all_seg[car] = load(os.path.join(d, f'{args.run_id}_{car}_segments.csv'))
        all_net[car] = load(os.path.join(d, f'{args.run_id}_{car}_network.csv'))
        all_summary[car] = load_json(os.path.join(d, f'{args.run_id}_{car}_summary.json'))
    loss_ylim = args.loss_ylim

    outage_ylim = args.outage_ylim
    for i in range(args.cars):
        car = f'car{i + 1}'
        seg, net, summ = all_seg[car], all_net[car], all_summary[car]
        ax_rssi, ax_bw, ax_loss, ax_out = axes[0, i], axes[1, i], axes[2, i], axes[3, i]
        shift = i * LAG_S   # platoon-relative time: tau = t - i*LAG_S

        for ax in (ax_rssi, ax_bw, ax_loss, ax_out):
            ax.set_facecolor(SURFACE)
            for spine in ('top', 'right'):
                ax.spines[spine].set_visible(False)
            for spine in ('left', 'bottom'):
                ax.spines[spine].set_color(BASELINE)
            ax.tick_params(colors=INK_MUTED, labelsize=8)
            ax.grid(True, color=GRID, linewidth=0.8, axis='y')

        # ---- RSU zone bands (drawn on the RSSI row, spanning into title) --
        net_tau = [float(r['t']) - shift for r in net] if net else []
        spans = rsu_spans(net_tau, [r['rsu'] for r in net]) if net else []
        for rsu, t0, t1 in spans:
            ax_rssi.axvspan(t0, t1, color=RSU_TINTS.get(rsu, '#ddd'), alpha=0.16, lw=0)
            mid = (t0 + t1) / 2
            ax_rssi.text(mid, 1.06, rsu.upper(), transform=ax_rssi.get_xaxis_transform(),
                         ha='center', va='bottom', fontsize=7.5, fontweight='bold',
                         color=INK_SECONDARY)

        # ---- handover markers: one vertical line per RSU transition, drawn
        # through all 4 rows of this column -----------------------------
        ho_times = handover_times(spans)
        for ax in (ax_rssi, ax_bw, ax_loss, ax_out):
            for ho_t in ho_times:
                ax.axvline(ho_t, color=HANDOVER_LINE, linewidth=1.0,
                           linestyle=':', alpha=0.7, zorder=1)

        # ---- Row 1: RSSI ---------------------------------------------------
        if net:
            rssi = [float(r['rssi_dbm']) for r in net]
            ax_rssi.plot(net_tau, rssi, color=INK_PRIMARY, linewidth=1.6)
            ax_rssi.set_ylim(-80, -35)
            ax_rssi.set_xlim(net_tau[0], net_tau[-1])
        if i == 0:
            ax_rssi.set_ylabel('rssi (dBm)', fontsize=9, color=INK_SECONDARY)
        rebuf = summ.get('rebuffering_ratio')
        title = car if rebuf is None else f'{car}   (rebuffer {rebuf * 100:.2f}%)'
        ax_rssi.set_title(title, fontsize=12.5, fontweight='bold', color=INK_PRIMARY, pad=22)

        # ---- Row 2: bitrate (quality) vs allocated bw_mbps ------------------
        for val, label in RENDITIONS:
            ax_bw.axhline(val, color=GRID, linewidth=1.0, zorder=0)
        if seg:
            seg_tau = [float(r['timestamp']) - shift for r in seg]
            br = [float(r['bitrate_kbps']) / 1000.0 for r in seg]
            ax_bw.step(seg_tau, br, where='post', color=SERIES_BITRATE, linewidth=2.2, zorder=3)
            stall_tau = [seg_tau[k] for k, r in enumerate(seg) if float(r['stall_duration_s']) > 0]
            if stall_tau:
                ax_bw.scatter(stall_tau, [0] * len(stall_tau), marker='v', s=70,
                              color=STATUS_CRITICAL, zorder=5, clip_on=False)
        if net:
            nt = [float(r['t']) - shift for r in net if r['allocated_bw_mbps']]
            nb = [float(r['allocated_bw_mbps']) for r in net if r['allocated_bw_mbps']]
            ax_bw.plot(nt, nb, color=SERIES_ALLOC, linestyle='--', linewidth=1.6,
                       alpha=0.9, zorder=2)
        ax_bw.set_ylim(0, 11)
        ax_bw.set_yticks([0, 1.0, 2.5, 5.0, 7.5, 10])
        if i == 0:
            ax_bw.set_ylabel('bw_mbps / quality', fontsize=9, color=INK_SECONDARY)
            ax_bw.set_yticklabels(['0', '1.0 (360p)', '2.5 (720p)', '5.0 (1080p)', '7.5', '10'])
        else:
            ax_bw.set_yticklabels([])

        # ---- Row 3: packet loss --------------------------------------------
        loss = [float(r['icmp_loss_pct']) for r in net] if net else []
        if net:
            ax_loss.fill_between(net_tau, loss, color=STATUS_WARNING, alpha=0.35, step='post')
            ax_loss.plot(net_tau, loss, color=STATUS_WARNING, linewidth=1.4, drawstyle='steps-post')
        ax_loss.set_ylim(0, loss_ylim)
        if i == 0:
            ax_loss.set_ylabel('loss (%)', fontsize=9, color=INK_SECONDARY)

        # ---- Row 4: cumulative outage (seconds) -----------------------------
        if net:
            cum_out = cumulative_outage(net_tau, loss)
            ax_out.fill_between(net_tau, cum_out, color=STATUS_OUTAGE, alpha=0.30, step='post')
            ax_out.plot(net_tau, cum_out, color=STATUS_OUTAGE, linewidth=1.6, drawstyle='steps-post')
            final_pct = (cum_out[-1] / net_tau[-1] * 100) if net_tau[-1] else 0.0
            ax_out.text(0.98, 0.88, f'{cum_out[-1]:.1f}s ({final_pct:.1f}%)',
                        transform=ax_out.transAxes, ha='right', va='top',
                        fontsize=7.5, color=STATUS_OUTAGE, fontweight='bold')
        ax_out.set_ylim(0, outage_ylim)
        if i == 0:
            ax_out.set_ylabel('cum_outage_s', fontsize=9, color=INK_SECONDARY)
        ax_out.set_xlabel('τ (s)', fontsize=9, color=INK_SECONDARY)

    # ---- shared legend -------------------------------------------------------
    legend_handles = [
        Line2D([0], [0], color=SERIES_BITRATE, lw=2.2, label='quality (chosen bitrate)'),
        Line2D([0], [0], color=SERIES_ALLOC, lw=1.6, ls='--', label='bw_mbps (allocated, step2h+contention)'),
        Line2D([0], [0], color=HANDOVER_LINE, lw=1.4, ls=':', label='handover'),
        Line2D([0], [0], color=STATUS_CRITICAL, lw=0, marker='v', markersize=8, label='stall event'),
        Line2D([0], [0], color=STATUS_WARNING, lw=1.4, label='loss (%)'),
        Line2D([0], [0], color=STATUS_OUTAGE, lw=1.6, label='cum_outage_s'),
    ] + [mpatches.Patch(facecolor=RSU_TINTS[r], alpha=0.35, label=r.upper()) for r in ['rsu1', 'rsu2', 'rsu3', 'rsu4']]

    fig.legend(handles=legend_handles, loc='lower center', ncol=5, frameon=False,
               fontsize=8.5, bbox_to_anchor=(0.5, -0.03),
               labelcolor=INK_SECONDARY)

    fig.suptitle(f'Situation 1 (Traffic Density) — {args.run_id} ({args.cars} vehicles)',
                 fontsize=15, fontweight='bold', color=INK_PRIMARY, y=1.01)
    fig.text(0.5, 0.975,
              'rssi, bw_mbps/quality, loss, cum_outage_s, handover — one column per vehicle. '
              f'τ = t − i·{LAG_S:.1f}s aligns every vehicle to the same point on the shared route '
              '(i = 0-based platoon position), so RSU-zone widths are directly comparable across columns.',
              fontsize=8.5, color=INK_SECONDARY, ha='center')

    plt.tight_layout(rect=[0.02, 0.06, 1, 0.95])
    out = os.path.join(args.out_dir, f'{args.run_id}_detailed_plot.png')
    plt.savefig(out, dpi=150, facecolor=SURFACE, bbox_inches='tight')
    print('Saved:', out)


if __name__ == '__main__':
    main()
