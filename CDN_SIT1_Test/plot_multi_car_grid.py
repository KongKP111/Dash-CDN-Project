#!/usr/bin/env python3
"""
plot_multi_car_grid.py — Situation 1 (Traffic Density) SDN+CDN, combined
grid view

Merges the two other multi-car plots into one figure: 3 columns (3 / 5 / 7
cars, like plot_multi_car_detail.py's layout) x 6 metric rows (QoE / Latency
/ RSSI / Bandwidth / Loss / Cache), but EVERY vehicle in each case is
overlaid in its own column (like plot_multi_car_overlay.py's content),
instead of just car1. This is the single most information-dense view: read
down a column to see one density case's full contention picture, read
across a row to see how that metric's contention pattern changes as density
increases.

X-axis is TIME, not position (same reason as the other two scripts: this is
a loop route, so position is not monotonic and would make the lines cross).
RSU background zone bands are NOT drawn (same reason as
plot_multi_car_overlay.py: each vehicle has its own handover schedule, so
one shared zone-band overlay would be misleading/cluttered for N vehicles) --
per-vehicle handover ticks (thin dotted lines, vehicle's own colour) are
used instead.

Usage:
    python3 plot_multi_car_grid.py                     # auto-pick latest run per car count
    python3 plot_multi_car_grid.py --run3 <id> --run5 <id> --run7 <id>
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
CASE_TITLE_COLOR = {3: '#1baf7a', 5: '#eda100', 7: '#e34948'}
CAR_COLORS = ['#1482c5', '#e67e22', '#27ae60', '#c0392b',
              '#8e44ad', '#16a085', '#e84393']
CV_MAP = {'HIT': 1, 'MISS': 0, 'UNKNOWN': 0.5}


def find_latest_run(n_cars):
    candidates = [
        d for d in glob.glob(os.path.join(RESULT_ROOT, f'*{n_cars}cars*'))
        if os.path.isdir(d)
    ]
    return max(candidates, key=os.path.getmtime) if candidates else None


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

ROW_TITLES = ['Quality of Experience (QoE)', 'CDN Latency', 'RSSI',
              'Allocated Bandwidth (step2h + contention)', 'Packet Loss',
              'Cache HIT/MISS']
ROW_YLABELS = ['QoE', 'Latency (s)', 'RSSI (dBm)', 'Bandwidth (Mbps)',
               'Loss (%)', 'Cache']


def make_plot(run_dirs, out_path):
    cars_present = [n for n in CAR_COUNTS if run_dirs.get(n)]
    if not cars_present:
        print('[ERROR] no runs found for any car count -- nothing to plot')
        return

    fig, axes = plt.subplots(6, len(cars_present),
                              figsize=(6.5 * len(cars_present), 17.5),
                              facecolor='white', squeeze=False)
    fig.subplots_adjust(hspace=0.55, wspace=0.25, top=0.90)
    fig.suptitle(
        'Situation 1: Traffic Density — SDN+CDN, all vehicles overlaid '
        '(3 / 5 / 7 cars side-by-side)',
        fontsize=13, fontweight='bold', color='#1a1a1a', y=0.995)

    for col_i, n in enumerate(cars_present):
        run_dir = run_dirs[n]
        per_car = []
        for i in range(1, n + 1):
            matches = glob.glob(os.path.join(run_dir, f'*_car{i}_network.csv'))
            if not matches:
                print(f'  [WARN] no car{i} CSV in {run_dir}')
                continue
            per_car.append((i, load_csv(matches[0])))
        if not per_car:
            continue

        all_t = [float(r['t']) for _, rows in per_car for r in rows]
        tmin, tmax = min(all_t), max(all_t)
        n_samples_total = sum(len(rows) for _, rows in per_car)

        col_title = f'{n} cars  (n={n_samples_total} samples total)'
        axes[0, col_i].annotate(
            col_title, xy=(0.5, 1.24), xycoords='axes fraction',
            ha='center', va='bottom', fontsize=11.5, fontweight='bold',
            color=CASE_TITLE_COLOR.get(n, '#333333'))

        ax_qoe, ax_lat, ax_rssi, ax_bw, ax_loss, ax_cache = axes[:, col_i]
        net_qoe_total = 0.0

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

            ax_qoe.plot(t, qoe, color=color, lw=1.4, marker='o', markersize=2.3,
                        alpha=0.85, label=label, zorder=4)
            ax_lat.plot(t, lat, color=color, lw=1.2, marker='o', markersize=2.3,
                        alpha=0.85, zorder=4)
            ax_rssi.plot(t, rssi, color=color, lw=1.2, marker='o', markersize=2.3,
                         alpha=0.85, zorder=4)
            ax_bw.step(t, bw, color=color, lw=1.4, where='post', alpha=0.85, zorder=4)
            ax_loss.plot(t, loss, color=color, lw=1.2, marker='o', markersize=2.3,
                         alpha=0.85, zorder=4)
            cv = [CV_MAP.get(c, 0.5) for c in cache]
            ax_cache.step(t, cv, color=color, lw=0.9, where='post', alpha=0.5, zorder=2)
            ax_cache.scatter(t, cv, s=13, color=color, alpha=0.85, zorder=5,
                              edgecolors='none')

            for ax in (ax_qoe, ax_lat, ax_rssi, ax_bw, ax_loss, ax_cache):
                for xt in handover_t:
                    ax.axvline(xt, color=color, lw=0.5, ls=':', alpha=0.3, zorder=1)

        for row_i, ax in enumerate((ax_qoe, ax_lat, ax_rssi, ax_bw, ax_loss, ax_cache)):
            ax.set_xlim(tmin, tmax)
            ax.set_title(ROW_TITLES[row_i], fontsize=9.5, fontweight='semibold', pad=4)
            if col_i == 0:
                ax.set_ylabel(ROW_YLABELS[row_i], fontsize=9)

        ax_lat.set_ylim(0, 3.5)
        ax_loss.set_ylim(bottom=0)
        ax_cache.set_ylim(-0.4, 1.4)
        ax_cache.set_yticks([0, 0.5, 1])
        ax_cache.set_yticklabels(['MISS', 'UNK', 'HIT'], fontsize=8)
        ax_cache.set_xlabel('Time (s)', fontsize=9)

        avg_qoe = net_qoe_total / n_samples_total if n_samples_total else 0.0
        ax_qoe.text(
            0.99, 0.06, f'Net={net_qoe_total:.1f} (avg {avg_qoe:.3f})',
            transform=ax_qoe.transAxes, ha='right', va='bottom', fontsize=7.5,
            fontweight='semibold',
            bbox=dict(boxstyle='round,pad=0.25', facecolor='white',
                      edgecolor='#cccccc', alpha=0.85))
        ax_qoe.legend(loc='upper left', fontsize=6.8, ncol=min(n, 4),
                      framealpha=0.85, handlelength=1.2, columnspacing=0.8)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    print(f'  saved -> {out_path}')
    plt.close(fig)


if __name__ == '__main__':
    p = argparse.ArgumentParser(
        description='Situation 1 combined grid (3/5/7 cars x all vehicles overlaid)')
    p.add_argument('--run3', type=str, default=None)
    p.add_argument('--run5', type=str, default=None)
    p.add_argument('--run7', type=str, default=None)
    p.add_argument('--out', type=str,
                    default=os.path.join(_HERE, 'plots', 'multi_car_grid.png'))
    args = p.parse_args()

    explicit = {3: args.run3, 5: args.run5, 7: args.run7}
    run_dirs = {}
    for n in CAR_COUNTS:
        run_dir = (os.path.join(RESULT_ROOT, explicit[n]) if explicit[n]
                   else find_latest_run(n))
        if run_dir is None or not os.path.isdir(run_dir):
            print(f'[WARN] no run found for {n} cars')
            continue
        run_dirs[n] = run_dir

    make_plot(run_dirs, args.out)
    print('\nDone.')
