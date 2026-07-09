#!/usr/bin/env python3
"""
plot_smoke_run.py -- quick QoE/bandwidth visual check for one Situation 1
smoke-test run. No sudo needed; reads the already-saved per-vehicle CSVs.

Run: python3 plot_smoke_run.py <run_id> [--dir results_raw/<run_id>]
"""
import os
import sys
import csv
import argparse
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

COLORS = {'car1': '#d62728', 'car2': '#2ca02c', 'car3': '#9467bd'}


def load_segments(path):
    return list(csv.DictReader(open(path))) if os.path.exists(path) else []


def load_network(path):
    return list(csv.DictReader(open(path))) if os.path.exists(path) else []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('run_id')
    ap.add_argument('--dir', default=None)
    ap.add_argument('--cars', type=int, default=3)
    args = ap.parse_args()
    d = args.dir or os.path.join('results_raw', args.run_id)

    fig, axes = plt.subplots(args.cars, 1, figsize=(11, 3.1 * args.cars), sharex=True)
    if args.cars == 1:
        axes = [axes]

    for i in range(args.cars):
        car = f'car{i+1}'
        ax = axes[i]
        seg = load_segments(os.path.join(d, f'{args.run_id}_{car}_segments.csv'))
        net = load_network(os.path.join(d, f'{args.run_id}_{car}_network.csv'))
        color = COLORS.get(car, '#1f77b4')

        if seg:
            ts = [float(r['timestamp']) for r in seg]
            br = [float(r['bitrate_kbps']) / 1000.0 for r in seg]
            ax.step(ts, br, where='post', color=color, linewidth=2,
                    label=f'{car} chosen bitrate (Mbps)')
            for r in seg:
                if float(r['stall_duration_s']) > 0:
                    ax.axvspan(float(r['timestamp']) - float(r['stall_duration_s']),
                               float(r['timestamp']), color='red', alpha=0.25)

        if net:
            nt = [float(r['t']) for r in net]
            nb = [float(r['allocated_bw_mbps']) for r in net if r['allocated_bw_mbps']]
            nt2 = [float(r['t']) for r in net if r['allocated_bw_mbps']]
            ax.plot(nt2, nb, color=color, linestyle='--', alpha=0.6, linewidth=1.2,
                    label=f'{car} allocated_bw (hybrid model, Mbps)')

            # shade RSU segments faintly along the top
            prev_rsu, seg_start = None, None
            rsu_colors = {'rsu1': '#cfe8ff', 'rsu2': '#ffe8cf', 'rsu3': '#d8f5d8', 'rsu4': '#f5d8ee'}
            for r in net:
                if r['rsu'] != prev_rsu:
                    if prev_rsu is not None:
                        ax.axvspan(seg_start, float(r['t']), ymin=0.94, ymax=1.0,
                                   color=rsu_colors.get(prev_rsu, '#ddd'))
                    prev_rsu, seg_start = r['rsu'], float(r['t'])
            if prev_rsu is not None:
                ax.axvspan(seg_start, nt[-1], ymin=0.94, ymax=1.0,
                           color=rsu_colors.get(prev_rsu, '#ddd'))

        ax.set_ylabel('Mbps')
        ax.set_ylim(0, 11)
        ax.grid(True, alpha=0.4)
        ax.legend(loc='lower right', fontsize=8)
        ax.set_title(f'{car}  (red band = stall)', fontsize=10, loc='left')

    axes[-1].set_xlabel('t (s)')
    fig.suptitle(f'Situation 1 smoke test: {args.run_id}  --  chosen bitrate vs. '
                 f'hybrid-model allocated bandwidth per vehicle', fontsize=13, fontweight='bold')
    plt.tight_layout(rect=[0, 0, 1, 0.96])
    out = os.path.join(d, f'{args.run_id}_qoe_plot.png')
    plt.savefig(out, dpi=110)
    print('Saved:', out)


if __name__ == '__main__':
    main()
