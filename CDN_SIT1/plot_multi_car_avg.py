#!/usr/bin/env python3
"""
plot_multi_car_avg.py -- Situation 1 (Traffic Density) SDN+CDN,
cross-vehicle AVERAGE view. One column, 6 rows (same row set as
plot_multi_car_percar.py), one figure per car-count case (3/5/7),
sized for an A4 portrait page insertion into the paper.

Same averaging methodology as Situation1_DASH/plot_situation1_avg.py:
each row's line is the mean across all n_cars vehicles in that density
case, resampled onto a common time grid (dt=0.5s, matching
campus_config's SAMPLE_DT_S) with zero-order hold (step) interpolation
-- appropriate here since every underlying signal (RSSI, bandwidth,
loss, cache status, QoE) is itself piecewise-constant between real
samples, not a continuously-varying quantity that linear interpolation
would suit.

RSU zone shading / handover markers from plot_multi_car_percar.py are
intentionally NOT carried over here: those are well-defined for a
single vehicle's own zone-crossing timeline, but with n staggered
vehicles (10 m gap, same speed) averaged onto one shared time axis, no
single "current RSU" exists at a given t across the whole platoon, so a
per-vehicle zone band would misrepresent the averaged line under it.

Cache status (categorical HIT/MISS/LOSS) is averaged via the same
CV_MAP encoding plot_multi_car_percar.py uses for its per-vehicle
scatter dots (HIT=1, MISS=0, LOSS=0.5) -- the averaged line reads as
"fraction of the platoon in a healthy cache state" at each instant,
not a single categorical status.

Usage:
    python3 plot_multi_car_avg.py --run cdn_sdn_3cars_20260713_182204 --cars 3
    python3 plot_multi_car_avg.py                     # all 3 cases, auto-latest
"""
import csv
import os
import sys
import glob
import argparse
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, '..'))
RESULT_ROOT = os.path.join(_HERE, 'result_multi_car')

sys.path.insert(0, os.path.join(_REPO_ROOT, 'CDN_baseline'))
import baseline_model as M

CAR_COUNTS = [3, 5, 7]
CASE_COLOR = {3: '#1baf7a', 5: '#eda100', 7: '#e34948'}
C_CDN = '#1baf7a'
GRID_DT = 0.5  # matches campus_config.SAMPLE_DT_S

# 'LOSS' replaces the old 'UNKNOWN' tier -- cache HIT/MISS is strictly an
# edge-content question; a request that got no answer at all (outage or a
# timed-out probe) is a connection LOSS, not a third cache state. Same
# encoding as plot_multi_car_percar.py's CV_MAP.
CV_MAP = {'HIT': 1, 'MISS': 0, 'LOSS': 0.5, 'UNKNOWN': 0.5}

A4_W, A4_H = 8.27, 11.69  # inches, portrait


def find_latest_run(n_cars):
    candidates = [
        d for d in glob.glob(os.path.join(RESULT_ROOT, f'*{n_cars}cars*'))
        if os.path.isdir(d)
    ]
    return max(candidates, key=os.path.getmtime) if candidates else None


def load(path):
    return list(csv.DictReader(open(path))) if os.path.exists(path) else []


def col(rows, key, cast=float):
    return [cast(r[key]) for r in rows]


def step_hold(sample_t, sample_v, grid):
    """Zero-order-hold resample: value at grid[k] is the last known sample
    at or before grid[k] (np.nan before the first sample). Identical logic
    to Situation1_DASH/plot_situation1_avg.py's own step_hold()."""
    sample_t = np.asarray(sample_t, dtype=float)
    sample_v = np.asarray(sample_v, dtype=float)
    idx = np.searchsorted(sample_t, grid, side='right') - 1
    out = np.full(grid.shape, np.nan)
    valid = idx >= 0
    out[valid] = sample_v[idx[valid]]
    return out


def load_case(run_dir, n_cars):
    per_car = []
    for i in range(1, n_cars + 1):
        matches = glob.glob(os.path.join(run_dir, f'*_car{i}_network.csv'))
        if not matches:
            print(f'  [WARN] no car{i} CSV in {run_dir}')
            continue
        per_car.append((i, load(matches[0])))
    return per_car


