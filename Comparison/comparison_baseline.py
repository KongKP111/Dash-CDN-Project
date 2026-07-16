#!/usr/bin/env python3
"""
comparison_baseline.py — CDN_baseline vs dash-baseline (single vehicle,
straight-line 4-AP/RSU, position-x axis).

Panel set (finalized in-session, RSSI dropped -- see project memory):
  1. QoE (Yin et al., both arms dt-based now)
  2. Bandwidth (Mbps, synthetic RSSI-derived -- same Step2HysteresisMapper
     model on both arms)
  3. Packet Loss (%)
  4. Stall (0/1 per-tick timeline -- both arms have a real per-tick stall
     column)
  5. Cumulative Outage (s)
Handover count and Rebuffer ratio % are run-level scalars, not per-tick
lines -- shown as a text annotation on the QoE panel instead (same
convention as this project's other comparison plots).

Auto-picks the newest CSV found for each arm (by mtime) -- no fixed
sit/speed axis on the CDN side and no sit/speed CLI on the dash-baseline
side (this scenario has neither; SPEED_KMH is a fixed 20 in both models).

Usage:
    python3 comparison_baseline.py
    python3 comparison_baseline.py --cdn-csv <path> --dash-csv <path>
"""
import os, sys, glob, argparse
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, '..'))
sys.path.insert(0, os.path.join(_REPO_ROOT, 'CDN_baseline'))
import baseline_model as M   # CDN_BITRATE_MBPS, compute_cdn_qoe()

# DASH's rendition ladder + mu -- CDN_baseline/baseline_model.py has no
# LADDER (CDN has one fixed bitrate, no ABR), so this is hardcoded here,
# same convention as dash_cdn_comparison.py's own _BITRATE_MBPS/_MU.
DASH_LADDER = {"360p": 1.0, "720p": 2.5, "1080p": 5.0}
DASH_MU = 1.0

sys.path.insert(0, _HERE)
from _common import (
    C_CDN, C_DASH, load_csv, col, add_zone_bands, handover_xs,
    add_handover_lines, rebuffer_pct, outage_pct, summary_box,
)

CDN_ROOT  = os.path.join(_REPO_ROOT, 'CDN_baseline', 'results', 'sdn')
DASH_ROOT = os.path.join(_REPO_ROOT, 'dash-baseline')


def find_cdn_csv():
    matches = glob.glob(os.path.join(CDN_ROOT, 'sit*', 'speed*', '*', '*.csv'))
    # exclude the auxiliary per-run files (vlc telemetry/events, handover
    # logs) -- only the main <run_id>.csv has the t/x/bw_mbps/... columns
    # this script needs.
    matches = [m for m in matches
               if not any(tag in os.path.basename(m)
                          for tag in ('vlc_events_', 'vlc_playback_', 'topology_ho_', 'ryu_ho_'))]
    return max(matches, key=os.path.getmtime) if matches else None


def find_dash_csv():
    matches = (glob.glob(os.path.join(DASH_ROOT, 'results', '*.csv')) +
               glob.glob(os.path.join(DASH_ROOT, 'runs', '**', '*.csv'), recursive=True))
    # aggregate/handover_times.csv files are summary artifacts, not
    # per-tick run CSVs -- exclude by header shape (must have a 't' column
    # that's actually numeric per-row, cheapest filter is by filename).
    matches = [m for m in matches if 'aggregate' not in m and 'handover_times' not in m]
    return max(matches, key=os.path.getmtime) if matches else None


def compute_dash_qoe(rows):
    """Same dt-based Yin et al. formula as dash_cdn_comparison.py's own
    compute_dash_qoe() (fixed in-session) -- not imported from there since
    that file's own load path assumes a different CSV shape; re-derived
    here against this scenario's actual columns (quality, t, stall)."""
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


def ap_zone_int(ap_col):
    """CDN's 'ap' column is a string like 'ap1'/'ap2'; DASH's 'rsu' column
    is already a plain int 1-4. Normalize both to int for add_zone_bands()."""
    out = []
    for v in ap_col:
        v = str(v)
        out.append(int(v[2:]) if v.lower().startswith('ap') else int(v))
    return out


