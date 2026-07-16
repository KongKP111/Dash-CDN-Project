#!/usr/bin/env python3
"""
comparison_multi.py — CDN_SIT1 vs Situation1_DASH (traffic density,
platoon loop route). One column per car count (3/5/7), car1 (platoon
leader) used as the representative vehicle for both arms -- same
convention as this project's other per-case detail plots
(plot_multi_car_detail.py, plot_situation1_percar.py).

X-axis is TIME, not position -- this is a loop route, so position isn't
monotonic and would make the lines cross/double back (same reasoning as
every other Sit1 plot script in this project).

Same 5-panel set as the other 2 Comparison/*.py scripts:
  QoE / Bandwidth / Packet Loss / Stall / Cumulative Outage

QoE/Stall resolution differs between arms here, unavoidably: CDN's is
per-network-tick (SAMPLE_DT=0.5s cadence); DASH's own segments.csv is
per-segment (~4s cadence, DASH's real segment-fetch rate) -- plotted at
its own timestamps rather than resampled, same "honest reflection of the
real protocol, not a bug" reasoning as plot_situation1_percar.py.

Usage:
    python3 comparison_multi.py
    python3 comparison_multi.py --cars 3 5 7
"""
import os, sys, glob, argparse
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, '..'))
sys.path.insert(0, os.path.join(_REPO_ROOT, 'CDN_baseline'))
import baseline_model as M   # compute_cdn_qoe()

sys.path.insert(0, _HERE)
from _common import (
    C_CDN, C_DASH, load_csv, col, add_zone_bands, handover_xs,
    add_handover_lines, rebuffer_pct, outage_pct, summary_box,
)

DASH_LADDER = {"360p": 1.0, "720p": 2.5, "1080p": 5.0}
DASH_MU = 1.0

CDN_ROOT  = os.path.join(_REPO_ROOT, 'CDN_SIT1', 'result_multi_car')
DASH_ROOT = os.path.join(_REPO_ROOT, 'Situation1_DASH', 'results_raw')
CAR_COUNTS = [3, 5, 7]


def find_cdn_car1(n_cars):
    dirs = [d for d in glob.glob(os.path.join(CDN_ROOT, f'*{n_cars}cars*')) if os.path.isdir(d)]
    if not dirs:
        return None
    run_dir = max(dirs, key=os.path.getmtime)
    matches = glob.glob(os.path.join(run_dir, '*_car1_network.csv'))
    return matches[0] if matches else None


def find_dash_car1(n_cars):
    dirs = [d for d in glob.glob(os.path.join(DASH_ROOT, f'smoke_{n_cars}cars*')) if os.path.isdir(d)]
    if not dirs:
        return None
    run_dir = max(dirs, key=os.path.getmtime)
    net_matches = glob.glob(os.path.join(run_dir, '*_car1_network.csv'))
    seg_matches = glob.glob(os.path.join(run_dir, '*_car1_segments.csv'))
    if not net_matches or not seg_matches:
        return None
    return net_matches[0], seg_matches[0]


def dash_segment_qoe(seg_rows):
    """Per-segment Yin et al. QoE -- identical formula/constants to
    Situation1_DASH/plot_situation1_percar.py's own compute_dash_qoe(),
    T_k = the segment's real measured stall_duration_s (not a tick-based
    dt approximation -- this arm's CSV already gives that directly)."""
    qoes, prev_bitrate = [], None
    for s in seg_rows:
        bitrate = float(s['bitrate_kbps']) / 1000.0
        switch_penalty = (DASH_MU * abs(bitrate - prev_bitrate)
                          if prev_bitrate is not None else 0.0)
        rebuf_s = float(s['stall_duration_s'])
        qoes.append(bitrate - switch_penalty - rebuf_s)
        prev_bitrate = bitrate
    return qoes


def zone_int(rsu_col):
    return [int(str(v)[3:]) if str(v).lower().startswith('rsu') else int(v) for v in rsu_col]


