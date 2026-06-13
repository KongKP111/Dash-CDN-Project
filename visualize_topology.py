#!/usr/bin/env python3
"""
============================================================================
  Topology Visualizer (static layout view)
----------------------------------------------------------------------------
  Shows the campus route + 4 RSU positions + coverage circles,
  using the same visual style as the SUMO live view.
  This is a STATIC view (no streaming) just to check RSU placement.

  Run: python3 visualize_topology.py
============================================================================
"""
import sys
import os
import matplotlib.pyplot as plt
from matplotlib.patches import Circle

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                'Dash/topology'))
from mobility_positions import positions

# ---- Same RSU config as combined_topology.py ----
RSU_LAYOUT = {
    'rsu1': {'x': 58,  'y': 160},
    'rsu2': {'x': 142, 'y': 160},
    'rsu3': {'x': 138, 'y': 64},
    'rsu4': {'x': 56,  'y': 66},
}
# Plot coverage radius (visual only). Real range=300m, but plot uses
# scaled units. 58 matches the old SUMO live view look.
COVERAGE_RADIUS = 62

xs = [x for _, x, y in positions]
ys = [y for _, x, y in positions]

fig, ax = plt.subplots(figsize=(10, 8))

# Closed loop route (blue)
closed_xs = xs + [xs[0]]
closed_ys = ys + [ys[0]]
ax.plot(closed_xs, closed_ys, linewidth=2.5, marker='o',
        markersize=3, color='#1482c5')

# Start/End label
ax.text(xs[0] + 1, ys[0] + 2, 'START/END', fontsize=10, fontweight='bold')

# RSUs + coverage circles
for ap_name, ap_data in RSU_LAYOUT.items():
    apx, apy = ap_data['x'], ap_data['y']
    ax.scatter(apx, apy, s=140, marker='s')
    ax.text(apx + 2, apy + 2, f"{ap_name.upper()} (R=300m)",
            fontsize=10, fontweight='bold')
    circle = Circle((apx, apy), radius=COVERAGE_RADIUS,
                    fill=True, facecolor='skyblue', edgecolor='red',
                    linewidth=2, alpha=0.18)
    ax.add_patch(circle)

# Car at start
start_t, start_x, start_y = positions[0]
ax.scatter(start_x, start_y, s=160, marker='o', color='dimgray')

ax.set_title('Campus Route + RSU Coverage (DASH Topology)')
ax.set_xlabel('X')
ax.set_ylabel('Y')
ax.grid(True)
ax.set_aspect('equal', adjustable='box')

ap_xs = [ap['x'] for ap in RSU_LAYOUT.values()]
ap_ys = [ap['y'] for ap in RSU_LAYOUT.values()]
min_x = min(min(xs), min(ap_xs) - COVERAGE_RADIUS) - 5
max_x = max(max(xs), max(ap_xs) + COVERAGE_RADIUS) + 5
min_y = min(min(ys), min(ap_ys) - COVERAGE_RADIUS) - 5
max_y = max(max(ys), max(ap_ys) + COVERAGE_RADIUS) + 5
ax.set_xlim(min_x, max_x)
ax.set_ylim(min_y, max_y)

# Coverage check: how many waypoints are covered by at least 1 RSU
covered = 0
for _, x, y in positions:
    for ap in RSU_LAYOUT.values():
        d = ((ap['x'] - x) ** 2 + (ap['y'] - y) ** 2) ** 0.5
        if d <= COVERAGE_RADIUS:
            covered += 1
            break
pct = covered / len(positions) * 100
print(f"Route waypoints: {len(positions)}")
print(f"Covered by >=1 RSU: {covered} ({pct:.1f}%)")
print(f"Coverage radius (plot): {COVERAGE_RADIUS} units")

ax.text(0.02, 0.02, f'Coverage: {pct:.0f}% of route',
        transform=ax.transAxes, fontsize=10,
        bbox=dict(boxstyle='round', alpha=0.3))

plt.tight_layout()
plt.savefig(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'topology_layout.png'), dpi=100)
print("Saved: topology_layout.png")
plt.show()
