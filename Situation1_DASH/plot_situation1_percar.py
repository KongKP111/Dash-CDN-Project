#!/usr/bin/env python3
"""
plot_situation1_percar.py -- Situation 1 (Traffic Density) SDN+DASH,
per-vehicle columns. Mirrors the structure of the SDN+CDN teammate's
CDN_SIT1/plot_multi_car_percar.py 1:1 (read-only reference, not modified):
one figure PER car-count case (3 separate PNGs for 3/5/7 cars), each
vehicle gets its own column, same 5-panel-per-vehicle style.

Row set adapted from CDN's 6 rows for what the DASH arm actually has:
  1. Quality of Experience (QoE)  -- KEPT, same Yin et al. formula/constants
     as CDN_baseline/dash_cdn_comparison.py's compute_dash_qoe() (mu=1.0,
     bitrate ladder 360p/720p/1080p = 1.0/2.5/5.0 Mbps), computed from
     segments.csv's real per-segment bitrate_kbps + stall_duration_s
     (DASH's natural ~4s segment cadence -- this arm doesn't have CDN's
     per-network-tick quality polling, so QoE here is naturally sparser
     than the other rows, which is an honest reflection of the real
     protocol, not a bug).
  2. RSSI                          -- KEPT, from network.csv rssi_dbm
  3. Allocated Bandwidth (step2h+contention) -- KEPT, from network.csv
  4. Packet Loss                   -- KEPT, from network.csv icmp_loss_pct
  5. Quality / Rendition           -- REPLACES CDN's "Cache HIT/MISS" (DASH
     has no cache tier; rendition selection is the DASH-native equivalent
     signal), from segments.csv bitrate_kbps
  "CDN Latency" row DROPPED entirely -- the DASH arm has no CDN-edge
  latency measurement, there's nothing to plot.

X-axis is TIME (not position), same reasoning as CDN's script: this is a
loop route, so position is not monotonic across a lap and would make the
line double back on itself.

No sudo needed; reads the already-saved per-vehicle CSVs (segments.csv,
network.csv) directly off disk.

Usage:
    python3 plot_situation1_percar.py --run smoke_3cars_v5 --cars 3 --dir DIR
"""
import csv
import os
import argparse
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

CASE_COLOR = {3: '#1baf7a', 5: '#eda100', 7: '#e34948'}   # same as CDN_SIT1, for cross-team visual parity
C_DASH = '#2a78d6'    # single consistent accent for all DASH data series (categorical slot 1, matches this project's other plots)
C_HO   = '#e89c00'    # handover lines, same colour CDN uses

RSU_BAND = {
    'rsu1': ('#2a78d6', 0.08), 'rsu2': ('#1baf7a', 0.08),
    'rsu3': ('#eda100', 0.09), 'rsu4': ('#e34948', 0.08),
}
_MU = 1.0

ROW_TITLES = ['Quality of Experience (QoE)', 'RSSI',
              'Allocated Bandwidth (step2h + contention)', 'Packet Loss',
              'Quality / Rendition']


def load(path):
    return list(csv.DictReader(open(path))) if os.path.exists(path) else []


def col(rows, key, cast=float):
    return [cast(r[key]) for r in rows]


def compute_dash_qoe(seg_rows):
    """Per-segment Yin et al. QoE term: q(R_k) - mu*|q(R_k)-q(R_k-1)| - T_k.
    Same formula/constants as CDN_baseline/dash_cdn_comparison.py's
    compute_dash_qoe(), just fed from this arm's own segments.csv instead
    of a per-tick-sampled CSV -- T_k here is the real measured
    stall_duration_s (seconds), not a 0/1 flag, so this is if anything a
    more literal reading of the original formula than the tick-based
    version."""
    qoes, prev_bitrate = [], None
    for r in seg_rows:
        bitrate = float(r['bitrate_kbps']) / 1000.0
        switch_penalty = (_MU * abs(bitrate - prev_bitrate)
                          if prev_bitrate is not None else 0.0)
        rebuf_s = float(r['stall_duration_s'])
        qoes.append(bitrate - switch_penalty - rebuf_s)
        prev_bitrate = bitrate
    return qoes


def zone_spans(t, rsu, tmax):
    spans, i = [], 0
    while i < len(t):
        a = rsu[i]
        j = i
        while j < len(t) and rsu[j] == a:
            j += 1
        t_end = t[j] if j < len(t) else tmax
        spans.append((t[i], t_end, a))
        i = j
    return spans


