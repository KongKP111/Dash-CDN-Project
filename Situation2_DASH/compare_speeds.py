#!/usr/bin/env python3
"""
compare_speeds.py -- Situation 2 (Mobility Speed) DASH comparison

Template copied from CDN_SIT2/compare_speeds.py (see
Situation2_DASH/HANDOFF_FROM_CDN_REFERENCE.md) and adapted for DASH's own
CSV schema, which has no 'latency_s' or 'cache' columns (no CDN edge/cache
concept on this arm) -- those two panels/columns are dropped, not filled
with placeholders. Everything else (x-position axis, AP-zone background
bands, one line per speed, summary table + overlay figure) mirrors the CDN
template exactly so the two arms' figures read as the same family.

Panel substitution: CDN's top panel is QoE (via M.compute_cdn_qoe(), a
formula that needs latency+cache, which DASH doesn't have). Substituted
with a Rendition/Quality panel instead -- DASH's own most direct "what did
the viewer actually get" signal, built straight from real data instead of
a formula. (baseline_model.py's own qoe() is a toy 1-5 heuristic with no
academic basis -- not used here, see TEAMMATE_SETUP.md / project memory.)
Added a 5th panel, Cumulative Outage, since outage/cum_outage_s is the
headline metric this whole Situation 2 rewrite exists to measure.

Metric set finalized 2026-07-10 against CDN_SIT2's own CSV columns (see
project memory) -- these are the ones that mean the same thing on both
arms without unit conversion: bw_mbps, rssi, loss, cum_outage_s/outage%,
handover, quality. rebuffer_ratio_pct is a derived scalar (DASH's buffer_s
vs CDN's vlc_buffer_pct are different units/sources -- only the normalized
ratio, cum_stall_s/total_t, is safe to compare) so it gets a bar panel
instead of a position-line panel. Handover is a discrete event, not a
line -- gets its own thin per-speed marker strip instead of being folded
into another axis.

Usage:
    python3 compare_speeds.py                    # all speeds found
    python3 compare_speeds.py --speeds 80 100 120 # just these
"""
import csv, os, sys, glob, argparse
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import baseline_model as M
import baseline_4rsu_model as M4

NEW_ROOT = os.path.join(_HERE, 'results_hightspeed')

ALL_SPEEDS = [20, 80, 100, 120]
# same hex codes as CDN_SIT2/compare_speeds.py's SPEED_COLOR -- keeping the
# same speed->color mapping across both arms' figures makes them directly
# eyeball-comparable side by side.
SPEED_COLOR = {20: '#1baf7a', 80: '#2a78d6', 100: '#eda100', 120: '#e34948'}

RSU_BAND = {
    'rsu1': ('#2a78d6', 0.06), 'rsu2': ('#1baf7a', 0.06),
    'rsu3': ('#eda100', 0.07), 'rsu4': ('#e34948', 0.06),
}
RUNG_LABEL = {0: '360p', 1: '720p', 2: '1080p'}


def find_run(speed):
    """Only looks under results_hightspeed/speed{speed}/*.csv -- picks the
    most recently modified file if more than one exists."""
    pattern = os.path.join(NEW_ROOT, f'speed{speed}', '*.csv')
    matches = glob.glob(pattern)
    return max(matches, key=os.path.getmtime) if matches else None


def load_csv(path):
    with open(path) as f:
        return list(csv.DictReader(f))


def col(rows, key, cast=float):
    return [cast(r[key]) for r in rows]


def rsu_spans(x, rsu, xmax):
    spans, i = [], 0
    while i < len(x):
        a = rsu[i]; j = i
        while j < len(x) and rsu[j] == a:
            j += 1
        spans.append((x[i], x[j] if j < len(x) else xmax, a))
        i = j
    return spans


def summarize(rows, speed_kmh):
    t = col(rows, 't')
    x = col(rows, 'x')
    rsu = col(rows, 'rsu', str)
    bw = col(rows, 'bw_mbps')
    qidx = col(rows, 'quality_idx', int)
    stall = col(rows, 'stall', int)
    outage = col(rows, 'outage', int)

    # per-row dt reconstructed from consecutive wall-clock t values (the
    # CSV itself only stores cumulative t) -- drives both the stall-second
    # and sanity totals below, same wall-clock-realism principle as the
    # run_loop() fix that produced this data in the first place.
    dt = [t[0]] + [t[i] - t[i - 1] for i in range(1, len(t))]

    handovers = sum(1 for i in range(1, len(rsu)) if rsu[i] != rsu[i - 1])
    bitrate_mbps = [M.LADDER[RUNG_LABEL[q]] for q in qidx]
    total_t = t[-1]
    cum_stall_s = sum(d for d, s in zip(dt, stall) if s)
    cum_outage_s = float(rows[-1]['cum_outage_s'])
    outage_samples = sum(outage)
    overlap_dwell_s = 100.0 / (speed_kmh / 3.6)

    return dict(
        speed=speed_kmh,
        n_samples=len(rows),
        total_t=total_t,
        handovers=handovers,
        avg_bitrate_mbps=sum(bitrate_mbps) / len(bitrate_mbps),
        avg_bw_mbps=sum(bw) / len(bw),
        cum_stall_s=cum_stall_s,
        rebuffer_ratio_pct=100.0 * cum_stall_s / total_t,
        overlap_dwell_s=overlap_dwell_s,
        cum_outage_s=cum_outage_s,
        outage_ratio_pct=100.0 * cum_outage_s / total_t,
        outage_samples=outage_samples,
    )