def plot_column(axes_col, n_cars, cdn_net_path, dash_net_path, dash_seg_path):
    rc = load_csv(cdn_net_path)
    rd = load_csv(dash_net_path)
    seg = load_csv(dash_seg_path)

    tc = col(rc, 't'); td = col(rd, 't')
    qoe_c = M.compute_cdn_qoe(rc)
    seg_t = col(seg, 'timestamp')
    qoe_d = dash_segment_qoe(seg)
    bw_c = col(rc, 'bw_mbps'); bw_d = col(rd, 'allocated_bw_mbps')
    loss_c = col(rc, 'loss_pct'); loss_d = col(rd, 'icmp_loss_pct')
    stall_c = col(rc, 'stall', int)
    stall_seg = [1 if float(s['stall_duration_s']) > 0 else 0 for s in seg]
    ho_c = col(rc, 'handover', int); ho_d = col(rd, 'handover', int)
    outage_c = col(rc, 'cum_outage_s'); outage_d = col(rd, 'cum_outage_s')
    zone_c = zone_int(col(rc, 'rsu', str))

    tmax = max(tc + td)
    # One handover line per event, from CDN's own timeline only -- unlike
    # the position-based scripts this is a time axis (loop route, CDN and
    # DASH complete laps at different real speeds), but combining both
    # arms' handover times still just doubled up dashed lines without
    # adding readable information. Same CDN-only convention as the zone
    # bands above.
    ho_ts = handover_xs(tc, ho_c)

    total_t_c = tc[-1]; total_t_d = td[-1]
    cum_stall_c = float(rc[-1]['vlc_cum_stall_s'])
    cum_stall_d = sum(float(s['stall_duration_s']) for s in seg)

    ax = axes_col[0]
    ax.plot(seg_t, qoe_d, color=C_DASH, lw=1.6, marker='o', markersize=2.5, label='DASH', zorder=4)
    ax.fill_between(seg_t, qoe_d, alpha=0.12, color=C_DASH)
    ax.plot(tc, qoe_c, color=C_CDN, lw=1.6, label='CDN (SDN)', zorder=4)
    ax.fill_between(tc, qoe_c, alpha=0.12, color=C_CDN)
    ax.set_ylim(0, 5.5); ax.set_xlim(0, tmax)
    ax.set_title(f'{n_cars} cars  (n={len(rc)})\nQoE', fontsize=10, fontweight='semibold')
    ax.set_ylabel('QoE (score)', fontsize=9)
    ax.set_xlabel('Time (s)', fontsize=9)
    add_zone_bands(ax, tc, zone_c, tmax, (0, 5.5))
    add_handover_lines(ax, ho_ts, (0, 5.5))
    ax.legend(loc='upper right', fontsize=7.5, framealpha=0.85)
    summary_box(ax, [
        f'DASH avg={sum(qoe_d)/len(qoe_d):.2f} (n={len(qoe_d)} seg) HOs={sum(ho_d)} '
        f'rebuf={rebuffer_pct(cum_stall_d, total_t_d):.1f}%',
        f'CDN  avg={sum(qoe_c)/len(qoe_c):.2f} (n={len(qoe_c)} tick) HOs={sum(ho_c)} '
        f'rebuf={rebuffer_pct(cum_stall_c, total_t_c):.1f}%',
    ], loc='lower right')

    ax = axes_col[1]
    ax.step(td, bw_d, color=C_DASH, lw=1.4, where='post', label='DASH', zorder=4)
    ax.step(tc, bw_c, color=C_CDN, lw=1.4, where='post', label='CDN (SDN)', zorder=4)
    bw_max = max(max(bw_c + bw_d) * 1.15, 1)
    ax.set_ylim(0, bw_max); ax.set_xlim(0, tmax)
    ax.set_title('Bandwidth (Mbps)', fontsize=9.5, fontweight='semibold')
    ax.set_ylabel('Bandwidth (Mbps)', fontsize=9)
    ax.set_xlabel('Time (s)', fontsize=9)
    add_zone_bands(ax, tc, zone_c, tmax, (0, bw_max))
    add_handover_lines(ax, ho_ts, (0, bw_max))

    ax = axes_col[2]
    ax.plot(td, loss_d, color=C_DASH, lw=1.4, label='DASH', zorder=4)
    ax.plot(tc, loss_c, color=C_CDN, lw=1.4, label='CDN (SDN)', zorder=4)
    loss_max = max(max(loss_c + loss_d) * 1.2, 5)
    ax.set_ylim(0, loss_max); ax.set_xlim(0, tmax)
    ax.set_title('Packet Loss (%)', fontsize=9.5, fontweight='semibold')
    ax.set_ylabel('Loss (%)', fontsize=9)
    ax.set_xlabel('Time (s)', fontsize=9)
    add_zone_bands(ax, tc, zone_c, tmax, (0, loss_max))
    add_handover_lines(ax, ho_ts, (0, loss_max))

    ax = axes_col[3]
    ax.step(seg_t, stall_seg, color=C_DASH, lw=1.3, where='post', alpha=0.8,
            marker='o', markersize=2.5, zorder=4)
    ax.step(tc, [v + 0.02 for v in stall_c], color=C_CDN, lw=1.3, where='post', alpha=0.8, zorder=3)
    ax.set_ylim(-0.1, 1.2); ax.set_xlim(0, tmax)
    ax.set_yticks([0, 1]); ax.set_yticklabels(['OK', 'STALL'], fontsize=8)
    ax.set_title('Stall (CDN=per-tick, DASH=per-seg)', fontsize=9, fontweight='semibold')
    ax.set_xlabel('Time (s)', fontsize=9)
    add_zone_bands(ax, tc, zone_c, tmax, (-0.1, 1.2))
    add_handover_lines(ax, ho_ts, (-0.1, 1.2))

    ax = axes_col[4]
    ax.step(td, outage_d, color=C_DASH, lw=1.6, where='post', label='DASH', zorder=4)
    ax.step(tc, outage_c, color=C_CDN, lw=1.6, where='post', label='CDN (SDN)', zorder=4)
    out_max = max(max(outage_c + outage_d) * 1.15, 1)
    ax.set_ylim(0, out_max); ax.set_xlim(0, tmax)
    ax.set_title('Cumulative Outage (s)', fontsize=9.5, fontweight='semibold')
    ax.set_ylabel('Outage (s)', fontsize=9)
    ax.set_xlabel('Time (s)', fontsize=9.5)
    add_zone_bands(ax, tc, zone_c, tmax, (0, out_max))
    add_handover_lines(ax, ho_ts, (0, out_max))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description='CDN_SIT1 vs Situation1_DASH comparison plot')
    p.add_argument('--cars', type=int, nargs='+', default=CAR_COUNTS)
    p.add_argument('--out', type=str,
                    default=os.path.join(_HERE, 'plots', 'comparison_multi.png'))
    args = p.parse_args()

    present = []
    paths = {}
    for n in args.cars:
        cdn_path = find_cdn_car1(n)
        dash_res = find_dash_car1(n)
        if not cdn_path or not dash_res:
            print(f'[WARN] {n} cars: missing '
                  f'{"CDN" if not cdn_path else ""} {"DASH" if not dash_res else ""} run -- skipping')
            continue
        dash_net_path, dash_seg_path = dash_res
        print(f'[{n} cars] CDN={cdn_path}')
        print(f'[{n} cars] DASH net={dash_net_path}')
        print(f'[{n} cars] DASH seg={dash_seg_path}')
        present.append(n)
        paths[n] = (cdn_path, dash_net_path, dash_seg_path)

    if not present:
        print('[ERROR] no car count had both CDN and DASH runs -- nothing to plot')
        sys.exit(1)

    fig, axes = plt.subplots(5, len(present), figsize=(6.2 * len(present), 14),
                             facecolor='white', squeeze=False)
    fig.subplots_adjust(hspace=0.55, wspace=0.3, top=0.93)
    fig.suptitle('CDN_SIT1 vs Situation1_DASH — Traffic Density (car1)',
                 fontsize=13, fontweight='bold', color='#1a1a1a', y=0.985)

    for col_i, n in enumerate(present):
        cdn_path, dash_net_path, dash_seg_path = paths[n]
        plot_column(axes[:, col_i], n, cdn_path, dash_net_path, dash_seg_path)

    out = args.out
    os.makedirs(os.path.dirname(out), exist_ok=True)
    fig.savefig(out, dpi=150, bbox_inches='tight', facecolor='white', edgecolor='none')
    print(f'\n  saved -> {out}')
    plt.close(fig)
    print('Done.')
