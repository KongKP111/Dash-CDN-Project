#!/usr/bin/env python3
"""
plot_situation1_overlay.py -- Situation 1 (Traffic Density) figure, styled
after the Situation 2 (Mobility Speed) template: ONE combined plot per
density case (3 / 5 / 7 cars), one row per metric, one colored line per
VEHICLE (in Situation 2's template a line is one speed condition; here a
line is one vehicle within that density case, since a density case is
inherently multi-vehicle).

Rows: Rendition/Quality, RSSI, Bandwidth (allocated, hybrid step2h +
contention), Packet Loss, Cumulative Outage, Handover Events (one track per
vehicle), Rebuffer Ratio (running cumulative stall/elapsed).

X-axis is arc-length POSITION along the shared loop route (metres), not
time: position = tau * SPEED_MPS where tau = t - i*LAG_S is the same
platoon-relative time used in plot_smoke_run_v2.py -- by construction of
the fixed-gap platoon model this is exact (every vehicle's arc-length
position at equal tau is identical), so it's a simpler and equally exact
way to get a position axis than re-deriving it from raw x,y.

Background RSU-zone bands use car1's (index 0) logged RSU sequence as the
shared visual reference for all rows except Handover Events, which shows
each vehicle's own real handover timing/positions (including any
handover-retry lag) on its own track.

No sudo needed; reads the already-saved per-vehicle CSVs + summary.json.

Run: python3 plot_situation1_overlay.py <run_id> --dir DIR --cars N
"""
import os
import csv
import json
import argparse
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# Same platoon constants as plot_smoke_run_v2.py / campus_config.py
SPACING_M = 10.0
SPEED_KMH = 20.0
SPEED_MPS = SPEED_KMH / 3.6
LAG_S     = SPACING_M / SPEED_MPS
ROUTE_LEN_XLIM = 520.0   # fixed axis across all car-count figures (route is ~514m)

# ---- validated palette (references/palette.md) ----------------------------
INK_PRIMARY   = '#0b0b0b'
INK_SECONDARY = '#52514e'
INK_MUTED     = '#898781'
GRID          = '#e1e0d9'
BASELINE      = '#c3c2b7'
SURFACE       = '#fcfcfb'

# Fixed categorical order (identity = vehicle, never re-cycled/re-ranked)
CAR_COLORS = ['#2a78d6', '#1baf7a', '#eda100', '#008300',
              '#4a3aa7', '#e34948', '#e87ba4', '#eb6834']

RSU_TINTS = {
    'rsu1': '#1baf7a', 'rsu2': '#eda100', 'rsu3': '#008300', 'rsu4': '#e87ba4',
}
RENDITIONS = [(1.0, '360p'), (2.5, '720p'), (5.0, '1080p')]
LOSS_YLIM = 100.0
OUTAGE_YLIM = 40.0


def load(path):
    return list(csv.DictReader(open(path))) if os.path.exists(path) else []


def load_json(path):
    return json.load(open(path)) if os.path.exists(path) else {}


def rsu_spans(pos_list, rsu_list):
    spans, prev, start = [], None, None
    for pos, rsu in zip(pos_list, rsu_list):
        if rsu != prev:
            if prev is not None:
                spans.append((prev, start, pos))
            prev, start = rsu, pos
    if prev is not None:
        spans.append((prev, start, pos_list[-1]))
    return spans


def handover_positions(spans):
    return [p0 for _, p0, _ in spans[1:]]


def cumulative_outage(pos_list, loss_list, speed_mps):
    """Running total (seconds) of time spent in any sample with loss > 0.
    dt is derived from the position delta (pos = tau*speed) rather than
    reading tau directly, since this script only carries position."""
    cum, out, prev_pos = [], 0.0, None
    for pos, loss in zip(pos_list, loss_list):
        if prev_pos is not None:
            dt = (pos - prev_pos) / speed_mps
            if loss > 0:
                out += dt
        cum.append(out)
        prev_pos = pos
    return cum


MIN_ELAPSED_S = 10.0  # warm-up: only long enough to drop the truly
                       # degenerate near-zero-denominator points. A single
                       # early stall (these run 5-11s) still dominates the
                       # ratio for a while after that -- a running/cumulative
                       # ratio naturally decays from an inflated early value
                       # toward its converged rate, which is correct, expected
                       # behaviour for this kind of statistic, not a bug. The
                       # authoritative number is the final (right-edge) value,
                       # which is why it's also labelled directly from
                       # summary.json rather than read off the noisy curve.


