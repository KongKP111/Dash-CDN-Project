#!/usr/bin/env python3
"""
plot_multi_car.py — Situation 1 (Traffic Density) SDN+CDN scalability report

Loads the per-vehicle network CSVs from one result_multi_car/<run_id>/ folder
per car count (3/5/7), computes a per-case summary (same columns as
Situation1_DASH/README.md's own scalability table, so the two arms are
directly comparable), prints it, and renders a comparison figure of how each
metric degrades as vehicle density increases.

QoE is computed post-hoc from the raw CSV via baseline_model.compute_cdn_qoe()
-- same "raw signals only in the CSV" convention as the single-vehicle
CDN_baseline scripts and plot_comparison.py (a formula change never requires
re-running the mininet-wifi scenario).

Usage:
    python3 plot_multi_car.py                     # auto-pick latest run per car count
    python3 plot_multi_car.py --run3 <run_id> --run5 <run_id> --run7 <run_id>
    python3 plot_multi_car.py --out /custom/path
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
COLORS = {3: '#1baf7a', 5: '#eda100', 7: '#e34948'}


# ── discovery ────────────────────────────────────────────────────────────
def find_latest_run(n_cars):
    """Pick the most-recently-modified result_multi_car/<run_id>/ folder
    whose run_id matches cdn_sdn_{n}cars* or *_{n}cars (covers both the
    auto-timestamped default run_id and a user-supplied --run-id with the
    run_multi_car.sh batch-mode _{n}cars suffix)."""
    candidates = [
        d for d in glob.glob(os.path.join(RESULT_ROOT, f'*{n_cars}cars*'))
        if os.path.isdir(d)
    ]
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def load_case(run_dir, n_cars):
    """Load every carN_network.csv in run_dir. Returns list of per-car row
    lists (list[list[dict]])."""
    per_car = []
    for i in range(1, n_cars + 1):
        matches = glob.glob(os.path.join(run_dir, f'*_car{i}_network.csv'))
        if not matches:
            print(f'  [WARN] no network CSV for car{i} in {run_dir}')
            continue
        with open(matches[0]) as f:
            per_car.append(list(csv.DictReader(f)))
    return per_car


# ── per-case summary (mirrors Situation1_DASH/README.md's table columns) ──
def summarize_case(per_car, total_t):
    """per_car: list of row-lists (one per vehicle). Returns a dict of
    run-level averages, each first averaged within a vehicle then across
    vehicles (so a car with more/fewer samples doesn't get over/under-
    weighted relative to the others)."""
    n_cars = len(per_car)
    per_vehicle = []
    for rows in per_car:
        if not rows:
            continue
        bw = [float(r['bw_mbps']) for r in rows if r['bw_mbps'] != '']
        thr_mbps = [float(r['speed_bps']) / 1e6 for r in rows]
        rssi = [float(r['rssi_dbm']) for r in rows]
        loss = [float(r['loss_pct']) for r in rows]
        hits = sum(1 for r in rows if r['cache'] == 'HIT')
        stall_samples = sum(int(r['stall']) for r in rows)
        cum_stall_s = float(rows[-1]['vlc_cum_stall_s'])
        handovers = sum(int(r['handover']) for r in rows)
        qoe = M.compute_cdn_qoe(rows)
        per_vehicle.append(dict(
            n_samples=len(rows),
            avg_bw_mbps=sum(bw) / len(bw) if bw else 0.0,
            avg_throughput_mbps=sum(thr_mbps) / len(thr_mbps),
            avg_rssi=sum(rssi) / len(rssi),
            avg_loss_pct=sum(loss) / len(loss),
            hit_rate_pct=100.0 * hits / len(rows),
            stall_samples=stall_samples,
            cum_stall_s=cum_stall_s,
            rebuffer_ratio_pct=100.0 * cum_stall_s / total_t,
            handovers=handovers,
            avg_qoe=sum(qoe) / len(qoe),
        ))

    def avg(key):
        return sum(v[key] for v in per_vehicle) / len(per_vehicle)

    return dict(
        n_cars=n_cars,
        avg_samples_per_vehicle=avg('n_samples'),
        avg_bw_mbps=avg('avg_bw_mbps'),
        avg_throughput_mbps=avg('avg_throughput_mbps'),
        avg_rssi=avg('avg_rssi'),
        avg_loss_pct=avg('avg_loss_pct'),
        avg_hit_rate_pct=avg('hit_rate_pct'),
        avg_stall_samples_per_vehicle=avg('stall_samples'),
        avg_stall_dur_s=avg('cum_stall_s'),
        avg_rebuffer_ratio_pct=avg('rebuffer_ratio_pct'),
        avg_handovers_per_vehicle=avg('handovers'),
        avg_qoe_per_sample=avg('avg_qoe'),
        per_vehicle=per_vehicle,
    )


def print_summary_table(summaries):
    print()
    print('=' * 100)
    print('Situation 1: Traffic Density (SDN+CDN) — scalability summary')
    print('=' * 100)
    hdr = (f"{'cars':>4} | {'samples/veh':>11} | {'avg BW':>8} | {'avg thpt':>9} | "
           f"{'avg RSSI':>9} | {'HIT%':>6} | {'loss%':>6} | "
           f"{'stalls/veh':>10} | {'stall dur':>9} | {'rebuf%':>7} | {'QoE/smpl':>9}")
    print(hdr)
    print('-' * len(hdr))
    for n in CAR_COUNTS:
        s = summaries.get(n)
        if s is None:
            print(f"{n:>4} | {'(no run found)':>11}")
            continue
        print(f"{n:>4} | {s['avg_samples_per_vehicle']:>11.1f} | "
              f"{s['avg_bw_mbps']:>6.2f}Mb | {s['avg_throughput_mbps']:>7.2f}Mb | "
              f"{s['avg_rssi']:>7.1f}dB | {s['avg_hit_rate_pct']:>5.1f}% | "
              f"{s['avg_loss_pct']:>5.1f}% | {s['avg_stall_samples_per_vehicle']:>10.2f} | "
              f"{s['avg_stall_dur_s']:>7.2f}s | {s['avg_rebuffer_ratio_pct']:>6.2f}% | "
              f"{s['avg_qoe_per_sample']:>9.3f}")
    print('=' * 100)

    # Sampling-density caveat: the mobility loop measures every vehicle
    # SEQUENTIALLY each tick (real curl/ping/handover work per car, not
    # simulated), so real per-tick duration grows with n_cars -- more cars
    # means fewer, coarser-spaced samples over the SAME fixed lap duration,
    # not a fixed-rate degradation. Flag this explicitly since it directly
    # affects how much to trust the 5/7-car rows above (fewer samples =
    # more likely to miss brief RSSI dips / stall blips between ticks).
    print()
    print('NOTE — sample density drops sharply with car count (all vehicles')
    print('are measured sequentially each tick, so real per-tick duration')
    print('scales with n_cars): '
          + ', '.join(f"{n} cars={summaries[n]['avg_samples_per_vehicle']:.0f} samples/veh"
                       for n in CAR_COUNTS if n in summaries)
          + '. Treat 5/7-car rows as coarser-grained than the 3-car row,')
    print('not necessarily "worse" by the same measurement resolution.')
    print()


# ── comparison figure ───────────────────────────────────────────────────
def make_comparison_plot(summaries, out_path):
    cars_present = [n for n in CAR_COUNTS if n in summaries]
    if not cars_present:
        print('[ERROR] no runs found for any car count -- nothing to plot')
        return

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
    })

    fig, axes = plt.subplots(2, 3, figsize=(15, 8), facecolor='white')
    fig.suptitle('Situation 1: Traffic Density — SDN+CDN scalability '
                  '(3 / 5 / 7 cars)', fontsize=13, fontweight='bold', y=0.98)

    bar_colors = [COLORS[n] for n in cars_present]
    xlabels = [str(n) for n in cars_present]

    def bar_panel(ax, key, title, ylabel, pct=False):
        vals = [summaries[n][key] for n in cars_present]
        ax.bar(xlabels, vals, color=bar_colors, width=0.55)
        for i, v in enumerate(vals):
            ax.text(i, v, (f'{v:.1f}%' if pct else f'{v:.2f}'),
                    ha='center', va='bottom', fontsize=9, fontweight='semibold')
        ax.set_title(title, fontsize=10, fontweight='semibold')
        ax.set_xlabel('Vehicles')
        ax.set_ylabel(ylabel)

    bar_panel(axes[0, 0], 'avg_bw_mbps', 'Avg Allocated Bandwidth', 'Mbps')
    bar_panel(axes[0, 1], 'avg_throughput_mbps', 'Avg Measured Throughput', 'Mbps')
    bar_panel(axes[0, 2], 'avg_hit_rate_pct', 'Avg Cache HIT Rate', '%', pct=True)
    bar_panel(axes[1, 0], 'avg_stall_dur_s', 'Avg Cumulative Stall Duration', 's')
    bar_panel(axes[1, 1], 'avg_rebuffer_ratio_pct', 'Avg Rebuffer Ratio', '%', pct=True)
    bar_panel(axes[1, 2], 'avg_qoe_per_sample', 'Avg QoE per Sample', 'QoE')

    fig.tight_layout(rect=[0, 0, 1, 0.94])
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    print(f'  saved -> {out_path}')
    plt.close(fig)


# ── CLI ──────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    p = argparse.ArgumentParser(description='Situation 1 scalability report/plot')
    p.add_argument('--run3', type=str, default=None, help='run_id for the 3-car case')
    p.add_argument('--run5', type=str, default=None, help='run_id for the 5-car case')
    p.add_argument('--run7', type=str, default=None, help='run_id for the 7-car case')
    p.add_argument('--out', type=str,
                    # NOT under result_multi_car/ -- that tree is root-owned
                    # (created by the sudo mininet run), so a normal-user
                    # run of this script can read the CSVs there (world-
                    # readable) but can't write a new file into it.
                    default=os.path.join(_HERE, 'plots', 'scalability_comparison.png'))
    args = p.parse_args()

    explicit = {3: args.run3, 5: args.run5, 7: args.run7}
    summaries = {}
    for n in CAR_COUNTS:
        run_dir = (os.path.join(RESULT_ROOT, explicit[n]) if explicit[n]
                   else find_latest_run(n))
        if run_dir is None or not os.path.isdir(run_dir):
            print(f'[WARN] no run found for {n} cars (looked for '
                  f'{explicit[n] or f"*{n}cars* under result_multi_car/"})')
            continue
        print(f'[{n} cars] loading {run_dir}')
        per_car = load_case(run_dir, n)
        if not per_car:
            print(f'  [WARN] no per-vehicle CSVs loaded for {n} cars -- skipping')
            continue
        total_t = max(float(r['t']) for rows in per_car for r in rows)
        summaries[n] = summarize_case(per_car, total_t)

    print_summary_table(summaries)
    make_comparison_plot(summaries, args.out)
    print('\nDone.')
