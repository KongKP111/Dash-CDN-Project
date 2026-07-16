#!/usr/bin/env python3
"""
_common.py — shared style/plotting helpers for the 3 Comparison/*.py
scripts (comparison_baseline.py, comparison_multi.py,
comparison_hight_speed.py). Not a runnable script itself.

Panel set is the one finalized in-session (7 topics, RSSI dropped):
  QoE, Bandwidth (synthetic), Packet Loss, Handover (dashed lines +
  count annotation), Outage (cum_outage_s line + outage% annotation),
  Rebuffer ratio % (annotation, since it's a run-level scalar not a
  per-tick line), Stall (0/1 timeline where the arm has per-tick stall
  data).
"""
import csv
import matplotlib.pyplot as plt

C_CDN  = "#1baf7a"   # green — CDN series (matches every other CDN-side plot in this project)
C_DASH = "#e67e22"   # orange — DASH series (matches dash_cdn_comparison.py)
C_HO   = "#e89c00"   # handover dashed lines

ZONE_BAND = {
    1: ('#2a78d6', 0.07), 2: ('#1baf7a', 0.07),
    3: ('#eda100', 0.08), 4: ('#e34948', 0.07),
}

plt.rcParams.update({
    'font.family':       'DejaVu Sans',
    'axes.facecolor':    '#f4f4f4',
    'figure.facecolor':  'white',
    'axes.grid':         True,
    'grid.color':        'white',
    'grid.linewidth':    1.0,
    'axes.spines.top':   False,
    'axes.spines.right': False,
    'axes.edgecolor':    '#cccccc',
    'xtick.color':       '#555555',
    'ytick.color':       '#555555',
    'axes.labelcolor':   '#333333',
})


def load_csv(path):
    with open(path) as f:
        return list(csv.DictReader(f))


def col(rows, key, cast=float):
    return [cast(r[key]) for r in rows]


def zone_spans(x, zone, xmax):
    spans, i = [], 0
    while i < len(x):
        a = zone[i]; j = i
        while j < len(x) and zone[j] == a:
            j += 1
        spans.append((x[i], x[j] if j < len(x) else xmax, a))
        i = j
    return spans


def add_zone_bands(ax, x, zone, xmax, ylim):
    """zone: list of 1-based AP/RSU index (int) per row."""
    for x0, x1, z in zone_spans(x, zone, xmax):
        color, alpha = ZONE_BAND.get(z, ('#aaaaaa', 0.07))
        ax.axvspan(x0, x1, color=color, alpha=alpha, zorder=0, linewidth=0)


def handover_xs(x, handover):
    return [x[i] for i in range(len(x)) if int(handover[i])]


def add_handover_lines(ax, ho_xs, ylim):
    for xh in ho_xs:
        ax.axvline(xh, color=C_HO, lw=1.1, ls='--', alpha=0.7, zorder=3)


def rebuffer_pct(cum_stall_s, total_t):
    return 100.0 * cum_stall_s / total_t if total_t > 0 else 0.0


def outage_pct(cum_outage_s, total_t):
    return 100.0 * cum_outage_s / total_t if total_t > 0 else 0.0


def summary_box(ax, lines, loc='lower right'):
    """Small boxed text annotation for the run-level scalars that don't
    make sense as a per-tick line (Net QoE, handover count, rebuffer%,
    outage%) -- same convention comparison_baseline.py already used for
    Net QoE alone, just generalized to more lines."""
    xy = {'lower right': (0.99, 0.03), 'upper right': (0.99, 0.97),
          'lower left': (0.01, 0.03)}[loc]
    va = 'bottom' if 'lower' in loc else 'top'
    ha = 'right' if 'right' in loc else 'left'
    ax.text(xy[0], xy[1], '\n'.join(lines), transform=ax.transAxes,
             ha=ha, va=va, fontsize=8, color='#1a1a1a',
             bbox=dict(boxstyle='round,pad=0.35', facecolor='white',
                       edgecolor='#cccccc', alpha=0.9))
