#!/usr/bin/env python3
"""
plot_multi_car_percar.py — Situation 1 (Traffic Density) SDN+CDN, per-vehicle
columns

One figure PER car-count case (3 separate PNGs for 3/5/7 cars, same as
plot_multi_car_overlay.py) -- but unlike that script, vehicles are NOT
overlaid on top of each other. Instead each vehicle gets its OWN column,
same 6-panel-per-vehicle style as plot_comparison.py (QoE / Latency / RSSI /
Bandwidth / Loss / Cache, all vs. TIME -- see plot_multi_car_detail.py's
comment for why time, not position: this is a loop route). So the 5-car
case's figure is 6 rows x 5 columns (one per vehicle), the 7-car case's is
6 rows x 7 columns, etc. -- images get wide, not tall, which is fine.

Because each column is exactly one vehicle now (not several averaged/
overlaid together), RSU zone background bands and handover labels are
meaningful again per column (each vehicle has its own real handover
schedule) -- reusing the same zone-band/handover-label helpers as
plot_multi_car_detail.py.

Usage:
    python3 plot_multi_car_percar.py                     # all 3 cases found, auto-latest
    python3 plot_multi_car_percar.py --run3 <id> --run5 <id> --run7 <id>
    python3 plot_multi_car_percar.py --cars 5             # just the 5-car case
"""
import csv, os, sys, glob, argparse
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.lines as mlines
import matplotlib.patches as mpatches

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, '..'))
RESULT_ROOT = os.path.join(_HERE, 'result_multi_car')

sys.path.insert(0, os.path.join(_REPO_ROOT, 'CDN_baseline'))
import baseline_model as M

CAR_COUNTS = [3, 5, 7]
CASE_COLOR = {3: '#1baf7a', 5: '#eda100', 7: '#e34948'}
C_SDN = "#1baf7a"
C_HO = "#e89c00"
C_HIT, C_MISS, C_UNK = "#0ca30c", "#e34948", "#888888"
# 'LOSS' replaces the old 'UNKNOWN' tier (cdn_sdn_multi_car.py's outage
# tracking) -- cache HIT/MISS is strictly an edge-content question; a
# request that got no answer at all (outage or a timed-out probe) is a
# connection LOSS, not a third cache state. 'UNKNOWN' kept as a fallback key
# too for any older CSV that still has literal 'UNKNOWN' rows.
SDN_DOT = {'HIT': C_HIT, 'MISS': C_MISS, 'LOSS': C_UNK, 'UNKNOWN': C_UNK}
CV_MAP = {'HIT': 1, 'MISS': 0, 'LOSS': 0.5, 'UNKNOWN': 0.5}

RSU_BAND = {
    'rsu1': ('#2a78d6', 0.08),
    'rsu2': ('#1baf7a', 0.08),
    'rsu3': ('#eda100', 0.09),
    'rsu4': ('#e34948', 0.08),
}


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


def zone_transitions(t, rsu):
    return [
        (t[i], f"{rsu[i-1].upper()}→{rsu[i].upper()}")
        for i in range(1, len(rsu)) if rsu[i] != rsu[i-1]
    ]


def zone_spans(t, rsu, tmax):
    spans = []
    i = 0
    while i < len(t):
        a = rsu[i]; j = i
        while j < len(t) and rsu[j] == a:
            j += 1
        t_end = t[j] if j < len(t) else tmax
        spans.append((t[i], t_end, a))
        i = j
    return spans


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
            ax.text(ct, ylim[0] + (ylim[1] - ylim[0]) * 0.03,
                    a.upper(), ha='center', va='bottom',
                    fontsize=6.5, color=color, alpha=0.85, zorder=1)
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

