#!/usr/bin/env python3
"""
plot_step2h_situation2_style_with_outage.py -- ORIGINAL 7-panel version
(includes the derived Cumulative Outage panel). Kept alongside the trimmed
6-panel version (plot_step2h_situation2_style.py) rather than overwritten --
this file's output is the "old" PNG the user asked to keep as-is.

Template: Situation2_DASH/compare_speeds.py (Rendition/Quality, RSSI,
Imposed Bandwidth, Packet Loss, Cumulative Outage, Handover Events,
Rebuffer Ratio; white background; per-RSU zone shading).

Lives OUTSIDE dash-baseline/ (read-only per project convention) -- only
imports baseline_model/baseline_4rsu_model from there for shared constants,
reads test04_step2h.csv, writes its own output here.

dash-baseline's CSV has no 'outage'/'cum_outage_s' column -- derived here:
outage = loss >= OUTAGE_LOSS_THRESH in a 0.5s sample.
"""
import csv, os, sys
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

_HERE = os.path.dirname(os.path.abspath(__file__))
DASH_BASELINE = os.path.join(_HERE, '..', 'dash-baseline')
sys.path.insert(0, DASH_BASELINE)
import baseline_model as M
import baseline_4rsu_model as M4

CSV_PATH = os.path.join(DASH_BASELINE, 'runs', '2026-07-08_bw-mapping-smoke-tests',
                         'test04_step2h.csv')
OUT_PATH = os.path.join(_HERE, 'plots', 'step2h_situation2_style.png')

OUTAGE_LOSS_THRESH = 50.0
SAMPLE_DT = 0.5
LINE_COLOR = '#1baf7a'
RSU_BAND = {
    '1': ('#2a78d6', 0.06), '2': ('#1baf7a', 0.06),
    '3': ('#eda100', 0.07), '4': ('#e34948', 0.06),
}
RUNG_LABEL = {0: '360p', 1: '720p', 2: '1080p'}


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


def derive_outage(rows):
    cum = 0.0
    outage, cum_outage_s = [], []
    for r in rows:
        is_out = float(r['loss']) >= OUTAGE_LOSS_THRESH
        if is_out:
            cum += SAMPLE_DT
        outage.append(1 if is_out else 0)
        cum_outage_s.append(cum)
    return outage, cum_outage_s


def handover_positions(rows):
    x = col(rows, 'x')
    ho = col(rows, 'handover', int)
    return [x[i] for i in range(len(x)) if ho[i]]


def summarize(rows, outage, cum_outage_s):
    t = col(rows, 't')
    stall = col(rows, 'stall', int)
    dt = [t[0]] + [t[i] - t[i - 1] for i in range(1, len(t))]
    handovers = sum(int(r['handover']) for r in rows)
    total_t = t[-1] + SAMPLE_DT
    cum_stall_s = sum(d for d, s in zip(dt, stall) if s)
    return dict(
        n_samples=len(rows), total_t=total_t, handovers=handovers,
        cum_stall_s=cum_stall_s,
        rebuffer_ratio_pct=100.0 * cum_stall_s / total_t,
        cum_outage_s=cum_outage_s[-1],
        outage_ratio_pct=100.0 * cum_outage_s[-1] / total_t,
        outage_samples=sum(outage),
    )