def zone_transitions(t, rsu):
    return [(t[i], f'{rsu[i-1].upper()}→{rsu[i].upper()}')
            for i in range(1, len(rsu)) if rsu[i] != rsu[i - 1]]


def add_rsu_bands(ax, t, rsu, tmax, ylim, show_label=True):
    trange = (tmax - min(t)) or 1
    last_label_t = None
    for t0, t1, a in zone_spans(t, rsu, tmax):
        color, alpha = RSU_BAND.get(a, ('#aaaaaa', 0.07))
        ax.axvspan(t0, t1, color=color, alpha=alpha, zorder=0, linewidth=0)
        if not show_label:
            continue
        ct = (t0 + t1) / 2
        wide_enough = (t1 - t0) / trange > 0.04
        far_enough = last_label_t is None or (ct - last_label_t) / trange > 0.08
        if wide_enough and far_enough:
            ax.text(ct, ylim[0] + (ylim[1] - ylim[0]) * 0.03, a.upper(),
                    ha='center', va='bottom', fontsize=6.5, color=color,
                    alpha=0.85, zorder=1)
            last_label_t = ct


def add_handover_lines(ax, trans, ylim, t_offset=1.5, show_label=True):
    for t_ho, lbl in trans:
        ax.axvline(t_ho, color=C_HO, lw=1.0, ls='--', alpha=0.7, zorder=3)
        if show_label:
            ax.text(t_ho + t_offset, ylim[1] * 0.97, lbl, rotation=90,
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


def make_case_plot(n_cars, run_dir, run_id, out_path):
    per_car = []
    for i in range(1, n_cars + 1):
        seg = load(os.path.join(run_dir, f'{run_id}_car{i}_segments.csv'))
        net = load(os.path.join(run_dir, f'{run_id}_car{i}_network.csv'))
        if not net:
            print(f'  [WARN] no car{i} network.csv in {run_dir}')
            continue
        per_car.append((i, seg, net))
    if not per_car:
        print(f'  [WARN] nothing to plot for {n_cars} cars')
        return

    fig, axes = plt.subplots(5, len(per_car),
                              figsize=(4.3 * len(per_car), 15.0),
                              facecolor='white', squeeze=False)
    fig.subplots_adjust(hspace=0.6, wspace=0.32, top=0.90)
    fig.suptitle(
        f'Situation 1: Traffic Density — SDN+DASH, {n_cars} cars '
        f'(one column per vehicle)',
        fontsize=13, fontweight='bold', color='#1a1a1a', y=0.995)

    for col_i, (i, seg, net) in enumerate(per_car):
        t = col(net, 't')
        rssi = col(net, 'rssi_dbm')
        bw = col(net, 'allocated_bw_mbps')
        loss = col(net, 'icmp_loss_pct')
        rsu = col(net, 'rsu', str)
        tmin, tmax = min(t), max(t)
        trans = zone_transitions(t, rsu)

        seg_t = col(seg, 'timestamp') if seg else []
        qoe = compute_dash_qoe(seg) if seg else []
        net_qoe = sum(qoe) if qoe else 0.0

        col_title = f'car{i}  (n={len(net)})'
        axes[0, col_i].annotate(
            col_title, xy=(0.5, 1.22), xycoords='axes fraction',
            ha='center', va='bottom', fontsize=11, fontweight='bold',
            color=CASE_COLOR.get(n_cars, '#333333'))

        # 1. QoE (segment cadence, sparser than the network-tick rows below)
        ax = axes[0, col_i]
        if qoe:
            ax.plot(seg_t, qoe, color=C_DASH, lw=1.8, marker='o', markersize=3, zorder=4)
            ax.fill_between(seg_t, qoe, alpha=0.15, color=C_DASH)
        ax.set_xlim(tmin, tmax)
        ax.set_title(ROW_TITLES[0], fontsize=9.5, fontweight='semibold', pad=4)
        if col_i == 0:
            ax.set_ylabel('QoE', fontsize=9)
        if qoe:
            ax.text(0.99, 0.06, f'Net={net_qoe:.1f} (avg {net_qoe/len(qoe):.3f})',
                    transform=ax.transAxes, ha='right', va='bottom', fontsize=7,
                    fontweight='semibold',
                    bbox=dict(boxstyle='round,pad=0.25', facecolor='white',
                              edgecolor='#cccccc', alpha=0.85))
        add_rsu_bands(ax, t, rsu, tmax, ax.get_ylim())
        add_handover_lines(ax, trans, ax.get_ylim())

        # 2. RSSI
        ax = axes[1, col_i]
        ax.plot(t, rssi, color=C_DASH, lw=1.4, zorder=3, alpha=0.6)
        ax.scatter(t, rssi, s=16, color=C_DASH, zorder=5, marker='o', edgecolors='none')
        rssi_ylim = (min(rssi) - 5, max(rssi) + 5)
        ax.set_ylim(*rssi_ylim); ax.set_xlim(tmin, tmax)
        ax.set_title(ROW_TITLES[1], fontsize=9.5, fontweight='semibold', pad=4)
        if col_i == 0:
            ax.set_ylabel('RSSI (dBm)', fontsize=9)
        add_rsu_bands(ax, t, rsu, tmax, rssi_ylim, show_label=False)
        add_handover_lines(ax, trans, rssi_ylim, show_label=False)

        # 3. Bandwidth
        ax = axes[2, col_i]
        bw_max = max(max(bw) * 1.15, 1)
        ax.step(t, bw, color=C_DASH, lw=1.8, where='post', zorder=4)
        ax.fill_between(t, bw, step='post', alpha=0.15, color=C_DASH)
        ax.set_ylim(0, bw_max); ax.set_xlim(tmin, tmax)
        ax.set_title(ROW_TITLES[2], fontsize=9.5, fontweight='semibold', pad=4)
        if col_i == 0:
            ax.set_ylabel('Bandwidth (Mbps)', fontsize=9)
        add_rsu_bands(ax, t, rsu, tmax, (0, bw_max), show_label=False)
        add_handover_lines(ax, trans, (0, bw_max), show_label=False)

        # 4. Packet loss
        ax = axes[3, col_i]
        ax.plot(t, loss, color=C_DASH, lw=1.8, marker='o', markersize=3, zorder=4)
        ax.fill_between(t, loss, alpha=0.15, color=C_DASH)
        loss_max = max(max(loss) * 1.2, 5)
        ax.set_ylim(0, loss_max); ax.set_xlim(tmin, tmax)
        ax.set_title(ROW_TITLES[3], fontsize=9.5, fontweight='semibold', pad=4)
        if col_i == 0:
            ax.set_ylabel('Loss (%)', fontsize=9)
        add_rsu_bands(ax, t, rsu, tmax, (0, loss_max), show_label=False)
        add_handover_lines(ax, trans, (0, loss_max), show_label=False)

        # 5. Quality / Rendition (replaces CDN's Cache HIT/MISS)
        ax = axes[4, col_i]
        if seg:
            qbr = [float(r['bitrate_kbps']) / 1000.0 for r in seg]
            ax.step(seg_t, qbr, color=C_DASH, lw=1.8, where='post', zorder=4)
            ax.fill_between(seg_t, qbr, step='post', alpha=0.15, color=C_DASH)
        ax.set_ylim(0, 6); ax.set_xlim(tmin, tmax)
        ax.set_yticks([1.0, 2.5, 5.0])
        ax.set_yticklabels(['360p', '720p', '1080p'], fontsize=8)
        if col_i == 0:
            ax.set_ylabel('Rendition', fontsize=9)
        ax.set_xlabel('Time (s)', fontsize=9)
        ax.set_title(ROW_TITLES[4], fontsize=9.5, fontweight='semibold', pad=4)
        add_rsu_bands(ax, t, rsu, tmax, (0, 6), show_label=False)
        add_handover_lines(ax, trans, (0, 6), show_label=False)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    print(f'  saved -> {out_path}')
    plt.close(fig)


if __name__ == '__main__':
    p = argparse.ArgumentParser(
        description='Situation 1 (SDN+DASH), one column per vehicle -- mirrors CDN_SIT1/plot_multi_car_percar.py')
    p.add_argument('--run', required=True, help='run_id, e.g. smoke_3cars_v5')
    p.add_argument('--cars', type=int, required=True, choices=[3, 5, 7])
    p.add_argument('--dir', default=None, help='where the CSVs live (default: results_raw/<run>)')
    p.add_argument('--out-dir', default='graphs')
    args = p.parse_args()

    run_dir = args.dir or os.path.join('results_raw', args.run)
    out_path = os.path.join(args.out_dir, f'percar_{args.run}_{args.cars}cars.png')
    make_case_plot(args.cars, run_dir, args.run, out_path)
