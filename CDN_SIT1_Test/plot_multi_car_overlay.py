#!/usr/bin/env python3
"""
plot_multi_car_overlay.py — Situation 1 (Traffic Density) SDN+CDN, per-case
vehicle-overlay view

Produces ONE figure PER car count (3 separate PNGs for the 3/5/7-car cases,
not one combined figure) -- inside each figure, every vehicle in that case
(car1..carN) is overlaid as its own coloured line on the same panels
(RSSI / Bandwidth / Loss / QoE / Latency / Cache), all vs. TIME (same reason
as plot_multi_car_detail.py: this is a loop route, position (x) is not
monotonic so a position axis makes the lines cross/zigzag).

This is the "can I see vehicles actually contending for the same RSU's
bandwidth at the same time" view -- plot_multi_car_detail.py (car1 only,
3/5/7 side by side) answers a different question (how does ONE vehicle's
experience change as density increases), plot_multi_car.py answers a third
(run-level averages). All three read the same raw CSVs, just sliced/
aggregated differently -- no new data collection needed.

RSU background bands are deliberately NOT drawn here (unlike
plot_multi_car_detail.py): each vehicle has its own handover schedule
(staggered by the platoon's 10 m gap), so N vehicles' zone bands would
overlap into unreadable stripes. The vertical dashed handover lines are also
per-vehicle here -- shown in the same colour as that vehicle's line, so a
simultaneous dip in the Bandwidth panel across several cars' lines that also
lines up with several same-coloured handover ticks is the contention signal
this plot exists to show.

Usage:
    python3 plot_multi_car_overlay.py                     # all 3 cases found, auto-latest
    python3 plot_multi_car_overlay.py --run3 <id> --run5 <id> --run7 <id>
    python3 plot_multi_car_overlay.py --cars 5             # just the 5-car case
"""
import csv, os, sys, glob, argparse
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
CAR_COLORS = ['#1482c5', '#e67e22', '#27ae60', '#c0392b',
              '#8e44ad', '#16a085', '#e84393']
CV_MAP = {'HIT': 1, 'MISS': 0, 'UNKNOWN': 0.5}


# ── discovery (same convention as the other plot_multi_car*.py scripts) ──
def find_latest_run(n_cars):
    candidates = [
        d for d in glob.glob(os.path.join(RESULT_ROOT, f'*{n_cars}cars*'))
        if os.path.isdir(d)
    ]
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def load_csv(path):
    with open(path) as f:
        return list(csv.DictReader(f))


def col(rows, key, cast=float):
    return [cast(r[key]) for r in rows]


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