def make_avg_plot(n_cars, run_dir, out_path):
    per_car = load_case(run_dir, n_cars)
    if not per_car:
        print(f'  [WARN] nothing to plot for {n_cars} cars')
        return

    # common grid: 0 .. min(tmax across cars), so every car has real data
    # across the whole grid (no extrapolation past any car's own run).
    tmax_each = [max(col(rows, 't')) for _, rows in per_car]
    tmax = min(tmax_each)
    grid = np.arange(GRID_DT, tmax + 1e-9, GRID_DT)

    qoe_stack, lat_stack, rssi_stack, bw_stack, loss_stack, cache_stack = [], [], [], [], [], []
    for i, rows in per_car:
        t = col(rows, 't')
        qoe = M.compute_cdn_qoe(rows)
        cv = [CV_MAP.get(c, 0.5) for c in col(rows, 'cache', str)]
        qoe_stack.append(step_hold(t, qoe, grid))
        lat_stack.append(step_hold(t, col(rows, 'latency_s'), grid))
        rssi_stack.append(step_hold(t, col(rows, 'rssi_dbm'), grid))
        bw_stack.append(step_hold(t, col(rows, 'bw_mbps'), grid))
        loss_stack.append(step_hold(t, col(rows, 'loss_pct'), grid))
        cache_stack.append(step_hold(t, cv, grid))

    avg_qoe = np.nanmean(qoe_stack, axis=0)
    avg_lat = np.nanmean(lat_stack, axis=0)
    avg_rssi = np.nanmean(rssi_stack, axis=0)
    avg_bw = np.nanmean(bw_stack, axis=0)
    avg_loss = np.nanmean(loss_stack, axis=0)
    avg_cache = np.nanmean(cache_stack, axis=0)
    net_qoe_mean = np.nansum(avg_qoe)
    n_qoe_samples = np.count_nonzero(~np.isnan(avg_qoe))

    color = CASE_COLOR.get(n_cars, C_CDN)

    fig, axes = plt.subplots(6, 1, figsize=(A4_W, A4_H), facecolor='white')
    fig.subplots_adjust(left=0.12, right=0.96, top=0.91, bottom=0.05, hspace=0.65)
    fig.suptitle(f'Situation 1: Traffic Density — {n_cars} cars',
                 fontsize=15, fontweight='bold', color='#1a1a1a', y=0.985)
    fig.text(0.5, 0.955, f'SDN+CDN, average across {len(per_car)} vehicles',
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

    # 2. Latency
    ax = axes[1]
    ax.plot(grid, avg_lat, color=color, lw=1.6, marker='o', markersize=2.2, zorder=4)
    ax.fill_between(grid, avg_lat, alpha=0.12, color=color)
    ax.set_ylim(0, 3.5)
    ax.set_title('CDN Latency', fontsize=11, fontweight='semibold', pad=6)
    ax.set_ylabel('Latency (s)', fontsize=9.5)
    ax.set_xlabel('Time (s)', fontsize=9)
    ax.set_xlim(0, tmax)

    # 3. RSSI
    ax = axes[2]
    ax.plot(grid, avg_rssi, color=color, lw=1.4, zorder=3, alpha=0.7)
    ax.scatter(grid, avg_rssi, s=10, color=color, zorder=5, edgecolors='none')
    ax.set_title('RSSI', fontsize=11, fontweight='semibold', pad=6)
    ax.set_ylabel('RSSI (dBm)', fontsize=9.5)
    ax.set_xlabel('Time (s)', fontsize=9)
    ax.set_xlim(0, tmax)

    # 4. Bandwidth
    ax = axes[3]
    ax.step(grid, avg_bw, color=color, lw=1.8, where='post', zorder=4)
    ax.fill_between(grid, avg_bw, step='post', alpha=0.15, color=color)
    ax.set_ylim(0, max(np.nanmax(avg_bw) * 1.15, 1))
    ax.set_title('Bandwidth (step2h)', fontsize=11, fontweight='semibold', pad=6)
    ax.set_ylabel('Bandwidth (Mbps)', fontsize=9.5)
    ax.set_xlabel('Time (s)', fontsize=9)
    ax.set_xlim(0, tmax)

    # 5. Packet loss
    ax = axes[4]
    ax.plot(grid, avg_loss, color=color, lw=1.8, marker='o', markersize=2.2, zorder=4)
    ax.fill_between(grid, avg_loss, alpha=0.15, color=color)
    ax.set_ylim(0, max(np.nanmax(avg_loss) * 1.2, 5))
    ax.set_title('Packet Loss', fontsize=11, fontweight='semibold', pad=6)
    ax.set_ylabel('Loss (%)', fontsize=9.5)
    ax.set_xlabel('Time (s)', fontsize=9)
    ax.set_xlim(0, tmax)

    # 6. Cache (fraction of platoon in HIT state, 1=all HIT, 0=all MISS)
    ax = axes[5]
    ax.plot(grid, avg_cache, color=color, lw=1.8, marker='o', markersize=2.2, zorder=4)
    ax.fill_between(grid, avg_cache, alpha=0.15, color=color)
    ax.set_ylim(-0.05, 1.05)
    ax.set_yticks([0, 0.5, 1])
    ax.set_yticklabels(['MISS', 'LOSS', 'HIT'], fontsize=8.5)
    ax.set_title('Cache HIT/MISS (platoon avg)', fontsize=11, fontweight='semibold', pad=6)
    ax.set_ylabel('Cache', fontsize=9.5)
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
    p = argparse.ArgumentParser(description='Situation 1 (SDN+CDN), cross-vehicle average, A4 layout')
    p.add_argument('--run', default=None, help='run_id under result_multi_car/ (default: auto-latest per --cars)')
    p.add_argument('--cars', type=int, choices=CAR_COUNTS, default=None,
                    help='only plot this one case (default: all found)')
    p.add_argument('--out-dir', default=os.path.join(_HERE, 'graphs'))
    args = p.parse_args()

    targets = [args.cars] if args.cars else CAR_COUNTS

    for n in targets:
        run_dir = (os.path.join(RESULT_ROOT, args.run) if args.run
                   else find_latest_run(n))
        if run_dir is None or not os.path.isdir(run_dir):
            print(f'[WARN] no run found for {n} cars')
            continue
        run_id = os.path.basename(run_dir)
        print(f'[{n} cars] loading {run_dir}')
        out_path = os.path.join(args.out_dir, f'avg_{run_id}_{n}cars.png')
        make_avg_plot(n, run_dir, out_path)

    print('\nDone.')
