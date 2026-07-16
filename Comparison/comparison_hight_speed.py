#!/usr/bin/env python3
"""
comparison_hight_speed.py — CDN_SIT2 vs Situation2_DASH (mobility speed,
straight-line 4-AP/RSU, position-x axis). One column per speed
(80/100/120 km/h), same 5-panel set as comparison_baseline.py:
  QoE / Bandwidth / Packet Loss / Stall / Cumulative Outage
Handover count + Rebuffer ratio % shown as a text annotation on each
column's QoE panel (run-level scalars, not per-tick lines).

Auto-picks the newest CSV per speed per arm (by mtime).

Usage:
    python3 comparison_hight_speed.py
    python3 comparison_hight_speed.py --speeds 80 100 120
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

CDN_ROOT  = os.path.join(_REPO_ROOT, 'CDN_SIT2', 'results_hightspeed', 'sit1')
DASH_ROOT = os.path.join(_REPO_ROOT, 'Situation2_DASH', 'results_hightspeed')
SPEEDS = [80, 100, 120]


def find_cdn_csv(speed):
    matches = glob.glob(os.path.join(CDN_ROOT, f'speed{speed}', '*', '*.csv'))
    matches = [m for m in matches
               if not any(tag in os.path.basename(m)
                          for tag in ('vlc_events_', 'vlc_playback_', 'topology_ho_', 'ryu_ho_'))]
    return max(matches, key=os.path.getmtime) if matches else None


def find_dash_csv(speed):
    matches = glob.glob(os.path.join(DASH_ROOT, f'speed{speed}', '*.csv'))
    return max(matches, key=os.path.getmtime) if matches else None


def compute_dash_qoe(rows):
    qoes, prev_bitrate, prev_t = [], None, None
    for r in rows:
        bitrate = DASH_LADDER.get(r['quality'], 0.0)
        switch_penalty = (DASH_MU * abs(bitrate - prev_bitrate)
                          if prev_bitrate is not None else 0.0)
        t = float(r['t'])
        dt = (t - prev_t) if prev_t is not None else 0.0
        rebuf_s = dt if int(r.get('stall', 0)) else 0.0
        qoes.append(bitrate - switch_penalty - rebuf_s)
        prev_bitrate = bitrate
        prev_t = t
    return qoes


def cum_stall_from_stall_col(rows, stall_col):
    total, prev_t = 0.0, None
    for r, s in zip(rows, stall_col):
        t = float(r['t'])
        dt = (t - prev_t) if prev_t is not None else 0.0
        if s:
            total += dt
        prev_t = t
    return total


def ap_zone_int(ap_col):
    out = []
    for v in ap_col:
        v = str(v)
        out.append(int(v[2:]) if v.lower().startswith('ap') else int(v))
    return out


def plot_column(axes_col, speed, cdn_path, dash_path):
    rc = load_csv(cdn_path); rd = load_csv(dash_path)
    xc = col(rc, 'x'); xd = col(rd, 'x')
    qoe_c = M.compute_cdn_qoe(rc)
    qoe_d = compute_dash_qoe(rd)
    bw_c = col(rc, 'bw_mbps'); bw_d = col(rd, 'bw_mbps')
    loss_c = col(rc, 'loss_pct'); loss_d = col(rd, 'loss')
    stall_c = col(rc, 'stall', int); stall_d = col(rd, 'stall', int)
    ho_c = col(rc, 'handover', int); ho_d = col(rd, 'handover', int)
    outage_c = col(rc, 'cum_outage_s'); outage_d = col(rd, 'cum_outage_s')
    zone_c = ap_zone_int(col(rc, 'ap', str))

    xmin, xmax = min(xc + xd), max(xc + xd)
    # One handover line per event, from CDN's own timeline only -- both
    # arms cross the same physical AP boundaries on this straight-line
    # route, so drawing DASH's handover x's too just doubled up
    # near-identical dashed lines at each boundary instead of adding
    # information. Same convention as the zone bands above (CDN-only).
    ho_xs = handover_xs(xc, ho_c)

    total_t_c = float(rc[-1]['t']); total_t_d = float(rd[-1]['t'])
    cum_stall_c = float(rc[-1]['vlc_cum_stall_s'])
    cum_stall_d = cum_stall_from_stall_col(rd, stall_d)

    ax = axes_col[0]
    ax.plot(xd, qoe_d, color=C_DASH, lw=1.6, label='DASH', zorder=4)
    ax.fill_between(xd, qoe_d, alpha=0.12, color=C_DASH)
    ax.plot(xc, qoe_c, color=C_CDN, lw=1.6, label='CDN (SDN)', zorder=4)
    ax.fill_between(xc, qoe_c, alpha=0.12, color=C_CDN)
    ax.set_ylim(0, 5.5); ax.set_xlim(xmin, xmax)
    ax.set_title(f'{speed} km/h\nQoE', fontsize=10, fontweight='semibold')
    ax.set_ylabel('QoE (score)', fontsize=9)
    ax.set_xlabel('Position (m)', fontsize=9)
    add_zone_bands(ax, xc, zone_c, xmax, (0, 5.5))
    add_handover_lines(ax, ho_xs, (0, 5.5))
    ax.legend(loc='upper right', fontsize=7.5, framealpha=0.85)
    summary_box(ax, [
        f'DASH avg={sum(qoe_d)/len(qoe_d):.2f} HOs={sum(ho_d)} '
        f'rebuf={rebuffer_pct(cum_stall_d, total_t_d):.1f}%',
        f'CDN  avg={sum(qoe_c)/len(qoe_c):.2f} HOs={sum(ho_c)} '
        f'rebuf={rebuffer_pct(cum_stall_c, total_t_c):.1f}%',
    ], loc='lower right')

    ax = axes_col[1]
    ax.step(xd, bw_d, color=C_DASH, lw=1.4, where='post', label='DASH', zorder=4)
    ax.step(xc, bw_c, color=C_CDN, lw=1.4, where='post', label='CDN (SDN)', zorder=4)
    bw_max = max(max(bw_c + bw_d) * 1.15, 1)
    ax.set_ylim(0, bw_max); ax.set_xlim(xmin, xmax)
    ax.set_title('Bandwidth (Mbps)', fontsize=9.5, fontweight='semibold')
    ax.set_ylabel('Bandwidth (Mbps)', fontsize=9)
    ax.set_xlabel('Position (m)', fontsize=9)
    add_zone_bands(ax, xc, zone_c, xmax, (0, bw_max))
    add_handover_lines(ax, ho_xs, (0, bw_max))

    ax = axes_col[2]
    ax.plot(xd, loss_d, color=C_DASH, lw=1.4, label='DASH', zorder=4)
    ax.plot(xc, loss_c, color=C_CDN, lw=1.4, label='CDN (SDN)', zorder=4)
    loss_max = max(max(loss_c + loss_d) * 1.2, 5)
    ax.set_ylim(0, loss_max); ax.set_xlim(xmin, xmax)
    ax.set_title('Packet Loss (%)', fontsize=9.5, fontweight='semibold')
    ax.set_ylabel('Loss (%)', fontsize=9)
    ax.set_xlabel('Position (m)', fontsize=9)
    add_zone_bands(ax, xc, zone_c, xmax, (0, loss_max))
    add_handover_lines(ax, ho_xs, (0, loss_max))

    ax = axes_col[3]
    ax.step(xd, stall_d, color=C_DASH, lw=1.3, where='post', alpha=0.8, zorder=4)
    ax.step(xc, [v + 0.02 for v in stall_c], color=C_CDN, lw=1.3, where='post', alpha=0.8, zorder=3)
    ax.set_ylim(-0.1, 1.2); ax.set_xlim(xmin, xmax)
    ax.set_yticks([0, 1]); ax.set_yticklabels(['OK', 'STALL'], fontsize=8)
    ax.set_title('Stall (0/1)', fontsize=9.5, fontweight='semibold')
    ax.set_xlabel('Position (m)', fontsize=9)
    add_zone_bands(ax, xc, zone_c, xmax, (-0.1, 1.2))
    add_handover_lines(ax, ho_xs, (-0.1, 1.2))

    ax = axes_col[4]
    ax.step(xd, outage_d, color=C_DASH, lw=1.6, where='post', label='DASH', zorder=4)
    ax.step(xc, outage_c, color=C_CDN, lw=1.6, where='post', label='CDN (SDN)', zorder=4)
    out_max = max(max(outage_c + outage_d) * 1.15, 1)
    ax.set_ylim(0, out_max); ax.set_xlim(xmin, xmax)
    ax.set_title('Cumulative Outage (s)', fontsize=9.5, fontweight='semibold')
    ax.set_ylabel('Outage (s)', fontsize=9)
    ax.set_xlabel('Position (m)', fontsize=9.5)
    add_zone_bands(ax, xc, zone_c, xmax, (0, out_max))
    add_handover_lines(ax, ho_xs, (0, out_max))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description='CDN_SIT2 vs Situation2_DASH comparison plot')
    p.add_argument('--speeds', type=int, nargs='+', default=SPEEDS)
    p.add_argument('--out', type=str,
                    default=os.path.join(_HERE, 'plots', 'comparison_hight_speed.png'))
    args = p.parse_args()

    present = []
    paths = {}
    for spd in args.speeds:
        cdn_path = find_cdn_csv(spd)
        dash_path = find_dash_csv(spd)
        if not cdn_path or not dash_path:
            print(f'[WARN] speed={spd}: missing '
                  f'{"CDN" if not cdn_path else ""} {"DASH" if not dash_path else ""} run -- skipping')
            continue
        print(f'[speed={spd}] CDN={cdn_path}')
        print(f'[speed={spd}] DASH={dash_path}')
        present.append(spd)
        paths[spd] = (cdn_path, dash_path)

    if not present:
        print('[ERROR] no speed had both CDN and DASH runs -- nothing to plot')
        sys.exit(1)

    fig, axes = plt.subplots(5, len(present), figsize=(6.2 * len(present), 14),
                             facecolor='white', squeeze=False)
    fig.subplots_adjust(hspace=0.55, wspace=0.3, top=0.93)
    fig.suptitle('CDN_SIT2 vs Situation2_DASH — Mobility Speed',
                 fontsize=13, fontweight='bold', color='#1a1a1a', y=0.985)

    for col_i, spd in enumerate(present):
        cdn_path, dash_path = paths[spd]
        plot_column(axes[:, col_i], spd, cdn_path, dash_path)

    out = args.out
    os.makedirs(os.path.dirname(out), exist_ok=True)
    fig.savefig(out, dpi=150, bbox_inches='tight', facecolor='white', edgecolor='none')
    print(f'\n  saved -> {out}')
    plt.close(fig)
    print('Done.')
