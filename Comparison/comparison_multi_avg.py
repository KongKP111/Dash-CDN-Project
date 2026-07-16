#!/usr/bin/env python3
"""
comparison_multi_avg.py -- CDN_SIT1 vs Situation1_DASH (traffic density,
platoon loop route), CROSS-VEHICLE AVERAGE view. One column per car count
(3/5/7), same averaging methodology as CDN_SIT1/plot_multi_car_avg.py and
Situation1_DASH/plot_situation1_avg.py: each arm's line is the mean across
all n_cars vehicles in that density case, resampled onto its own common
time grid (dt=0.5s) with zero-order-hold (step) interpolation.

Unlike comparison_multi.py (which plots car1, the platoon leader, only),
this script answers the "is it car1 or the whole platoon's average"
question the other way -- both arms here are genuinely averaged across
every vehicle in the case, not a single representative car.

RSU zone shading / handover lines ARE shown, unlike the two underlying
avg scripts (which drop them entirely for the reason below) -- here
they're drawn from CDN car1's own real per-tick timeline only, as a
rough visual reference for "roughly where each RSU boundary falls in
time", not as an exact per-vehicle statement (with n staggered vehicles
averaged onto one shared time axis, no single "current RSU" exists at a
given t across the WHOLE platoon -- car1 is just the same reference
vehicle comparison_multi.py already uses). One handover line per event
(CDN car1 only, not doubled up with DASH's own) -- same convention as
every other Comparison/*.py script.

Panel set: QoE, Bandwidth, Packet Loss, Stall (fraction of platoon
stalled), Cumulative Outage (mean across platoon) -- same 5 topics as
comparison_multi.py, just each line is now a platoon-wide average instead
of car1 alone.

Usage:
    python3 comparison_multi_avg.py
    python3 comparison_multi_avg.py --cars 3 5 7
"""
import os, sys, glob, argparse
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, '..'))

# Reuse the two per-repo avg scripts' own functions directly (find_latest_run,
# load_case, step_hold, GRID_DT, col, ...) rather than re-deriving the
# averaging logic a third time -- guarantees this stays identical to
# CDN_SIT1/graphs/avg_*.png and Situation1_DASH/graphs/avg_*.png.
sys.path.insert(0, os.path.join(_REPO_ROOT, 'CDN_SIT1'))
import plot_multi_car_avg as cdn_avg   # noqa: E402  (M.compute_cdn_qoe, load_case, step_hold, GRID_DT)

sys.path.insert(0, os.path.join(_REPO_ROOT, 'Situation1_DASH'))
import plot_situation1_avg as dash_avg  # noqa: E402  (compute_dash_qoe, load_case, step_hold, GRID_DT)

# Applies this project's shared plot style (grey grid background, no top/
# right spines, ...) via _common's module-level plt.rcParams.update() --
# same house style as every other Comparison/*.py script. Also pulls in
# the zone-band/handover-line helpers used for the CDN-car1 reference
# overlay (see module docstring).
sys.path.insert(0, _HERE)
from _common import add_zone_bands, add_handover_lines, handover_xs

CDN_ROOT  = os.path.join(_REPO_ROOT, 'CDN_SIT1', 'result_multi_car')
DASH_ROOT = os.path.join(_REPO_ROOT, 'Situation1_DASH', 'results_raw')
CAR_COUNTS = [3, 5, 7]

C_CDN  = "#1baf7a"
C_DASH = "#e67e22"


def find_cdn_run(n_cars):
    return cdn_avg.find_latest_run(n_cars)


def zone_int(rsu_col):
    """CDN's 'rsu' column is a plain int already; kept for parity with
    comparison_multi.py's own zone_int() (same name, same behavior) in
    case a future CSV shape needs the 'rsuN' string form normalized too."""
    return [int(str(v)[3:]) if str(v).lower().startswith('rsu') else int(v) for v in rsu_col]


def find_dash_run(n_cars):
    dirs = [d for d in glob.glob(os.path.join(DASH_ROOT, f'smoke_{n_cars}cars*'))
            if os.path.isdir(d)]
    if not dirs:
        return None, None
    run_dir = max(dirs, key=os.path.getmtime)
    return run_dir, os.path.basename(run_dir)


