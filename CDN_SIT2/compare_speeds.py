#!/usr/bin/env python3
"""
compare_speeds.py — Situation 2 (Mobility Speed) CDN SDN comparison

Overlays every available speed case (20 km/h baseline from CDN_baseline's
own results tree, plus 80/100/120 km/h from CDN_SIT2/results_hightspeed/)
on the SAME position (x) axis -- this is the single-vehicle straight-line
4-AP route (CDN_baseline/cdn_baseline_topo_sdn.py's own scenario), so x is
monotonic and identical across every speed case (always -300m -> 1800m),
unlike Situation 1's loop route where only time was safe to plot against.

Prints a summary table (handovers, rebuffer ratio, avg latency/loss/BW,
avg QoE, cache hit rate) and a 6-panel overlay figure, one line per speed.

Usage:
    python3 compare_speeds.py                  # all speeds found
    python3 compare_speeds.py --speeds 20 80    # just these two
"""
import csv, os, sys, glob, argparse
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, '..'))
_CDN_BASELINE_DIR = os.path.join(_REPO_ROOT, 'CDN_baseline')
sys.path.insert(0, _CDN_BASELINE_DIR)
import baseline_model as M

# All Situation 2 runs live under CDN_SIT2/results_hightspeed/ (this
# script's own run tree, and cdn_sdn_hight_speed.py's own default --out-dir)
# -- no other location is searched. A speed with no run under here just
# doesn't show up in the comparison (see the [WARN] in __main__ below),
# rather than silently falling back to a differently-produced CSV from
# somewhere else in the repo.
NEW_ROOT = os.path.join(_HERE, 'results_hightspeed')

ALL_SPEEDS = [20, 80, 100, 120]
SPEED_COLOR = {20: '#1baf7a', 80: '#2a78d6', 100: '#eda100', 120: '#e34948'}

AP_BAND = {
    'ap1': ('#2a78d6', 0.06), 'ap2': ('#1baf7a', 0.06),
    'ap3': ('#eda100', 0.07), 'ap4': ('#e34948', 0.06),
}


def find_run(sit, speed):
    """Only ever looks under results_hightspeed/sit{sit}/speed{speed}/ --
    run_id is cdn_sdn_hightspeed_sit{sit}_spd{speed}_r{round}, picks the
    most recently modified round if more than one exists."""
    new_glob = os.path.join(NEW_ROOT, f'sit{sit}', f'speed{speed}',
                             f'cdn_sdn_hightspeed_sit{sit}_spd{speed}_r*',
                             f'cdn_sdn_hightspeed_sit{sit}_spd{speed}_r*.csv')
    matches = glob.glob(new_glob)
    return max(matches, key=os.path.getmtime) if matches else None


def load_csv(path):
    with open(path) as f:
        return list(csv.DictReader(f))


def col(rows, key, cast=float):
    return [cast(r[key]) for r in rows]


def ap_spans(x, ap, xmax):
    spans, i = [], 0
    while i < len(x):
        a = ap[i]; j = i
        while j < len(x) and ap[j] == a:
            j += 1
        spans.append((x[i], x[j] if j < len(x) else xmax, a))
        i = j
    return spans


def summarize(rows, speed_kmh):
    x = col(rows, 'x')
    ap = col(rows, 'ap', str)
    lat = col(rows, 'latency_s')
    loss = col(rows, 'loss_pct')
    bw = col(rows, 'bw_mbps')
    cache = col(rows, 'cache', str)
    qoe = M.compute_cdn_qoe(rows)
    handovers = sum(1 for i in range(1, len(ap)) if ap[i] != ap[i - 1])
    hits = sum(1 for c in cache if c == 'HIT')
    total_t = float(rows[-1]['t'])
    cum_stall_s = float(rows[-1]['vlc_cum_stall_s'])
    # dwell time in the 100m inter-AP overlap zone at this speed -- the
    # window a real handover (~0.95s assoc, see topology_ho csv) has to
    # complete in before the car is back in single-AP-only territory.
    overlap_dwell_s = 100.0 / (speed_kmh / 3.6)
    # outage/cum_outage_s only exist in runs from cdn_sdn_hight_speed.py's
    # Option-2 rewrite (position no longer freezes during handover, so a
    # handover that can't finish before the car leaves the AP's range shows
    # up as a real outage instead of being hidden) -- older runs (the
    # original CDN_baseline speed20 case, or anything from before that
    # rewrite) won't have these columns at all, so default to 0 rather than
    # KeyError.
    has_outage_col = 'cum_outage_s' in rows[-1]
    cum_outage_s = float(rows[-1]['cum_outage_s']) if has_outage_col else 0.0
    outages = sum(1 for r in rows if int(r.get('outage', 0))) if has_outage_col else 0
    return dict(
        speed=speed_kmh,
        n_samples=len(rows),
        total_t=total_t,
        handovers=handovers,
        hit_rate_pct=100.0 * hits / len(rows),
        avg_latency_s=sum(lat) / len(lat),
        max_latency_s=max(lat),
        avg_loss_pct=sum(loss) / len(loss),
        max_loss_pct=max(loss),
        avg_bw_mbps=sum(bw) / len(bw),
        cum_stall_s=cum_stall_s,
        rebuffer_ratio_pct=100.0 * cum_stall_s / total_t,
        avg_qoe=sum(qoe) / len(qoe),
        net_qoe=sum(qoe),
        overlap_dwell_s=overlap_dwell_s,
        cum_outage_s=cum_outage_s,
        outage_ratio_pct=100.0 * cum_outage_s / total_t,
        outage_samples=outages,
        has_outage_col=has_outage_col,
    )