def cumulative_rebuffer_ratio(pos_list, stall_list, speed_mps):
    """Running stall-time / elapsed-time ratio (%) over position, using
    each segment's own timestamp-derived elapsed time (position/speed).
    Returns (pos_out, ratio_out), both filtered to elapsed >= MIN_ELAPSED_S."""
    cum_stall = 0.0
    pos_out, ratio_out = [], []
    for pos, stall in zip(pos_list, stall_list):
        cum_stall += stall
        elapsed = pos / speed_mps
        if elapsed < MIN_ELAPSED_S:
            continue
        pos_out.append(pos)
        ratio_out.append(100.0 * cum_stall / elapsed)
    return pos_out, ratio_out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('run_id')
    ap.add_argument('--dir', default=None)
    ap.add_argument('--out-dir', default='graphs')
    ap.add_argument('--cars', type=int, required=True)
    args = ap.parse_args()
    d = args.dir or os.path.join('results_raw', args.run_id)
    os.makedirs(args.out_dir, exist_ok=True)

    n = args.cars
    all_seg, all_net, all_summary = {}, {}, {}
    for i in range(n):
        car = f'car{i + 1}'
        all_seg[car] = load(os.path.join(d, f'{args.run_id}_{car}_segments.csv'))
        all_net[car] = load(os.path.join(d, f'{args.run_id}_{car}_network.csv'))
        all_summary[car] = load_json(os.path.join(d, f'{args.run_id}_{car}_summary.json'))

    fig, axes = plt.subplots(7, 1, figsize=(11, 17), facecolor=SURFACE, sharex=True,
                              gridspec_kw={'height_ratios': [1.1, 1.1, 1.0, 0.9, 0.9,
                                                              0.9 + 0.12 * n, 1.0]})
    (ax_q, ax_rssi, ax_bw, ax_loss, ax_out, ax_ho, ax_rebuf) = axes

    for ax in axes:
        ax.set_facecolor(SURFACE)
        for spine in ('top', 'right'):
            ax.spines[spine].set_visible(False)
        for spine in ('left', 'bottom'):
            ax.spines[spine].set_color(BASELINE)
        ax.tick_params(colors=INK_MUTED, labelsize=8.5)
        ax.grid(True, color=GRID, linewidth=0.8, axis='y')
        ax.set_xlim(0, ROUTE_LEN_XLIM)

    # ---- background RSU-zone bands, from car1's own logged sequence -------
    car1_net = all_net['car1']
    car1_pos = [ (float(r['t']) - 0 * LAG_S) * SPEED_MPS for r in car1_net ]
    car1_spans = rsu_spans(car1_pos, [r['rsu'] for r in car1_net]) if car1_net else []
    for ax in (ax_q, ax_rssi, ax_bw, ax_loss, ax_out, ax_rebuf):
        for rsu, p0, p1 in car1_spans:
            ax.axvspan(p0, p1, color=RSU_TINTS.get(rsu, '#ddd'), alpha=0.13, lw=0, zorder=0)

    per_car_pos, per_car_rsu = {}, {}
    for i in range(n):
        car = f'car{i + 1}'
        color = CAR_COLORS[i % len(CAR_COLORS)]
        seg, net, summ = all_seg[car], all_net[car], all_summary[car]
        shift = i * LAG_S

        net_pos = [(float(r['t']) - shift) * SPEED_MPS for r in net] if net else []
        per_car_pos[car] = net_pos
        per_car_rsu[car] = [r['rsu'] for r in net] if net else []

        # Row 1: rendition/quality
        if seg:
            seg_pos = [(float(r['timestamp']) - shift) * SPEED_MPS for r in seg]
            br = [float(r['bitrate_kbps']) / 1000.0 for r in seg]
            ax_q.step(seg_pos, br, where='post', color=color, linewidth=1.6,
                      alpha=0.9, label=f'{car}')

        # Row 2: RSSI
        if net:
            rssi = [float(r['rssi_dbm']) for r in net]
            ax_rssi.plot(net_pos, rssi, color=color, linewidth=1.4, alpha=0.9)

        # Row 3: allocated bandwidth
        if net:
            bw_pos = [p for p, r in zip(net_pos, net) if r['allocated_bw_mbps']]
            bw = [float(r['allocated_bw_mbps']) for r in net if r['allocated_bw_mbps']]
            ax_bw.plot(bw_pos, bw, color=color, linewidth=1.4, alpha=0.9)

        # Row 4: packet loss
        loss = [float(r['icmp_loss_pct']) for r in net] if net else []
        if net:
            ax_loss.plot(net_pos, loss, color=color, linewidth=1.2, alpha=0.85)

        # Row 5: cumulative outage
        if net:
            cum_out = cumulative_outage(net_pos, loss, SPEED_MPS)
            ax_out.plot(net_pos, cum_out, color=color, linewidth=1.6, alpha=0.9)

        # Row 7: cumulative rebuffer ratio
        if seg:
            stall = [float(r['stall_duration_s']) for r in seg]
            rebuf_pos, cum_rebuf = cumulative_rebuffer_ratio(seg_pos, stall, SPEED_MPS)
            ax_rebuf.plot(rebuf_pos, cum_rebuf, color=color, linewidth=1.6, alpha=0.9)
            final = summ.get('rebuffering_ratio')
            if final is not None:
                ax_rebuf.annotate(f'{final * 100:.1f}%', xy=(ROUTE_LEN_XLIM, final * 100),
                                   xytext=(3, 0), textcoords='offset points',
                                   fontsize=7.5, color=color, fontweight='bold',
                                   va='center', annotation_clip=False)

    # Row 6: handover events -- one horizontal track per vehicle
    track_ys = list(range(n, 0, -1))   # car1 on top, matches template's order
    for i in range(n):
        car = f'car{i + 1}'
        color = CAR_COLORS[i % len(CAR_COLORS)]
        spans = rsu_spans(per_car_pos[car], per_car_rsu[car]) if per_car_pos[car] else []
        ho_pos = handover_positions(spans)
        y = track_ys[i]
        ax_ho.axhline(y, color=GRID, linewidth=0.8, zorder=0)
        if ho_pos:
            ax_ho.scatter(ho_pos, [y] * len(ho_pos), marker='v', s=60,
                          color=color, zorder=3)
    ax_ho.set_yticks(track_ys)
    ax_ho.set_yticklabels([f'car{i + 1}' for i in range(n)], fontsize=8.5)
    ax_ho.set_ylim(0.4, n + 0.6)

    # ---- per-row cosmetics -----------------------------------------------
    for val, _ in RENDITIONS:
        ax_q.axhline(val, color=GRID, linewidth=1.0, zorder=0)
    ax_q.set_ylim(0, 6)
    ax_q.set_yticks([1.0, 2.5, 5.0])
    ax_q.set_yticklabels(['360p', '720p', '1080p'])
    ax_q.set_title('Rendition / Quality', fontsize=11, fontweight='bold', color=INK_PRIMARY)

    ax_rssi.set_ylim(-80, -35)
    ax_rssi.set_ylabel('RSSI (dBm)', fontsize=9, color=INK_SECONDARY)
    ax_rssi.set_title('RSSI', fontsize=11, fontweight='bold', color=INK_PRIMARY)

    ax_bw.set_ylim(0, 11)
    ax_bw.set_ylabel('Bandwidth (Mbps)', fontsize=9, color=INK_SECONDARY)
    ax_bw.set_title('Allocated Bandwidth (step2h + contention)', fontsize=11,
                     fontweight='bold', color=INK_PRIMARY)

    ax_loss.set_ylim(0, LOSS_YLIM)
    ax_loss.set_ylabel('Loss (%)', fontsize=9, color=INK_SECONDARY)
    ax_loss.set_title('Packet Loss', fontsize=11, fontweight='bold', color=INK_PRIMARY)

    ax_out.set_ylim(-OUTAGE_YLIM * 0.02, OUTAGE_YLIM)   # tiny negative floor so a flat-zero line for an outage-free vehicle is still visible above the axis spine
    ax_out.set_ylabel('Outage (s)', fontsize=9, color=INK_SECONDARY)
    ax_out.set_title('Cumulative Outage', fontsize=11, fontweight='bold', color=INK_PRIMARY)

    ax_ho.set_title('Handover Events', fontsize=11, fontweight='bold', color=INK_PRIMARY)

    ax_rebuf.set_ylim(-0.3, 15)   # tiny negative floor, same reason as the outage row; headroom above the real observed max (~10.7%)
    ax_rebuf.set_ylabel('Rebuffer %', fontsize=9, color=INK_SECONDARY)
    ax_rebuf.set_xlabel('Position (m)', fontsize=9.5, color=INK_SECONDARY)
    ax_rebuf.set_title('Rebuffer Ratio (cum. stall / elapsed)', fontsize=11,
                        fontweight='bold', color=INK_PRIMARY)
    ax_rebuf.text(0.005, 0.95, 'labels = final session value; early curve is '
                  'noisy while elapsed time is still small', transform=ax_rebuf.transAxes,
                  fontsize=7, color=INK_MUTED, ha='left', va='top', style='italic')

    handles, labels = ax_q.get_legend_handles_labels()
    fig.legend(handles, labels, loc='lower center', ncol=min(n, 7), frameon=False,
               fontsize=9, bbox_to_anchor=(0.5, -0.01), labelcolor=INK_SECONDARY)

    fig.suptitle(f'Situation 1: Traffic Density — SDN+DASH, {n} vehicles ({args.run_id})',
                 fontsize=14, fontweight='bold', color=INK_PRIMARY, y=0.998)

    plt.tight_layout(rect=[0.02, 0.03, 1, 0.98])
    out = os.path.join(args.out_dir, f'{args.run_id}_overlay_plot.png')
    plt.savefig(out, dpi=150, facecolor=SURFACE, bbox_inches='tight')
    print('Saved:', out)


if __name__ == '__main__':
    main()