def cdn_platoon_avg(run_dir, n_cars):
    per_car = cdn_avg.load_case(run_dir, n_cars)
    if not per_car:
        return None
    tmax = min(max(cdn_avg.col(rows, 't')) for _, rows in per_car)
    grid = np.arange(cdn_avg.GRID_DT, tmax + 1e-9, cdn_avg.GRID_DT)

    qoe_stack, bw_stack, loss_stack, stall_stack, outage_stack = [], [], [], [], []
    ref_t, ref_zone, ref_ho_t = None, None, None
    for car_i, rows in per_car:
        t = cdn_avg.col(rows, 't')
        qoe_stack.append(cdn_avg.step_hold(t, cdn_avg.M.compute_cdn_qoe(rows), grid))
        bw_stack.append(cdn_avg.step_hold(t, cdn_avg.col(rows, 'bw_mbps'), grid))
        loss_stack.append(cdn_avg.step_hold(t, cdn_avg.col(rows, 'loss_pct'), grid))
        stall_stack.append(cdn_avg.step_hold(t, cdn_avg.col(rows, 'stall', int), grid))
        outage_stack.append(cdn_avg.step_hold(t, cdn_avg.col(rows, 'cum_outage_s'), grid))
        if car_i == 1:
            # car1 (platoon leader) real per-tick timeline, kept unresampled
            # -- used only as a rough visual reference for the zone bands /
            # handover lines (see module docstring), not part of the average.
            ref_t = t
            ref_zone = zone_int(cdn_avg.col(rows, 'rsu', str))
            ref_ho_t = handover_xs(t, cdn_avg.col(rows, 'handover', int))

    return dict(
        grid=grid, n_vehicles=len(per_car),
        qoe=np.nanmean(qoe_stack, axis=0),
        bw=np.nanmean(bw_stack, axis=0),
        loss=np.nanmean(loss_stack, axis=0),
        stall=np.nanmean(stall_stack, axis=0),
        outage=np.nanmean(outage_stack, axis=0),
        ref_t=ref_t, ref_zone=ref_zone, ref_ho_t=ref_ho_t,
    )


def dash_platoon_avg(run_dir, run_id, n_cars):
    per_car = dash_avg.load_case(run_dir, run_id, n_cars)
    if not per_car:
        return None
    tmax = min(max(dash_avg.col(net, 't')) for _, _, net in per_car)
    grid = np.arange(dash_avg.GRID_DT, tmax + 1e-9, dash_avg.GRID_DT)

    qoe_stack, bw_stack, loss_stack, stall_stack, outage_stack = [], [], [], [], []
    for _, seg, net in per_car:
        t = dash_avg.col(net, 't')
        bw_stack.append(dash_avg.step_hold(t, dash_avg.col(net, 'allocated_bw_mbps'), grid))
        loss_stack.append(dash_avg.step_hold(t, dash_avg.col(net, 'icmp_loss_pct'), grid))
        outage_stack.append(dash_avg.step_hold(t, dash_avg.col(net, 'cum_outage_s'), grid))
        if seg:
            seg_t = dash_avg.col(seg, 'timestamp')
            qoe = dash_avg.compute_dash_qoe(seg)
            stall_seg = [1 if float(s['stall_duration_s']) > 0 else 0 for s in seg]
            qoe_stack.append(dash_avg.step_hold(seg_t, qoe, grid))
            stall_stack.append(dash_avg.step_hold(seg_t, stall_seg, grid))

    return dict(
        grid=grid, n_vehicles=len(per_car),
        qoe=np.nanmean(qoe_stack, axis=0) if qoe_stack else np.full(grid.shape, np.nan),
        bw=np.nanmean(bw_stack, axis=0),
        loss=np.nanmean(loss_stack, axis=0),
        stall=np.nanmean(stall_stack, axis=0) if stall_stack else np.full(grid.shape, np.nan),
        outage=np.nanmean(outage_stack, axis=0),
    )