def print_summary_table(summaries):
    print()
    print('=' * 118)
    print('Situation 2: Mobility Speed (SDN+CDN) — comparison')
    print('=' * 118)
    hdr = (f"{'speed':>6} | {'run(s)':>7} | {'overlap dwell':>13} | {'HOs':>3} | "
           f"{'HIT%':>6} | {'avg lat':>8} | {'avg loss':>9} | {'avg BW':>8} | "
           f"{'stall dur':>9} | {'rebuf%':>7} | {'avg QoE':>8} | {'outage':>8} | {'outage%':>8}")
    print(hdr)
    print('-' * len(hdr))
    for s in sorted(summaries):
        v = summaries[s]
        outage_col = (f"{v['cum_outage_s']:>6.2f}s" if v['has_outage_col'] else "     n/a")
        outage_pct_col = (f"{v['outage_ratio_pct']:>6.2f}%" if v['has_outage_col'] else "     n/a")
        print(f"{v['speed']:>4}kmh | {v['total_t']:>6.1f}s | {v['overlap_dwell_s']:>11.2f}s | "
              f"{v['handovers']:>3} | {v['hit_rate_pct']:>5.1f}% | {v['avg_latency_s']:>7.3f}s | "
              f"{v['avg_loss_pct']:>8.3f}% | {v['avg_bw_mbps']:>6.2f}Mb | "
              f"{v['cum_stall_s']:>7.2f}s | {v['rebuffer_ratio_pct']:>6.2f}% | {v['avg_qoe']:>8.3f} | "
              f"{outage_col} | {outage_pct_col}")
    print('=' * 118)
    print('NOTE: overlap dwell = time available to cross the 100m inter-AP')
    print('overlap zone at that speed -- real WiFi (re)association takes a')
    print('roughly constant ~0.95s (see topology_ho_*.csv) regardless of')
    print('speed, so it eats a growing share of that window as speed rises.')
    print('rebuffer% = vlc_cum_stall_s / total run time -- the fair,')
    print('speed-normalized stall metric (raw stall seconds shrink at high')
    print('speed simply because the whole run is shorter, so compare ratios,')
    print('not raw seconds).')
    print('outage/outage% = time with NO real AP association at all (from')
    print('cum_outage_s -- only present in runs produced after the Option-2')
    print('rewrite, where the vehicle keeps moving during a handover instead')
    print('of freezing in place; "n/a" = older run, predates this column).')
    print('This is the hard-floor failure mode neither DASH buffering nor')
    print('CDN caching can paper over -- distinct from rebuffer%, which can')
    print('still be nonzero even with zero outage (just low bandwidth).')
    print()


