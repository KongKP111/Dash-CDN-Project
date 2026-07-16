#!/usr/bin/env python3
"""
plot_situation1_avg.py -- Situation 1 (Traffic Density) SDN+DASH,
cross-vehicle AVERAGE view. One column, 5 rows (same row set as
plot_situation1_percar.py), one figure per car-count case (3/5/7),
sized for an A4 portrait page insertion into the paper.

Each row's line is the mean across all n_cars vehicles in that density
case, resampled onto a common time grid (dt=0.5s, matching campus_config's
SAMPLE_DT_S) with zero-order hold (step) interpolation -- appropriate here
since every underlying signal (RSSI, bandwidth, loss, rendition, per-
segment QoE) is itself piecewise-constant between real samples, not a
continuously-varying quantity that linear interpolation would suit.

RSU zone shading / handover markers from plot_situation1_percar.py are
intentionally NOT carried over here: those are well-defined for a single
vehicle's own zone-crossing timeline, but with n staggered vehicles
(10 m gap, same speed) averaged onto one shared time axis, no single
"current RSU" exists at a given t across the whole platoon, so a per-
vehicle zone band would misrepresent the averaged line under it.

Usage:
    python3 plot_situation1_avg.py --run smoke_3cars_v5 --cars 3
"""
import csv
import os
import argparse
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

CASE_COLOR = {3: '#1baf7a', 5: '#eda100', 7: '#e34948'}
C_DASH = '#2a78d6'
_MU = 1.0
GRID_DT = 0.5  # matches campus_config.SAMPLE_DT_S

A4_W, A4_H = 8.27, 11.69  # inches, portrait


def load(path):
    return list(csv.DictReader(open(path))) if os.path.exists(path) else []


def col(rows, key, cast=float):
    return [cast(r[key]) for r in rows]


def compute_dash_qoe(seg_rows):
    qoes, prev_bitrate = [], None
    for r in seg_rows:
        bitrate = float(r['bitrate_kbps']) / 1000.0
        switch_penalty = (_MU * abs(bitrate - prev_bitrate)
                          if prev_bitrate is not None else 0.0)
        rebuf_s = float(r['stall_duration_s'])
        qoes.append(bitrate - switch_penalty - rebuf_s)
        prev_bitrate = bitrate
    return qoes


def step_hold(sample_t, sample_v, grid):
    """Zero-order-hold resample: value at grid[k] is the last known sample
    at or before grid[k] (np.nan before the first sample)."""
    sample_t = np.asarray(sample_t, dtype=float)
    sample_v = np.asarray(sample_v, dtype=float)
    idx = np.searchsorted(sample_t, grid, side='right') - 1
    out = np.full(grid.shape, np.nan)
    valid = idx >= 0
    out[valid] = sample_v[idx[valid]]
    return out


def load_case(run_dir, run_id, n_cars):
    per_car = []
    for i in range(1, n_cars + 1):
        seg = load(os.path.join(run_dir, f'{run_id}_car{i}_segments.csv'))
        net = load(os.path.join(run_dir, f'{run_id}_car{i}_network.csv'))
        if not net:
            print(f'  [WARN] no car{i} network.csv in {run_dir}')
            continue
        per_car.append((i, seg, net))
    return per_car