def make_plot(cdn_path, dash_path, out_path):
    rc = load_csv(cdn_path)
    rd = load_csv(dash_path)

    xc = col(rc, 'x'); xd = col(rd, 'x')
    qoe_c = M.compute_cdn_qoe(rc)
    qoe_d = compute_dash_qoe(rd)
    bw_c = col(rc, 'bw_mbps'); bw_d = col(rd, 'bw_mbps')
    loss_c = col(rc, 'loss_pct'); loss_d = col(rd, 'loss')
    stall_c = col(rc, 'stall', int); stall_d = col(rd, 'stall', int)
    ho_c = col(rc, 'handover', int); ho_d = col(rd, 'handover', int)
    outage_c = col(rc, 'cum_outage_s'); outage_d = col(rd, 'cum_outage_s')
    zone_c = ap_zone_int(col(rc, 'ap', str)); zone_d = ap_zone_int(col(rd, 'rsu', str))

    xmin, xmax = min(xc + xd), max(xc + xd)
    # One handover line per event, from CDN's own timeline only -- both
    # arms cross the same physical AP boundaries on this straight-line
    # route, so drawing DASH's handover x's too just doubled up
    # near-identical dashed lines at each boundary instead of adding
    # information. Same convention as the zone bands above (CDN-only).
    ho_xs_c = handover_xs(xc, ho_c)

    total_t_c = float(rc[-1]['t']); total_t_d = float(rd[-1]['t'])
    # cum_stall_s: CDN has vlc_cum_stall_s directly (real VLC telemetry);
    # DASH's own cum stall isn't a column here, sum stall*dt instead.
    def cum_stall_from_stall_col(rows, stall_col):
        total, prev_t = 0.0, None
        for r, s in zip(rows, stall_col):
            t = float(r['t'])
            dt = (t - prev_t) if prev_t is not None else 0.0
            if s:
                total += dt
            prev_t = t
        return total
    cum_stall_c = float(rc[-1]['vlc_cum_stall_s'])
    cum_stall_d = cum_stall_from_stall_col(rd, stall_d)

    fig, axes = plt.subplots(5, 1, figsize=(13, 14), facecolor='white')
    fig.subplots_adjust(hspace=0.5, top=0.94)
    fig.suptitle('CDN_baseline vs dash-baseline — single vehicle, 20 km/h',
                 fontsize=13, fontweight='bold', color='#1a1a1a', y=0.975)

    # 1. QoE
    ax = axes[0]
    ax.plot(xd, qoe_d, color=C_DASH, lw=1.8, label='DASH', zorder=4)
    ax.fill_between(xd, qoe_d, alpha=0.12, color=C_DASH)
    ax.plot(xc, qoe_c, color=C_CDN, lw=1.8, label='CDN (SDN)', zorder=4)
    ax.fill_between(xc, qoe_c, alpha=0.12, color=C_CDN)
    ax.set_ylim(0, 5.5); ax.set_xlim(xmin, xmax)
    ax.set_ylabel('QoE (score)', fontsize=10)
    ax.set_xlabel('Position (m)', fontsize=10)
    ax.set_title('Quality of Experience (QoE)', fontsize=10, fontweight='semibold', pad=4)
    add_zone_bands(ax, xc, zone_c, xmax, (0, 5.5))
    add_handover_lines(ax, ho_xs_c, (0, 5.5))
    ax.legend(loc='upper right', fontsize=9, framealpha=0.85)
    summary_box(ax, [
        f'DASH  Net={sum(qoe_d):.1f} avg={sum(qoe_d)/len(qoe_d):.3f}  HOs={sum(ho_d)}  '
        f'rebuf={rebuffer_pct(cum_stall_d, total_t_d):.2f}%  outage={outage_pct(outage_d[-1], total_t_d):.2f}%',
        f'CDN   Net={sum(qoe_c):.1f} avg={sum(qoe_c)/len(qoe_c):.3f}  HOs={sum(ho_c)}  '
        f'rebuf={rebuffer_pct(cum_stall_c, total_t_c):.2f}%  outage={outage_pct(outage_c[-1], total_t_c):.2f}%',
    ], loc='lower right')

    # 2. Bandwidth
    ax = axes[1]
    ax.step(xd, bw_d, color=C_DASH, lw=1.6, where='post', label='DASH', zorder=4)
    ax.step(xc, bw_c, color=C_CDN, lw=1.6, where='post', label='CDN (SDN)', zorder=4)
    bw_max = max(max(bw_c + bw_d) * 1.15, 1)
    ax.set_ylim(0, bw_max); ax.set_xlim(xmin, xmax)
    ax.set_ylabel('Bandwidth (Mbps)', fontsize=10)
    ax.set_xlabel('Position (m)', fontsize=10)
    ax.set_title('Imposed Bandwidth (synthetic, RSSI-derived)', fontsize=10, fontweight='semibold', pad=4)
    add_zone_bands(ax, xc, zone_c, xmax, (0, bw_max))
    add_handover_lines(ax, ho_xs_c, (0, bw_max))
    ax.legend(loc='upper right', fontsize=9, framealpha=0.85)

    # 3. Packet Loss
    ax = axes[2]
    ax.plot(xd, loss_d, color=C_DASH, lw=1.6, label='DASH', zorder=4)
    ax.plot(xc, loss_c, color=C_CDN, lw=1.6, label='CDN (SDN)', zorder=4)
    loss_max = max(max(loss_c + loss_d) * 1.2, 5)
    ax.set_ylim(0, loss_max); ax.set_xlim(xmin, xmax)
    ax.set_ylabel('Loss (%)', fontsize=10)
    ax.set_xlabel('Position (m)', fontsize=10)
    ax.set_title('Packet Loss', fontsize=10, fontweight='semibold', pad=4)
    add_zone_bands(ax, xc, zone_c, xmax, (0, loss_max))
    add_handover_lines(ax, ho_xs_c, (0, loss_max))
    ax.legend(loc='upper right', fontsize=9, framealpha=0.85)

    # 4. Stall (0/1 raw flag timeline)
    ax = axes[3]
    ax.step(xd, stall_d, color=C_DASH, lw=1.4, where='post', alpha=0.8, label='DASH', zorder=4)
    ax.step(xc, [v + 0.02 for v in stall_c], color=C_CDN, lw=1.4, where='post',
            alpha=0.8, label='CDN (SDN)', zorder=3)  # tiny offset so both are visible when both 0/1
    ax.set_ylim(-0.1, 1.2); ax.set_xlim(xmin, xmax)
    ax.set_yticks([0, 1]); ax.set_yticklabels(['OK', 'STALL'], fontsize=9)
    ax.set_ylabel('Stall', fontsize=10)
    ax.set_xlabel('Position (m)', fontsize=10)
    ax.set_title('Stall (0/1 per-tick)', fontsize=10, fontweight='semibold', pad=4)
    add_zone_bands(ax, xc, zone_c, xmax, (-0.1, 1.2))
    add_handover_lines(ax, ho_xs_c, (-0.1, 1.2))
    ax.legend(loc='upper right', fontsize=9, framealpha=0.85)

    # 5. Cumulative Outage
    ax = axes[4]
    ax.step(xd, outage_d, color=C_DASH, lw=1.8, where='post', label='DASH', zorder=4)
    ax.step(xc, outage_c, color=C_CDN, lw=1.8, where='post', label='CDN (SDN)', zorder=4)
    out_max = max(max(outage_c + outage_d) * 1.15, 1)
    ax.set_ylim(0, out_max); ax.set_xlim(xmin, xmax)
    ax.set_ylabel('Outage (s)', fontsize=10)
    ax.set_xlabel('Position (m)', fontsize=10)
    ax.set_title('Cumulative Outage', fontsize=10, fontweight='semibold', pad=4)
    add_zone_bands(ax, xc, zone_c, xmax, (0, out_max))
    add_handover_lines(ax, ho_xs_c, (0, out_max))
    ax.legend(loc='upper right', fontsize=9, framealpha=0.85)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches='tight', facecolor='white', edgecolor='none')
    print(f'  saved -> {out_path}')
    print(f'  DASH: Net QoE={sum(qoe_d):.1f} avg={sum(qoe_d)/len(qoe_d):.3f} '
          f'HOs={sum(ho_d)} rebuf={rebuffer_pct(cum_stall_d, total_t_d):.2f}%')
    print(f'  CDN:  Net QoE={sum(qoe_c):.1f} avg={sum(qoe_c)/len(qoe_c):.3f} '
          f'HOs={sum(ho_c)} rebuf={rebuffer_pct(cum_stall_c, total_t_c):.2f}%')
    plt.close(fig)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description='CDN_baseline vs dash-baseline comparison plot')
    p.add_argument('--cdn-csv', type=str, default=None)
    p.add_argument('--dash-csv', type=str, default=None)
    p.add_argument('--out', type=str,
                    default=os.path.join(_HERE, 'plots', 'comparison_baseline.png'))
    args = p.parse_args()

    cdn_path = args.cdn_csv or find_cdn_csv()
    dash_path = args.dash_csv or find_dash_csv()

    if not cdn_path or not os.path.isfile(cdn_path):
        print(f'[ERROR] no CDN_baseline run found under {CDN_ROOT}'); sys.exit(1)
    if not dash_path or not os.path.isfile(dash_path):
        print(f'[ERROR] no dash-baseline run found under {DASH_ROOT}'); sys.exit(1)

    print(f'[CDN]  {cdn_path}')
    print(f'[DASH] {dash_path}')
    make_plot(cdn_path, dash_path, args.out)
    print('\nDone.')