def plot_column(axes_col, n_cars, cdn, dash):
    tmax = max(cdn['grid'][-1], dash['grid'][-1])
    ref_t, ref_zone, ref_ho_t = cdn['ref_t'], cdn['ref_zone'], cdn['ref_ho_t']

    # 1. QoE
    ax = axes_col[0]
    ax.plot(dash['grid'], dash['qoe'], color=C_DASH, lw=1.6, marker='o', markersize=2.2, label='DASH', zorder=4)
    ax.fill_between(dash['grid'], dash['qoe'], alpha=0.12, color=C_DASH)
    ax.plot(cdn['grid'], cdn['qoe'], color=C_CDN, lw=1.6, label='CDN (SDN)', zorder=4)
    ax.fill_between(cdn['grid'], cdn['qoe'], alpha=0.12, color=C_CDN)
    ax.set_ylim(0, 5.5); ax.set_xlim(0, tmax)
    ax.set_title(f"{n_cars} cars  (CDN n={cdn['n_vehicles']}, DASH n={dash['n_vehicles']})\nQoE (platoon avg)",
                 fontsize=10, fontweight='semibold')
    ax.set_ylabel('QoE (score)', fontsize=9)
    ax.set_xlabel('Time (s)', fontsize=9)
    ax.legend(loc='upper right', fontsize=7.5, framealpha=0.85)
    dash_valid = dash['qoe'][~np.isnan(dash['qoe'])]
    cdn_valid = cdn['qoe'][~np.isnan(cdn['qoe'])]
    ax.text(0.99, 0.06,
            f"DASH mean={np.mean(dash_valid):.2f} (n={len(dash_valid)})\n"
            f"CDN  mean={np.mean(cdn_valid):.2f} (n={len(cdn_valid)})",
            transform=ax.transAxes, ha='right', va='bottom', fontsize=7.5,
            fontweight='semibold',
            bbox=dict(boxstyle='round,pad=0.3', facecolor='white', edgecolor='#cccccc', alpha=0.85))
    add_zone_bands(ax, ref_t, ref_zone, tmax, (0, 5.5))
    add_handover_lines(ax, ref_ho_t, (0, 5.5))

    # 2. Bandwidth
    ax = axes_col[1]
    ax.step(dash['grid'], dash['bw'], color=C_DASH, lw=1.4, where='post', label='DASH', zorder=4)
    ax.step(cdn['grid'], cdn['bw'], color=C_CDN, lw=1.4, where='post', label='CDN (SDN)', zorder=4)
    bw_max = max(np.nanmax(cdn['bw']), np.nanmax(dash['bw'])) * 1.15
    ax.set_ylim(0, max(bw_max, 1)); ax.set_xlim(0, tmax)
    ax.set_title('Bandwidth (platoon avg)', fontsize=9.5, fontweight='semibold')
    ax.set_ylabel('Bandwidth (Mbps)', fontsize=9)
    ax.set_xlabel('Time (s)', fontsize=9)
    add_zone_bands(ax, ref_t, ref_zone, tmax, (0, max(bw_max, 1)))
    add_handover_lines(ax, ref_ho_t, (0, max(bw_max, 1)))

    # 3. Packet Loss
    ax = axes_col[2]
    ax.plot(dash['grid'], dash['loss'], color=C_DASH, lw=1.4, label='DASH', zorder=4)
    ax.plot(cdn['grid'], cdn['loss'], color=C_CDN, lw=1.4, label='CDN (SDN)', zorder=4)
    loss_max = max(np.nanmax(cdn['loss']), np.nanmax(dash['loss'])) * 1.2
    ax.set_ylim(0, max(loss_max, 5)); ax.set_xlim(0, tmax)
    ax.set_title('Packet Loss (platoon avg)', fontsize=9.5, fontweight='semibold')
    ax.set_ylabel('Loss (%)', fontsize=9)
    ax.set_xlabel('Time (s)', fontsize=9)
    add_zone_bands(ax, ref_t, ref_zone, tmax, (0, max(loss_max, 5)))
    add_handover_lines(ax, ref_ho_t, (0, max(loss_max, 5)))

    # 4. Stall (fraction of platoon stalled at each instant)
    ax = axes_col[3]
    ax.plot(dash['grid'], dash['stall'], color=C_DASH, lw=1.4, marker='o', markersize=2, label='DASH', zorder=4)
    ax.plot(cdn['grid'], cdn['stall'], color=C_CDN, lw=1.4, label='CDN (SDN)', zorder=4)
    ax.set_ylim(-0.05, 1.05); ax.set_xlim(0, tmax)
    ax.set_title('Stall fraction of platoon (CDN=per-tick, DASH=per-seg)', fontsize=8.5, fontweight='semibold')
    ax.set_ylabel('Fraction stalled', fontsize=9)
    ax.set_xlabel('Time (s)', fontsize=9)
    add_zone_bands(ax, ref_t, ref_zone, tmax, (-0.05, 1.05))
    add_handover_lines(ax, ref_ho_t, (-0.05, 1.05))

    # 5. Cumulative Outage (mean across platoon)
    ax = axes_col[4]
    ax.step(dash['grid'], dash['outage'], color=C_DASH, lw=1.6, where='post', label='DASH', zorder=4)
    ax.step(cdn['grid'], cdn['outage'], color=C_CDN, lw=1.6, where='post', label='CDN (SDN)', zorder=4)
    out_max = max(np.nanmax(cdn['outage']), np.nanmax(dash['outage'])) * 1.15
    ax.set_ylim(0, max(out_max, 1)); ax.set_xlim(0, tmax)
    ax.set_title('Cumulative Outage (platoon avg)', fontsize=9.5, fontweight='semibold')
    ax.set_ylabel('Outage (s)', fontsize=9)
    ax.set_xlabel('Time (s)', fontsize=9)
    add_zone_bands(ax, ref_t, ref_zone, tmax, (0, max(out_max, 1)))
    add_handover_lines(ax, ref_ho_t, (0, max(out_max, 1)))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description='CDN_SIT1 vs Situation1_DASH comparison, platoon-wide average')
    p.add_argument('--cars', type=int, nargs='+', default=CAR_COUNTS)
    p.add_argument('--out', type=str,
                    default=os.path.join(_HERE, 'plots', 'comparison_multi_avg.png'))
    args = p.parse_args()

    present, data = [], {}
    for n in args.cars:
        cdn_run_dir = find_cdn_run(n)
        dash_run_dir, dash_run_id = find_dash_run(n)
        if not cdn_run_dir or not dash_run_dir:
            print(f'[WARN] {n} cars: missing '
                  f'{"CDN" if not cdn_run_dir else ""} {"DASH" if not dash_run_dir else ""} run -- skipping')
            continue
        cdn = cdn_platoon_avg(cdn_run_dir, n)
        dash = dash_platoon_avg(dash_run_dir, dash_run_id, n)
        if cdn is None or dash is None:
            print(f'[WARN] {n} cars: nothing to average on one side -- skipping')
            continue
        print(f'[{n} cars] CDN={cdn_run_dir}  (n_vehicles={cdn["n_vehicles"]})')
        print(f'[{n} cars] DASH={dash_run_dir}  (n_vehicles={dash["n_vehicles"]})')
        present.append(n)
        data[n] = (cdn, dash)

    if not present:
        print('[ERROR] no car count had both CDN and DASH runs -- nothing to plot')
        sys.exit(1)

    fig, axes = plt.subplots(5, len(present), figsize=(6.2 * len(present), 14),
                             facecolor='white', squeeze=False)
    fig.subplots_adjust(hspace=0.55, wspace=0.3, top=0.93)
    fig.suptitle('CDN_SIT1 vs Situation1_DASH — Traffic Density (platoon-wide average)',
                 fontsize=13, fontweight='bold', color='#1a1a1a', y=0.985)

    for col_i, n in enumerate(present):
        cdn, dash = data[n]
        plot_column(axes[:, col_i], n, cdn, dash)

    out = args.out
    os.makedirs(os.path.dirname(out), exist_ok=True)
    fig.savefig(out, dpi=150, bbox_inches='tight', facecolor='white', edgecolor='none')
    print(f'\n  saved -> {out}')
    plt.close(fig)
    print('Done.')