def make_avg_plot(n_cars, run_dir, run_id, out_path):
    per_car = load_case(run_dir, run_id, n_cars)
    if not per_car:
        print(f'  [WARN] nothing to plot for {n_cars} cars')
        return

    # common grid: 0 .. min(tmax across cars), so every car has real data
    # across the whole grid (no extrapolation past any car's own run).
    tmax_each = [max(col(net, 't')) for _, _, net in per_car]
    tmax = min(tmax_each)
    grid = np.arange(GRID_DT, tmax + 1e-9, GRID_DT)

    rssi_stack, bw_stack, loss_stack, qoe_stack, qual_stack = [], [], [], [], []
    for i, seg, net in per_car:
        t = col(net, 't')
        rssi_stack.append(step_hold(t, col(net, 'rssi_dbm'), grid))
        bw_stack.append(step_hold(t, col(net, 'allocated_bw_mbps'), grid))
        loss_stack.append(step_hold(t, col(net, 'icmp_loss_pct'), grid))
        if seg:
            seg_t = col(seg, 'timestamp')
            qoe = compute_dash_qoe(seg)
            qbr = [float(r['bitrate_kbps']) / 1000.0 for r in seg]
            qoe_stack.append(step_hold(seg_t, qoe, grid))
            qual_stack.append(step_hold(seg_t, qbr, grid))

    avg_rssi = np.nanmean(rssi_stack, axis=0)
    avg_bw = np.nanmean(bw_stack, axis=0)
    avg_loss = np.nanmean(loss_stack, axis=0)
    avg_qoe = np.nanmean(qoe_stack, axis=0) if qoe_stack else np.full(grid.shape, np.nan)
    avg_qual = np.nanmean(qual_stack, axis=0) if qual_stack else np.full(grid.shape, np.nan)
    net_qoe_mean = np.nansum(avg_qoe) if qoe_stack else 0.0
    n_qoe_samples = np.count_nonzero(~np.isnan(avg_qoe))

    color = CASE_COLOR.get(n_cars, C_DASH)

    fig, axes = plt.subplots(5, 1, figsize=(A4_W, A4_H), facecolor='white')
    fig.subplots_adjust(left=0.12, right=0.96, top=0.90, bottom=0.05, hspace=0.6)
    fig.suptitle(f'Situation 1: Traffic Density — {n_cars} cars',
                 fontsize=15, fontweight='bold', color='#1a1a1a', y=0.985)
    fig.text(0.5, 0.955, f'SDN+DASH, average across {len(per_car)} vehicles',
              ha='center', fontsize=9.5, color='#666666')

    # 1. QoE
    ax = axes[0]
    ax.plot(grid, avg_qoe, color=color, lw=1.8, marker='o', markersize=2.2, zorder=4)
    ax.fill_between(grid, avg_qoe, alpha=0.15, color=color)
    ax.set_title('Quality of Experience (QoE)', fontsize=11, fontweight='semibold', pad=6)
    ax.set_ylabel('QoE (score)', fontsize=9.5)
    ax.set_xlabel('Time (s)', fontsize=9)
    ax.set_xlim(0, tmax)
    if n_qoe_samples:
        ax.text(0.99, 0.06, f'mean QoE = {net_qoe_mean / n_qoe_samples:.3f}',
                transform=ax.transAxes, ha='right', va='bottom', fontsize=8,
                fontweight='semibold',
                bbox=dict(boxstyle='round,pad=0.25', facecolor='white',
                          edgecolor='#cccccc', alpha=0.85))

    # 2. RSSI
    ax = axes[1]
    ax.plot(grid, avg_rssi, color=color, lw=1.4, zorder=3, alpha=0.7)
    ax.scatter(grid, avg_rssi, s=10, color=color, zorder=5, edgecolors='none')
    ax.set_title('RSSI', fontsize=11, fontweight='semibold', pad=6)
    ax.set_ylabel('RSSI (dBm)', fontsize=9.5)
    ax.set_xlabel('Time (s)', fontsize=9)
    ax.set_xlim(0, tmax)

    # 3. Bandwidth
    ax = axes[2]
    ax.step(grid, avg_bw, color=color, lw=1.8, where='post', zorder=4)
    ax.fill_between(grid, avg_bw, step='post', alpha=0.15, color=color)
    ax.set_ylim(0, max(np.nanmax(avg_bw) * 1.15, 1))
    ax.set_title('Allocated Bandwidth (step2h + contention)', fontsize=11, fontweight='semibold', pad=6)
    ax.set_ylabel('Bandwidth (Mbps)', fontsize=9.5)
    ax.set_xlabel('Time (s)', fontsize=9)
    ax.set_xlim(0, tmax)

    # 4. Packet loss
    ax = axes[3]
    ax.plot(grid, avg_loss, color=color, lw=1.8, marker='o', markersize=2.2, zorder=4)
    ax.fill_between(grid, avg_loss, alpha=0.15, color=color)
    ax.set_ylim(0, max(np.nanmax(avg_loss) * 1.2, 5))
    ax.set_title('Packet Loss', fontsize=11, fontweight='semibold', pad=6)
    ax.set_ylabel('Packet Loss (%)', fontsize=9.5)
    ax.set_xlabel('Time (s)', fontsize=9)
    ax.set_xlim(0, tmax)

    # 5. Quality of Rendition
    ax = axes[4]
    ax.step(grid, avg_qual, color=color, lw=1.8, where='post', zorder=4)
    ax.fill_between(grid, avg_qual, step='post', alpha=0.15, color=color)
    ax.set_ylim(0, 6)
    ax.set_yticks([1.0, 2.5, 5.0])
    ax.set_yticklabels(['360p', '720p', '1080p'], fontsize=8.5)
    ax.set_title('Quality of Rendition', fontsize=11, fontweight='semibold', pad=6)
    ax.set_ylabel('Rendition (p)', fontsize=9.5)
    ax.set_xlabel('Time (s)', fontsize=9)
    ax.set_xlim(0, tmax)

    for ax in axes:
        ax.set_facecolor('#f4f4f4')
        ax.grid(True, color='white', lw=1.0)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['left'].set_color('#cccccc')
        ax.spines['bottom'].set_color('#cccccc')
        ax.tick_params(colors='#555555')

    os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
    fig.savefig(out_path, dpi=200, facecolor='white', edgecolor='none')
    print(f'  saved -> {out_path}  (n={len(grid)} grid points, {len(per_car)} vehicles)')
    plt.close(fig)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description='Situation 1 (SDN+DASH), cross-vehicle average, A4 layout')
    p.add_argument('--run', required=True, help='run_id, e.g. smoke_3cars_v5')
    p.add_argument('--cars', type=int, required=True, choices=[3, 5, 7])
    p.add_argument('--dir', default=None, help='where the CSVs live (default: results_raw/<run>)')
    p.add_argument('--out-dir', default='graphs')
    args = p.parse_args()

    run_dir = args.dir or os.path.join('results_raw', args.run)
    out_path = os.path.join(args.out_dir, f'avg_{args.run}_{args.cars}cars.png')
    make_avg_plot(args.cars, run_dir, args.run, out_path)
