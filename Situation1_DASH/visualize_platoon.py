#!/usr/bin/env python3
"""
============================================================================
  visualize_platoon.py  --  static preview of the Situation 1 platoon
----------------------------------------------------------------------------
  No sudo / no Mininet needed -- pure matplotlib preview so the platoon
  mobility model (10 m fixed gap, arc-length positions from
  campus_config.py) can be sanity-checked visually before running the real
  sudo Mininet-WiFi simulation. Same visual style as the repo's existing
  ../visualize_topology.py (route + RSU coverage circles), extended with:
    - platoon vehicle positions at several points in time (small multiples)
    - the measured arc-length gap between consecutive vehicles (should be
      exactly SPACING_M = 10.0 m at every snapshot, proving the platoon
      keeps formation correctly as it moves)

  Run: python3 visualize_platoon.py [--cars 3] [--out platoon_preview.png]
============================================================================
"""
import os
import sys
import math
import argparse
import matplotlib
matplotlib.use('Agg')   # headless-safe; no X display required
import matplotlib.pyplot as plt
from matplotlib.patches import Circle

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import campus_config as C

# Same "visual-only" coverage radius as ../visualize_topology.py (real mn-wifi
# range is 300 m; the plotted circle is intentionally smaller just to match
# the look of the existing repo visualizer -- it is not used for any
# handover/coverage decision, target_rsu_by_zone() is a separate rectangular
# zone rule, unaffected by this radius).
COVERAGE_RADIUS_PLOT = 62

COLORS = ['#d62728', '#2ca02c', '#9467bd', '#ff7f0e', '#17becf', '#8c564b', '#e377c2']


def draw_route_and_rsus(ax):
    xs = [x for _, x, y in C.positions]
    ys = [y for _, x, y in C.positions]
    closed_xs, closed_ys = xs + [xs[0]], ys + [ys[0]]
    ax.plot(closed_xs, closed_ys, linewidth=2.0, marker='o',
            markersize=2, color='#1482c5', zorder=1)
    ax.text(xs[0] + 1, ys[0] + 2, 'START/END', fontsize=9, fontweight='bold')

    for rsu_name, cfg in C.RSU_LAYOUT.items():
        apx, apy = cfg['x'], cfg['y']
        ax.scatter(apx, apy, s=120, marker='s', color='black', zorder=3)
        ax.text(apx + 2, apy + 2,
                f"{rsu_name.upper()} ch{cfg['channel']} (802.11g)",
                fontsize=8, fontweight='bold')
        ax.add_patch(Circle((apx, apy), radius=COVERAGE_RADIUS_PLOT,
                             fill=True, facecolor='skyblue',
                             edgecolor='red', linewidth=1.5, alpha=0.15,
                             zorder=0))
    return xs, ys


def snapshot(ax, t, n_cars):
    xs, ys = draw_route_and_rsus(ax)
    pts = [C.vehicle_position(i, t) for i in range(n_cars)]
    for i, (x, y) in enumerate(pts):
        ax.scatter(x, y, s=110, marker='o', color=COLORS[i % len(COLORS)],
                   edgecolor='black', linewidth=0.8, zorder=4,
                   label=f'car{i+1}')
        ax.text(x + 2, y - 4, f'car{i+1}', fontsize=7, fontweight='bold')

    # measured consecutive-gap check (should read exactly SPACING_M)
    gaps = []
    for i in range(n_cars - 1):
        d = math.hypot(pts[i][0] - pts[i+1][0], pts[i][1] - pts[i+1][1])
        gaps.append(d)
    gap_txt = ', '.join(f'{g:.1f}m' for g in gaps)

    ax.set_title(f't = {t:.0f}s   (straight-line car-to-car gap: {gap_txt})',
                 fontsize=10)
    ax.set_xlabel('X (m)')
    ax.set_ylabel('Y (m)')
    ax.grid(True)
    ax.set_aspect('equal', adjustable='box')

    ap_xs = [c['x'] for c in C.RSU_LAYOUT.values()]
    ap_ys = [c['y'] for c in C.RSU_LAYOUT.values()]
    pad = COVERAGE_RADIUS_PLOT + 5
    ax.set_xlim(min(min(xs), min(ap_xs) - pad) - 5,
                max(max(xs), max(ap_xs) + pad) + 5)
    ax.set_ylim(min(min(ys), min(ap_ys) - pad) - 5,
                max(max(ys), max(ap_ys) + pad) + 5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cars', type=int, default=3, choices=C.CAR_COUNTS)
    ap.add_argument('--out', type=str, default='platoon_preview.png')
    args = ap.parse_args()

    n_cars = args.cars
    lap = C.LAP_DURATION_S
    # 6 snapshots spread across one full lap, so the wraparound (last
    # vehicle starting "behind" the loop start) and steady formation over
    # a handover-heavy stretch are both visible.
    times = [0, lap/5, 2*lap/5, 3*lap/5, 4*lap/5, lap - 1]

    fig, axes = plt.subplots(2, 3, figsize=(18, 11))
    for t, ax in zip(times, axes.flat):
        snapshot(ax, t, n_cars)
    axes.flat[0].legend(loc='lower left', fontsize=8)

    fig.suptitle(
        f'Situation 1 -- Traffic Density preview: {n_cars}-car platoon, '
        f'{C.SPACING_M:.0f} m gap, {C.SPEED_KMH:.0f} km/h '
        f'(route {C.ROUTE_LENGTH_M:.0f} m, lap {lap:.0f}s)',
        fontsize=13, fontweight='bold')
    plt.tight_layout(rect=[0, 0, 1, 0.96])
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), args.out)
    plt.savefig(out_path, dpi=100)
    print(f'Saved: {out_path}')

    # numeric self-check, printed for the log (not just the plot)
    print(f'\nArc-length gap check (should all read {C.SPACING_M:.1f} m):')
    for t in times:
        pts = [C.vehicle_position(i, t) for i in range(n_cars)]
        gaps = [round(math.hypot(pts[i][0]-pts[i+1][0], pts[i][1]-pts[i+1][1]), 2)
                for i in range(n_cars - 1)]
        print(f'  t={t:6.1f}s  straight-line gaps={gaps}')


if __name__ == '__main__':
    main()