def make_plot(rows, outage, cum_outage_s, summary, out_path):
    fig, axes = plt.subplots(
        7, 1, figsize=(13, 18.5), facecolor='white',
        gridspec_kw=dict(height_ratios=[1, 1, 1, 1, 1, 0.45, 1.1]))
    fig.subplots_adjust(hspace=0.6, top=0.96)
    fig.suptitle('Step2h bandwidth mapping -- SDN+DASH, 4-RSU, 20 km/h (n=1)',
                  fontsize=13, fontweight='bold', color='#1a1a1a', y=0.985)

    ax_q, ax_rssi, ax_bw, ax_loss, ax_outage, ax_ho, ax_rebuf = axes
    line_axes = (ax_q, ax_rssi, ax_bw, ax_loss, ax_outage, ax_ho)
    xmin, xmax = M4.START_X, M4.END_X

    x = col(rows, 'x')
    rsu = col(rows, 'rsu', str)
    for ax in line_axes:
        for x0, x1, a in rsu_spans(x, rsu, xmax):
            color, alpha = RSU_BAND.get(a, ('#aaaaaa', 0.05))
            ax.axvspan(x0, x1, color=color, alpha=alpha, zorder=0, linewidth=0)

    qidx = col(rows, 'quality_idx', int)
    rssi = col(rows, 'rssi')
    bw = col(rows, 'bw_mbps')
    loss = col(rows, 'loss')

    ax_q.step(x, qidx, color=LINE_COLOR, lw=1.8, where='post', alpha=0.95,
              label='step2h', zorder=4)
    ax_rssi.plot(x, rssi, color=LINE_COLOR, lw=1.4, alpha=0.9, zorder=4)
    ax_bw.step(x, bw, color=LINE_COLOR, lw=1.6, where='post', alpha=0.9, zorder=4)
    ax_loss.plot(x, loss, color=LINE_COLOR, lw=1.4, alpha=0.9, zorder=4)
    ax_loss.axhline(OUTAGE_LOSS_THRESH, color='#eb6834', ls='--', lw=1, alpha=0.8,
                     zorder=3)
    ax_outage.step(x, cum_outage_s, color=LINE_COLOR, lw=1.8, where='post',
                    alpha=0.95, zorder=4)

    ho_x = handover_positions(rows)
    ax_ho.scatter(ho_x, [0] * len(ho_x), color=LINE_COLOR, marker='v',
                  s=80, zorder=4, edgecolor='white', linewidth=0.6)

    for ax, title, ylabel in [
        (ax_q, 'Rendition / Quality', 'Rendition'),
        (ax_rssi, 'RSSI', 'RSSI (dBm)'),
        (ax_bw, 'Imposed Bandwidth', 'Bandwidth (Mbps)'),
        (ax_loss, 'Packet Loss (dashed = outage threshold, %.0f%%)' % OUTAGE_LOSS_THRESH, 'Loss (%)'),
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
    ax_ho.set_ylim(-0.6, 0.6)
    ax_ho.set_yticks([0])
    ax_ho.set_yticklabels(['step2h'], fontsize=8.5)
    ax_ho.set_xlabel('Position (m)', fontsize=9.5)

    ax_q.legend(loc='upper right', fontsize=9, framealpha=0.85)

    ax_rebuf.bar(['step2h'], [summary['rebuffer_ratio_pct']], color=LINE_COLOR,
                 width=0.4, zorder=3)
    ymax_bar = max(summary['rebuffer_ratio_pct'], 0.5) * 1.35
    ax_rebuf.text(0, summary['rebuffer_ratio_pct'] + ymax_bar * 0.03,
                  '%.2f%%' % summary['rebuffer_ratio_pct'],
                  ha='center', fontsize=9, color='#333333')
    ax_rebuf.set_title('Rebuffer Ratio (cum. stall / run time)', fontsize=10,
                        fontweight='semibold', pad=4)
    ax_rebuf.set_ylabel('Rebuffer %', fontsize=9.5)
    ax_rebuf.set_ylim(0, ymax_bar)
    ax_rebuf.grid(axis='y', color='#e5e5e5', linewidth=0.8, zorder=0)
    ax_rebuf.set_axisbelow(True)

    fig.text(0.02, 0.003,
              'n=1 (single confirmed run, 2026-07-08) -- HOs=%d, cum_outage=%.1fs (%.2f%%), '
              'rebuffer=%.2f%%. Outage = loss >= %.0f%% per 0.5s sample (not in original '
              'dash-baseline CSV -- derived here).'
              % (summary['handovers'], summary['cum_outage_s'], summary['outage_ratio_pct'],
                 summary['rebuffer_ratio_pct'], OUTAGE_LOSS_THRESH),
              fontsize=8.3, color='#666666', ha='left')

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches='tight', facecolor='white',
                edgecolor='none')
    print('saved -> %s' % out_path)
    plt.close(fig)


if __name__ == '__main__':
    rows = load_csv(CSV_PATH)
    outage, cum_outage_s = derive_outage(rows)
    summary = summarize(rows, outage, cum_outage_s)
    print(summary)
    make_plot(rows, outage, cum_outage_s, summary, OUT_PATH)
