#!/usr/bin/env python3
"""
plot_multi_car_detail.py — Situation 1 (Traffic Density) SDN+CDN, side-by-side
detail view

Same 6-panel-per-run style as CDN_baseline/plot_comparison.py (QoE / Latency /
RSSI / Bandwidth / Loss / Cache), but with 3 / 5 / 7-car cases as side-by-side
COLUMNS instead of one run per figure, so the shape change as density
increases is directly visible in one image. X-axis is TIME, not position --
see the comment at the 't' column load below for why (this scenario drives a
loop route, unlike plot_comparison.py's straight-line single-vehicle case).

Uses car1 (the platoon's lead vehicle) from each case as the representative
vehicle -- same "single series" convention as plot_comparison.py, and a fair
like-for-like pick since it's the same platoon position (index 0) in every
case. QoE is computed post-hoc via baseline_model.compute_cdn_qoe(), same
"raw signals only in the CSV" convention as the rest of this project.

Usage:
    python3 plot_multi_car_detail.py                     # auto-pick latest run per car count
    python3 plot_multi_car_detail.py --run3 <id> --run5 <id> --run7 <id>
    python3 plot_multi_car_detail.py --car 2              # use carN instead of car1
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
C_SDN = "#1baf7a"
C_HO = "#e89c00"
C_HIT, C_MISS, C_UNK = "#0ca30c", "#e34948", "#888888"
SDN_DOT = {'HIT': C_HIT, 'MISS': C_MISS, 'UNKNOWN': C_UNK}
CV_MAP = {'HIT': 1, 'MISS': 0, 'UNKNOWN': 0.5}

RSU_BAND = {
    'rsu1': ('#2a78d6', 0.08),
    'rsu2': ('#1baf7a', 0.08),
    'rsu3': ('#eda100', 0.09),
    'rsu4': ('#e34948', 0.08),
}


# ── discovery (same as plot_multi_car.py) ─────────────────────────────────
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


# ── AP/RSU-band + handover-line helpers (ported from plot_comparison.py,
#    'ap' column -> 'rsu' column) ──────────────────────────────────────────
def zone_transitions(x, rsu):
    return [
        (x[i], f"{rsu[i-1].upper()}→{rsu[i].upper()}")
        for i in range(1, len(rsu)) if rsu[i] != rsu[i-1]
    ]


def zone_spans(x, rsu, xmax):
    spans = []
    i = 0
    while i < len(x):
        a = rsu[i]; j = i
        while j < len(x) and rsu[j] == a:
            j += 1
        x_end = x[j] if j < len(x) else xmax
        spans.append((x[i], x_end, a))
        i = j
    return spans


def add_rsu_bands(ax, x, rsu, xmax, ylim, show_label=True):
    # With only 9-18 samples/vehicle (5/7-car cases), zone spans can be very
    # narrow AND the same RSU can recur a few spans later (car briefly
    # bounces back), so a plain per-span text label produces overlapping
    # garbage (e.g. "RSU1RSU1", glyphs merging into "FSU2"). Two guards:
    # (1) only label a span if it's wide enough to hold readable text, and
    # (2) track the last label's x-position and skip any new label that
    # would land too close to it, even if it's a different RSU. The
    # background colour band itself still marks every zone regardless of
    # width or label -- only the text is thinned out.
    xrange = (xmax - min(x)) or 1
    last_label_x = None
    for x0, x1, a in zone_spans(x, rsu, xmax):
        color, alpha = RSU_BAND.get(a, ('#aaaaaa', 0.07))
        ax.axvspan(x0, x1, color=color, alpha=alpha, zorder=0, linewidth=0)
        if not show_label:
            continue
        cx = (x0 + x1) / 2
        wide_enough = (x1 - x0) / xrange > 0.04
        far_enough = last_label_x is None or (cx - last_label_x) / xrange > 0.08
        if wide_enough and far_enough:
            ax.text(cx, ylim[0] + (ylim[1] - ylim[0]) * 0.03,
                    a.upper(), ha='center', va='bottom',
                    fontsize=6.5, color=color, alpha=0.85, zorder=1)
            last_label_x = cx


def add_handover_lines(ax, trans, ylim, x_offset=6, show_label=True):
    for x_ho, lbl in trans:
        ax.axvline(x_ho, color=C_HO, lw=1.0, ls='--', alpha=0.7, zorder=3)
        if show_label:
            ax.text(x_ho + x_offset, ylim[1] * 0.97, lbl, rotation=90,
                    va='top', ha='left', fontsize=6, color=C_HO, alpha=0.9)


# ── style ──────────────────────────────────────────────────────────────
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


def make_plot(run_dirs, car_idx, out_path):
    cars_present = [n for n in CAR_COUNTS if run_dirs.get(n)]
    if not cars_present:
        print('[ERROR] no runs found for any car count -- nothing to plot')
        return

    fig, axes = plt.subplots(6, len(cars_present),
                              figsize=(6.2 * len(cars_present), 17.5),
                              facecolor='white', squeeze=False)
    fig.subplots_adjust(hspace=0.6, wspace=0.28, top=0.90)
    fig.suptitle(
        f'Situation 1: Traffic Density — SDN+CDN, car{car_idx} '
        f'(3 / 5 / 7 cars side-by-side)',
        fontsize=13, fontweight='bold', color='#1a1a1a', y=0.995)

    row_titles = ['Quality of Experience (QoE)', 'CDN Latency', 'RSSI',
                  'Imposed Bandwidth (step2h)', 'Packet Loss', 'Cache HIT/MISS']

    for col_i, n in enumerate(cars_present):
        run_dir = run_dirs[n]
        matches = glob.glob(os.path.join(run_dir, f'*_car{car_idx}_network.csv'))
        if not matches:
            print(f'  [WARN] no car{car_idx} CSV in {run_dir} -- skipping column')
            continue
        rows = load_csv(matches[0])

        # Time (t), NOT position (x) -- unlike CDN_baseline/plot_comparison.py's
        # straight-line single-vehicle scenario (where x is monotonic, so
        # plotting against it is safe), this platoon drives a REAL LOOP route.
        # x/y both double back on themselves over one lap, so a line plotted
        # against raw x connects points out of physical order wherever the
        # route revisits a similar x at a different point in the loop --
        # exactly the crossing/zigzag mess this was showing. Time is
        # monotonic by construction (t = time.time() - t0, ever-increasing),
        # matching Situation1_DASH's own plot_smoke_run_v2.py convention
        # (its "tau (s)" x-axis) for the same reason.
        x = col(rows, 't')
        qoe = M.compute_cdn_qoe(rows)
        lat = col(rows, 'latency_s')
        rssi = col(rows, 'rssi_dbm')
        bw = col(rows, 'bw_mbps')
        loss = col(rows, 'loss_pct')
        rsu = col(rows, 'rsu', str)
        cache = col(rows, 'cache', str)
        xmin, xmax = min(x), max(x)
        trans = zone_transitions(x, rsu)
        net_qoe = sum(qoe)

        col_title = f'{n} cars  (n={len(rows)} samples)'
        axes[0, col_i].annotate(
            col_title, xy=(0.5, 1.22), xycoords='axes fraction',
            ha='center', va='bottom', fontsize=11, fontweight='bold',
            color=COLORS.get(n, '#333333'))

        # 1. QoE
        ax = axes[0, col_i]
        ax.plot(x, qoe, color=C_SDN, lw=1.8, marker='o', markersize=3, zorder=4)
        ax.fill_between(x, qoe, alpha=0.15, color=C_SDN)
        ax.set_xlim(xmin, xmax)
        ax.set_ylabel('QoE', fontsize=9)
        ax.set_title(row_titles[0], fontsize=9.5, fontweight='semibold', pad=4)
        ax.text(0.99, 0.06,
                f'Net={net_qoe:.1f} (avg {net_qoe/len(qoe):.3f})',
                transform=ax.transAxes, ha='right', va='bottom', fontsize=7.5,
                fontweight='semibold',
                bbox=dict(boxstyle='round,pad=0.25', facecolor='white',
                          edgecolor='#cccccc', alpha=0.85))
        add_rsu_bands(ax, x, rsu, xmax, ax.get_ylim())
        add_handover_lines(ax, trans, ax.get_ylim())

        # 2. Latency
        ax = axes[1, col_i]
        ax.plot(x, lat, color=C_SDN, lw=1.6, marker='o', markersize=3, zorder=4)
        ax.fill_between(x, lat, alpha=0.12, color=C_SDN)
        ax.set_ylim(0, 3.5); ax.set_xlim(xmin, xmax)
        ax.set_ylabel('Latency (s)', fontsize=9)
        ax.set_title(row_titles[1], fontsize=9.5, fontweight='semibold', pad=4)
        add_rsu_bands(ax, x, rsu, xmax, (0, 3.5), show_label=False)
        add_handover_lines(ax, trans, (0, 3.5), show_label=False)

        # 3. RSSI
        ax = axes[2, col_i]
        ax.plot(x, rssi, color=C_SDN, lw=1.4, zorder=3, alpha=0.6)
        ax.scatter(x, rssi, s=16, color=C_SDN, zorder=5, marker='o', edgecolors='none')
        rssi_ylim = (min(rssi) - 5, max(rssi) + 5)
        ax.set_ylim(*rssi_ylim); ax.set_xlim(xmin, xmax)
        ax.set_ylabel('RSSI (dBm)', fontsize=9)
        ax.set_title(row_titles[2], fontsize=9.5, fontweight='semibold', pad=4)
        add_rsu_bands(ax, x, rsu, xmax, rssi_ylim, show_label=False)
        add_handover_lines(ax, trans, rssi_ylim, show_label=False)

        # 4. Bandwidth (step plot -- discretised by Step2HysteresisMapper)
        ax = axes[3, col_i]
        bw_max = max(max(bw) * 1.15, 1)
        ax.step(x, bw, color=C_SDN, lw=1.8, where='post', zorder=4)
        ax.fill_between(x, bw, step='post', alpha=0.15, color=C_SDN)
        ax.set_ylim(0, bw_max); ax.set_xlim(xmin, xmax)
        ax.set_ylabel('Bandwidth (Mbps)', fontsize=9)
        ax.set_title(row_titles[3], fontsize=9.5, fontweight='semibold', pad=4)
        add_rsu_bands(ax, x, rsu, xmax, (0, bw_max), show_label=False)
        add_handover_lines(ax, trans, (0, bw_max), show_label=False)

        # 5. Packet loss
        ax = axes[4, col_i]
        ax.plot(x, loss, color=C_SDN, lw=1.8, marker='o', markersize=3, zorder=4)
        ax.fill_between(x, loss, alpha=0.15, color=C_SDN)
        loss_max = max(max(loss) * 1.2, 5)
        ax.set_ylim(0, loss_max); ax.set_xlim(xmin, xmax)
        ax.set_ylabel('Loss (%)', fontsize=9)
        ax.set_title(row_titles[4], fontsize=9.5, fontweight='semibold', pad=4)
        add_rsu_bands(ax, x, rsu, xmax, (0, loss_max), show_label=False)
        add_handover_lines(ax, trans, (0, loss_max), show_label=False)

        # 6. Cache
        ax = axes[5, col_i]
        cv = [CV_MAP.get(c, 0.5) for c in cache]
        ax.step(x, cv, color=C_SDN, lw=1.0, where='post', alpha=0.35, zorder=2)
        for x2, v2, c2 in zip(x, cv, cache):
            ax.scatter(x2, v2, s=30, marker='s', color=SDN_DOT[c2],
                       edgecolors='none', zorder=5)
        ax.set_ylim(-0.4, 1.4); ax.set_xlim(xmin, xmax)
        ax.set_yticks([0, 0.5, 1])
        ax.set_yticklabels(['MISS', 'UNK', 'HIT'], fontsize=8)
        ax.set_ylabel('Cache', fontsize=9)
        ax.set_xlabel('Time (s)', fontsize=9)
        ax.set_title(row_titles[5], fontsize=9.5, fontweight='semibold', pad=4)
        add_rsu_bands(ax, x, rsu, xmax, (-0.4, 1.4), show_label=False)
        add_handover_lines(ax, trans, (-0.4, 1.4), show_label=False)
        if col_i == len(cars_present) - 1:
            leg = [
                mlines.Line2D([], [], marker='s', ls='', color=C_HIT,
                              markerfacecolor=C_HIT, markersize=5, label='HIT'),
                mpatches.Patch(color=C_MISS, label='MISS'),
                mpatches.Patch(color=C_UNK, label='UNK'),
            ]
            ax.legend(handles=leg, loc='upper right', fontsize=6.5,
                      handlelength=0.7, handletextpad=0.3, borderpad=0.35,
                      labelspacing=0.15, framealpha=0.75, ncol=3, markerscale=0.8)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    print(f'  saved -> {out_path}')
    plt.close(fig)


COLORS = {3: '#1baf7a', 5: '#eda100', 7: '#e34948'}


if __name__ == '__main__':
    p = argparse.ArgumentParser(
        description='Situation 1 side-by-side (3/5/7 cars) detail plot')
    p.add_argument('--run3', type=str, default=None)
    p.add_argument('--run5', type=str, default=None)
    p.add_argument('--run7', type=str, default=None)
    p.add_argument('--car', type=int, default=1,
                    help='which vehicle (carN) to plot from each case (default 1)')
    p.add_argument('--out', type=str,
                    default=os.path.join(_HERE, 'plots', 'multi_car_detail.png'))
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

    make_plot(run_dirs, args.car, args.out)
    print('\nDone.')