def print_summary_table(summaries):
    print()
    print('=' * 104)
    print('Situation 2: Mobility Speed (SDN+DASH) -- comparison')
    print('=' * 104)
    hdr = (f"{'speed':>7} | {'run(s)':>7} | {'overlap dwell':>13} | {'HOs':>3} | "
           f"{'avg bitrate':>11} | {'avg BW':>8} | {'stall dur':>9} | "
           f"{'rebuf%':>7} | {'outage':>8} | {'outage%':>8}")
    print(hdr)
    print('-' * len(hdr))
    for s in sorted(summaries):
        v = summaries[s]
        print(f"{v['speed']:>4}kmh | {v['total_t']:>6.1f}s | {v['overlap_dwell_s']:>11.2f}s | "
              f"{v['handovers']:>3} | {v['avg_bitrate_mbps']:>9.2f}Mb | "
              f"{v['avg_bw_mbps']:>6.2f}Mb | {v['cum_stall_s']:>7.2f}s | "
              f"{v['rebuffer_ratio_pct']:>6.2f}% | {v['cum_outage_s']:>6.2f}s | "
              f"{v['outage_ratio_pct']:>6.2f}%")
    print('=' * 104)
    print('NOTE: overlap dwell = time available to cross the 100m inter-RSU')
    print('overlap zone at that speed -- real WiFi (re)association takes a')
    print('roughly constant amount of real time regardless of speed, so it')
    print('eats a growing share of that window as speed rises.')
    print('rebuffer% = cum_stall_s / total run time (speed-normalized --')
    print('raw stall seconds shrink at high speed simply because the whole')
    print('run is shorter, so compare ratios, not raw seconds).')
    print('outage/outage% = cum_outage_s / total run time -- real seconds')
    print('with NO RSU association at all. The hard-floor failure mode ABR')
    print('buffering cannot paper over; distinct from rebuffer%, which can')
    print('still be nonzero even with zero outage (just low bandwidth).')
    print()


def handover_positions(rows):
    """x-position of every row where the handover flag fires."""
    x = col(rows, 'x')
    ho = col(rows, 'handover', int)
    return [x[i] for i in range(len(x)) if ho[i]]