ROW_TITLES = ['Quality of Experience (QoE)', 'CDN Latency', 'RSSI',
              'Imposed Bandwidth (step2h)', 'Packet Loss', 'Cache HIT/MISS']


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

    fig, axes = plt.subplots(6, len(per_car),
                              figsize=(4.3 * len(per_car), 17.5),
                              facecolor='white', squeeze=False)
    fig.subplots_adjust(hspace=0.6, wspace=0.32, top=0.90)
    fig.suptitle(
        f'Situation 1: Traffic Density — SDN+CDN, {n_cars} cars '
        f'(one column per vehicle)',
        fontsize=13, fontweight='bold', color='#1a1a1a', y=0.995)

    for col_i, (i, rows) in enumerate(per_car):
        t = col(rows, 't')
        qoe = M.compute_cdn_qoe(rows)
        lat = col(rows, 'latency_s')
        rssi = col(rows, 'rssi_dbm')
        bw = col(rows, 'bw_mbps')
        loss = col(rows, 'loss_pct')
        rsu = col(rows, 'rsu', str)
        cache = col(rows, 'cache', str)
        tmin, tmax = min(t), max(t)
        trans = zone_transitions(t, rsu)
        net_qoe = sum(qoe)

        col_title = f'car{i}  (n={len(rows)})'
        axes[0, col_i].annotate(
            col_title, xy=(0.5, 1.22), xycoords='axes fraction',
            ha='center', va='bottom', fontsize=11, fontweight='bold',
            color=CASE_COLOR.get(n_cars, '#333333'))

        # 1. QoE
        ax = axes[0, col_i]
        ax.plot(t, qoe, color=C_SDN, lw=1.8, marker='o', markersize=3, zorder=4)
        ax.fill_between(t, qoe, alpha=0.15, color=C_SDN)
        ax.set_xlim(tmin, tmax)
        ax.set_xlabel('Time (s)', fontsize=9)
        ax.set_title(ROW_TITLES[0], fontsize=9.5, fontweight='semibold', pad=4)
        ax.set_ylabel('QoE (score)', fontsize=9)
        ax.text(0.99, 0.06, f'Net={net_qoe:.1f} (avg {net_qoe/len(qoe):.3f})',
                transform=ax.transAxes, ha='right', va='bottom', fontsize=7,
                fontweight='semibold',
                bbox=dict(boxstyle='round,pad=0.25', facecolor='white',
                          edgecolor='#cccccc', alpha=0.85))
        add_rsu_bands(ax, t, rsu, tmax, ax.get_ylim())
        add_handover_lines(ax, trans, ax.get_ylim())

        # 2. Latency
        ax = axes[1, col_i]
        ax.plot(t, lat, color=C_SDN, lw=1.6, marker='o', markersize=3, zorder=4)
        ax.fill_between(t, lat, alpha=0.12, color=C_SDN)
        ax.set_ylim(0, 3.5); ax.set_xlim(tmin, tmax)
        ax.set_xlabel('Time (s)', fontsize=9)
        ax.set_title(ROW_TITLES[1], fontsize=9.5, fontweight='semibold', pad=4)
        ax.set_ylabel('Latency (s)', fontsize=9)
        add_rsu_bands(ax, t, rsu, tmax, (0, 3.5), show_label=False)
        add_handover_lines(ax, trans, (0, 3.5), show_label=False)

        # 3. RSSI
        ax = axes[2, col_i]
        ax.plot(t, rssi, color=C_SDN, lw=1.4, zorder=3, alpha=0.6)
        ax.scatter(t, rssi, s=16, color=C_SDN, zorder=5, marker='o', edgecolors='none')
        rssi_ylim = (min(rssi) - 5, max(rssi) + 5)
        ax.set_ylim(*rssi_ylim); ax.set_xlim(tmin, tmax)
        ax.set_xlabel('Time (s)', fontsize=9)
        ax.set_title(ROW_TITLES[2], fontsize=9.5, fontweight='semibold', pad=4)
        ax.set_ylabel('RSSI (dBm)', fontsize=9)
        add_rsu_bands(ax, t, rsu, tmax, rssi_ylim, show_label=False)
        add_handover_lines(ax, trans, rssi_ylim, show_label=False)

        # 4. Bandwidth
        ax = axes[3, col_i]
        bw_max = max(max(bw) * 1.15, 1)
        ax.step(t, bw, color=C_SDN, lw=1.8, where='post', zorder=4)
        ax.fill_between(t, bw, step='post', alpha=0.15, color=C_SDN)
        ax.set_ylim(0, bw_max); ax.set_xlim(tmin, tmax)
        ax.set_xlabel('Time (s)', fontsize=9)
        ax.set_title(ROW_TITLES[3], fontsize=9.5, fontweight='semibold', pad=4)
        ax.set_ylabel('Bandwidth (Mbps)', fontsize=9)
        add_rsu_bands(ax, t, rsu, tmax, (0, bw_max), show_label=False)
        add_handover_lines(ax, trans, (0, bw_max), show_label=False)

        # 5. Packet loss
        ax = axes[4, col_i]
        ax.plot(t, loss, color=C_SDN, lw=1.8, marker='o', markersize=3, zorder=4)
        ax.fill_between(t, loss, alpha=0.15, color=C_SDN)
        loss_max = max(max(loss) * 1.2, 5)
        ax.set_ylim(0, loss_max); ax.set_xlim(tmin, tmax)
        ax.set_xlabel('Time (s)', fontsize=9)
        ax.set_title(ROW_TITLES[4], fontsize=9.5, fontweight='semibold', pad=4)
        ax.set_ylabel('Loss (%)', fontsize=9)
        add_rsu_bands(ax, t, rsu, tmax, (0, loss_max), show_label=False)
        add_handover_lines(ax, trans, (0, loss_max), show_label=False)

        # 6. Cache
        ax = axes[5, col_i]
        cv = [CV_MAP.get(c, 0.5) for c in cache]
        ax.step(t, cv, color=C_SDN, lw=1.0, where='post', alpha=0.35, zorder=2)
        for t2, v2, c2 in zip(t, cv, cache):
            ax.scatter(t2, v2, s=30, marker='s', color=SDN_DOT[c2],
                       edgecolors='none', zorder=5)
        ax.set_ylim(-0.4, 1.4); ax.set_xlim(tmin, tmax)
        ax.set_yticks([0, 0.5, 1])
        ax.set_yticklabels(['MISS', 'LOSS', 'HIT'], fontsize=8)
        ax.set_ylabel('Cache', fontsize=9)
        ax.set_xlabel('Time (s)', fontsize=9)
        ax.set_title(ROW_TITLES[5], fontsize=9.5, fontweight='semibold', pad=4)
        add_rsu_bands(ax, t, rsu, tmax, (-0.4, 1.4), show_label=False)
        add_handover_lines(ax, trans, (-0.4, 1.4), show_label=False)
        if col_i == len(per_car) - 1:
            leg = [
                mlines.Line2D([], [], marker='s', ls='', color=C_HIT,
                              markerfacecolor=C_HIT, markersize=5, label='HIT'),
                mpatches.Patch(color=C_MISS, label='MISS'),
                mpatches.Patch(color=C_UNK, label='LOSS'),
            ]
            ax.legend(handles=leg, loc='upper right', fontsize=6.5,
                      handlelength=0.7, handletextpad=0.3, borderpad=0.35,
                      labelspacing=0.15, framealpha=0.75, ncol=3, markerscale=0.8)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    print(f'  saved -> {out_path}')
    plt.close(fig)


if __name__ == '__main__':
    p = argparse.ArgumentParser(
        description='Situation 1 per-case, one column per vehicle')
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
        out_path = os.path.join(args.out_dir, f'multi_car_percar_{n}cars.png')
        make_case_plot(n, run_dir, out_path)

    print('\nDone.')