def make_overlay_plot(cases, out_path):
    fig, axes = plt.subplots(6, 1, figsize=(13, 17.5), facecolor='white')
    fig.subplots_adjust(hspace=0.5, top=0.94)
    fig.suptitle('Situation 2: Mobility Speed — SDN+CDN, 20 vs 80/100/120 km/h',
                  fontsize=13, fontweight='bold', color='#1a1a1a', y=0.975)

    ax_qoe, ax_lat, ax_rssi, ax_bw, ax_loss, ax_cache = axes
    xmin, xmax = -300.0, 1800.0
    # 'LOSS' replaces the old 'UNKNOWN' tier -- cache HIT/MISS is strictly an
    # edge-content question; a request that got no answer at all (outage or
    # a timed-out probe) is a connection LOSS, not a third cache state. Kept
    # as a fallback key too (.get(c, 0.5) below) for any older CSV that still
    # has literal 'UNKNOWN' rows from before this rename.
    CV_MAP = {'HIT': 1, 'MISS': 0, 'LOSS': 0.5, 'UNKNOWN': 0.5}

    # AP zone bands drawn once from whichever case has the most samples
    # (finest-grained zone boundaries) -- the AP layout itself (x position
    # of each zone) doesn't change with speed, only how many samples land
    # in each zone.
    ref_rows = max(cases.values(), key=lambda r: len(r))
    ref_x, ref_ap = col(ref_rows, 'x'), col(ref_rows, 'ap', str)
    for ax in axes:
        for x0, x1, a in ap_spans(ref_x, ref_ap, xmax):
            color, alpha = AP_BAND.get(a, ('#aaaaaa', 0.05))
            ax.axvspan(x0, x1, color=color, alpha=alpha, zorder=0, linewidth=0)

    for speed in sorted(cases):
        rows = cases[speed]
        color = SPEED_COLOR.get(speed, '#333333')
        label = f'{speed} km/h'
        x = col(rows, 'x')
        qoe = M.compute_cdn_qoe(rows)
        lat = col(rows, 'latency_s')
        rssi = col(rows, 'rssi')
        bw = col(rows, 'bw_mbps')
        loss = col(rows, 'loss_pct')
        cache = col(rows, 'cache', str)
        cv = [CV_MAP.get(c, 0.5) for c in cache]

        ax_qoe.plot(x, qoe, color=color, lw=1.6, marker='o', markersize=2.6,
                    alpha=0.9, label=label, zorder=4)
        ax_lat.plot(x, lat, color=color, lw=1.4, alpha=0.9, zorder=4)
        ax_rssi.plot(x, rssi, color=color, lw=1.3, alpha=0.85, zorder=4)
        ax_bw.step(x, bw, color=color, lw=1.5, where='post', alpha=0.85, zorder=4)
        ax_loss.plot(x, loss, color=color, lw=1.3, alpha=0.85, zorder=4)
        ax_cache.step(x, cv, color=color, lw=1.0, where='post', alpha=0.5,
                      zorder=2 + speed / 1000.0)

    for ax, title, ylabel in [
        (ax_qoe, 'Quality of Experience (QoE)', 'QoE (score)'),
        (ax_lat, 'CDN Latency', 'Latency (s)'),
        (ax_rssi, 'RSSI', 'RSSI (dBm)'),
        (ax_bw, 'Imposed Bandwidth (step2h)', 'Bandwidth (Mbps)'),
        (ax_loss, 'Packet Loss', 'Loss (%)'),
        (ax_cache, 'Cache HIT/MISS', 'Cache'),
    ]:
        ax.set_xlim(xmin, xmax)
        ax.set_xlabel('Position (m)', fontsize=9.5)
        ax.set_title(title, fontsize=10, fontweight='semibold', pad=4)
        ax.set_ylabel(ylabel, fontsize=9.5)

    ax_lat.set_ylim(0, 3.5)
    ax_loss.set_ylim(bottom=0)
    ax_cache.set_ylim(-0.4, 1.4)
    ax_cache.set_yticks([0, 0.5, 1])
    ax_cache.set_yticklabels(['MISS', 'LOSS', 'HIT'], fontsize=8.5)

    ax_qoe.legend(loc='upper right', fontsize=9, framealpha=0.85)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    print(f'  saved -> {out_path}')
    plt.close(fig)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description='Situation 2 speed comparison')
    p.add_argument('--sit', type=int, default=1, choices=[1, 2])
    p.add_argument('--speeds', type=int, nargs='+', default=ALL_SPEEDS)
    p.add_argument('--out', type=str,
                    default=os.path.join(_HERE, 'plots', 'speed_comparison.png'))
    args = p.parse_args()

    cases, summaries = {}, {}
    for speed in args.speeds:
        path = find_run(args.sit, speed)
        if path is None:
            print(f'[WARN] no run found for speed={speed} (sit={args.sit})')
            continue
        print(f'[speed={speed}] loading {path}')
        rows = load_csv(path)
        cases[speed] = rows
        summaries[speed] = summarize(rows, speed)

    if not cases:
        print('[ERROR] no runs found at all -- nothing to compare')
        sys.exit(1)

    print_summary_table(summaries)
    make_overlay_plot(cases, args.out)
    print('\nDone.')