def make_case_plot(n_cars, run_dir, out_path):
    per_car = []
    for i in range(1, n_cars + 1):
        matches = glob.glob(os.path.join(run_dir, f'*_car{i}_network.csv'))
        if not matches:
            print(f'  [WARN] no car{i} CSV in {run_dir}')
            continue
        per_car.append((i, load_csv(matches[0])))
    if not per_car:
        print(f'  [WARN] nothing to plot for {n_cars} cars')
        return

    fig, axes = plt.subplots(6, 1, figsize=(13, 17.5), facecolor='white')
    fig.subplots_adjust(hspace=0.45, top=0.94)
    fig.suptitle(
        f'Situation 1: Traffic Density — SDN+CDN, {n_cars} cars '
        f'(all vehicles overlaid)',
        fontsize=13, fontweight='bold', color='#1a1a1a', y=0.975)

    all_t = [float(r['t']) for _, rows in per_car for r in rows]
    tmin, tmax = min(all_t), max(all_t)

    ax_qoe, ax_lat, ax_rssi, ax_bw, ax_loss, ax_cache = axes

    net_qoe_total = 0.0
    n_samples_total = 0

    for i, rows in per_car:
        color = CAR_COLORS[(i - 1) % len(CAR_COLORS)]
        label = f'car{i}'
        t = col(rows, 't')
        qoe = M.compute_cdn_qoe(rows)
        lat = col(rows, 'latency_s')
        rssi = col(rows, 'rssi_dbm')
        bw = col(rows, 'bw_mbps')
        loss = col(rows, 'loss_pct')
        cache = col(rows, 'cache', str)
        rsu = col(rows, 'rsu', str)
        handover_t = [t[k] for k in range(1, len(t)) if rsu[k] != rsu[k - 1]]

        net_qoe_total += sum(qoe)
        n_samples_total += len(qoe)

        ax_qoe.plot(t, qoe, color=color, lw=1.5, marker='o', markersize=2.5,
                    alpha=0.85, label=label, zorder=4)
        ax_lat.plot(t, lat, color=color, lw=1.3, marker='o', markersize=2.5,
                    alpha=0.85, zorder=4)
        ax_rssi.plot(t, rssi, color=color, lw=1.3, marker='o', markersize=2.5,
                     alpha=0.85, zorder=4)
        ax_bw.step(t, bw, color=color, lw=1.6, where='post', alpha=0.85, zorder=4)
        ax_loss.plot(t, loss, color=color, lw=1.3, marker='o', markersize=2.5,
                     alpha=0.85, zorder=4)
        cv = [CV_MAP.get(c, 0.5) for c in cache]
        ax_cache.step(t, cv, color=color, lw=1.0, where='post', alpha=0.5, zorder=2)
        ax_cache.scatter(t, cv, s=16, color=color, alpha=0.85, zorder=5,
                          edgecolors='none')

        # Per-vehicle handover ticks, same colour as that vehicle's line --
        # several same-coloured ticks lining up across different vehicles at
        # the same time, coinciding with a simultaneous Bandwidth dip, is the
        # "contending for the same RSU" signal this plot is for.
        for ax in (ax_qoe, ax_lat, ax_rssi, ax_bw, ax_loss, ax_cache):
            for xt in handover_t:
                ax.axvline(xt, color=color, lw=0.6, ls=':', alpha=0.35, zorder=1)

    for ax, title, ylabel in [
        (ax_qoe, 'Quality of Experience (QoE)', 'QoE'),
        (ax_lat, 'CDN Latency', 'Latency (s)'),
        (ax_rssi, 'RSSI', 'RSSI (dBm)'),
        (ax_bw, 'Allocated Bandwidth (step2h + contention)', 'Bandwidth (Mbps)'),
        (ax_loss, 'Packet Loss', 'Loss (%)'),
        (ax_cache, 'Cache HIT/MISS', 'Cache'),
    ]:
        ax.set_xlim(tmin, tmax)
        ax.set_title(title, fontsize=10, fontweight='semibold', pad=4)
        ax.set_ylabel(ylabel, fontsize=9.5)

    ax_lat.set_ylim(0, 3.5)
    ax_loss.set_ylim(bottom=0)
    ax_cache.set_ylim(-0.4, 1.4)
    ax_cache.set_yticks([0, 0.5, 1])
    ax_cache.set_yticklabels(['MISS', 'UNK', 'HIT'], fontsize=8.5)
    ax_cache.set_xlabel('Time (s)', fontsize=9.5)

    avg_qoe = net_qoe_total / n_samples_total if n_samples_total else 0.0
    ax_qoe.text(
        0.99, 0.06,
        f'Net QoE (all vehicles) = {net_qoe_total:.1f}  '
        f'({n_samples_total} samples, avg {avg_qoe:.3f})',
        transform=ax_qoe.transAxes, ha='right', va='bottom', fontsize=8.5,
        fontweight='semibold',
        bbox=dict(boxstyle='round,pad=0.3', facecolor='white',
                  edgecolor='#cccccc', alpha=0.85))

    ax_qoe.legend(loc='upper left', fontsize=8, ncol=min(n_cars, 4),
                  framealpha=0.85)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    print(f'  saved -> {out_path}')
    plt.close(fig)


if __name__ == '__main__':
    p = argparse.ArgumentParser(
        description='Situation 1 per-case (all vehicles overlaid) plots')
    p.add_argument('--run3', type=str, default=None)
    p.add_argument('--run5', type=str, default=None)
    p.add_argument('--run7', type=str, default=None)
    p.add_argument('--cars', type=int, choices=CAR_COUNTS, default=None,
                    help='only plot this one case (default: all found)')
    p.add_argument('--out-dir', type=str, default=os.path.join(_HERE, 'plots'))
    args = p.parse_args()

    explicit = {3: args.run3, 5: args.run5, 7: args.run7}
    targets = [args.cars] if args.cars else CAR_COUNTS

    for n in targets:
        run_dir = (os.path.join(RESULT_ROOT, explicit[n]) if explicit[n]
                   else find_latest_run(n))
        if run_dir is None or not os.path.isdir(run_dir):
            print(f'[WARN] no run found for {n} cars')
            continue
        print(f'[{n} cars] loading {run_dir}')
        out_path = os.path.join(args.out_dir, f'multi_car_overlay_{n}cars.png')
        make_case_plot(n, run_dir, out_path)

    print('\nDone.')