def make_overlay_plot(cases, summaries, out_path):
    fig, axes = plt.subplots(
        7, 1, figsize=(13, 18.5), facecolor='white',
        gridspec_kw=dict(height_ratios=[1, 1, 1, 1, 1, 0.45, 1.1]))
    fig.subplots_adjust(hspace=0.6, top=0.96)
    fig.suptitle('Situation 2: Mobility Speed -- SDN+DASH, 80/100/120 km/h',
                  fontsize=13, fontweight='bold', color='#1a1a1a', y=0.985)

    ax_q, ax_rssi, ax_bw, ax_loss, ax_outage, ax_ho, ax_rebuf = axes
    line_axes = (ax_q, ax_rssi, ax_bw, ax_loss, ax_outage, ax_ho)
    xmin, xmax = M4.START_X, M4.END_X

    ref_rows = max(cases.values(), key=lambda r: len(r))
    ref_x, ref_rsu = col(ref_rows, 'x'), col(ref_rows, 'rsu', str)
    for ax in line_axes:
        for x0, x1, a in rsu_spans(ref_x, ref_rsu, xmax):
            color, alpha = RSU_BAND.get('rsu' + a, ('#aaaaaa', 0.05))
            ax.axvspan(x0, x1, color=color, alpha=alpha, zorder=0, linewidth=0)

    speeds_sorted = sorted(cases)
    for row_i, speed in enumerate(speeds_sorted):
        rows = cases[speed]
        color = SPEED_COLOR.get(speed, '#333333')
        label = f'{speed} km/h'
        x = col(rows, 'x')
        qidx = col(rows, 'quality_idx', int)
        rssi = col(rows, 'rssi')
        bw = col(rows, 'bw_mbps')
        loss = col(rows, 'loss')
        cum_out = col(rows, 'cum_outage_s')

        ax_q.step(x, qidx, color=color, lw=1.6, where='post', alpha=0.9,
                  label=label, zorder=4)
        ax_rssi.plot(x, rssi, color=color, lw=1.3, alpha=0.85, zorder=4)
        ax_bw.step(x, bw, color=color, lw=1.5, where='post', alpha=0.85, zorder=4)
        ax_loss.plot(x, loss, color=color, lw=1.3, alpha=0.85, zorder=4)
        ax_outage.step(x, cum_out, color=color, lw=1.6, where='post',
                        alpha=0.9, zorder=4)

        # Handover is a discrete event, not a continuous signal -- one thin
        # marker row per speed (stacked so overlapping events at nearly the
        # same x, which is expected since handover triggers on position not
        # time, don't hide each other) instead of forcing it onto a line axis.
        ho_x = handover_positions(rows)
        ax_ho.scatter(ho_x, [row_i] * len(ho_x), color=color, marker='v',
                       s=70, zorder=4, edgecolor='white', linewidth=0.6)

    for ax, title, ylabel in [
        (ax_q, 'Rendition / Quality', 'Rendition'),
        (ax_rssi, 'RSSI', 'RSSI (dBm)'),
        (ax_bw, 'Imposed Bandwidth', 'Bandwidth (Mbps)'),
        (ax_loss, 'Packet Loss', 'Loss (%)'),
        (ax_outage, 'Cumulative Outage', 'Outage (s)'),
        (ax_ho, 'Handover Events', ''),
    ]:
        ax.set_xlim(xmin, xmax)
        ax.set_title(title, fontsize=10, fontweight='semibold', pad=4)
        ax.set_ylabel(ylabel, fontsize=9.5)

    ax_q.set_ylim(-0.4, 2.4)
    ax_q.set_yticks([0, 1, 2])
    ax_q.set_yticklabels(['360p', '720p', '1080p'], fontsize=8.5)
    ax_loss.set_ylim(bottom=0)
    ax_outage.set_ylim(bottom=0)
    ax_ho.set_ylim(-0.6, len(speeds_sorted) - 0.4)
    ax_ho.set_yticks(range(len(speeds_sorted)))
    ax_ho.set_yticklabels([f'{s} km/h' for s in speeds_sorted], fontsize=8.5)
    ax_ho.set_xlabel('Position (m)', fontsize=9.5)

    ax_q.legend(loc='upper right', fontsize=9, framealpha=0.85)

    # Rebuffer ratio is a single scalar per speed (normalized so it's safe
    # to compare against CDN's own vlc_buffer_pct-derived ratio despite the
    # different underlying units) -- a bar per speed, not a position line.
    bars_x = [f'{s} km/h' for s in speeds_sorted]
    bars_y = [summaries[s]['rebuffer_ratio_pct'] for s in speeds_sorted]
    bar_colors = [SPEED_COLOR.get(s, '#333333') for s in speeds_sorted]
    ax_rebuf.bar(bars_x, bars_y, color=bar_colors, width=0.5, zorder=3)
    for i, v in enumerate(bars_y):
        ax_rebuf.text(i, v + max(bars_y + [0.5]) * 0.03, f'{v:.2f}%',
                       ha='center', fontsize=9, color='#333333')
    ax_rebuf.set_title('Rebuffer Ratio (cum. stall / run time)', fontsize=10,
                        fontweight='semibold', pad=4)
    ax_rebuf.set_ylabel('Rebuffer %', fontsize=9.5)
    ax_rebuf.set_ylim(0, max(bars_y + [0.5]) * 1.35)
    ax_rebuf.grid(axis='y', color='#e5e5e5', linewidth=0.8, zorder=0)
    ax_rebuf.set_axisbelow(True)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    print(f'  saved -> {out_path}')
    plt.close(fig)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description='Situation 2 DASH speed comparison')
    p.add_argument('--speeds', type=int, nargs='+', default=[80, 100, 120])
    p.add_argument('--out', type=str,
                    default=os.path.join(_HERE, 'plots', 'speed_comparison.png'))
    args = p.parse_args()

    cases, summaries = {}, {}
    for speed in args.speeds:
        path = find_run(speed)
        if path is None:
            print(f'[WARN] no run found for speed={speed}')
            continue
        print(f'[speed={speed}] loading {path}')
        rows = load_csv(path)
        cases[speed] = rows
        summaries[speed] = summarize(rows, speed)

    if not cases:
        print('[ERROR] no runs found at all -- nothing to compare')
        sys.exit(1)

    print_summary_table(summaries)
    make_overlay_plot(cases, summaries, args.out)
    print('\nDone.')
