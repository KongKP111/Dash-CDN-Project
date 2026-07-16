#!/usr/bin/env python3
"""
plot_step2h_situation1_style.py -- step2h bandwidth-mapping baseline
(single vehicle, 4-RSU), plotted using the EXACT same template as
Situation1_DASH/plot_situation1_percar.py (one column per vehicle, 5 rows:
QoE, RSSI, Allocated Bandwidth, Packet Loss, Quality/Rendition; RSU zone
shading; handover dashed lines; X axis = time). Only 1 column here since
this baseline is a single vehicle, not a 3/5/7-car platoon.

Lives OUTSIDE dash-baseline/ (read-only per project convention) -- reads
test04_step2h.csv directly, writes its own output here.

QoE: same Yin et al. formula/constants as Situation1's compute_dash_qoe()
and CDN_baseline/dash_cdn_comparison.py's compute_dash_qoe() (mu=1.0,
bitrate ladder 360p/720p/1080p = 1.0/2.5/5.0 Mbps): q(R_k) - mu*|q(R_k)-
q(R_k-1)| - T_k. Situation1 reads bitrate_kbps + stall_duration_s straight
from a per-segment segments.csv; this baseline's CSV is per-0.5s-sample
(t,x,dist,rsu,rssi,rssi_src,bw_mbps,quality,quality_idx,seg,loss,stall,
buffer_s,handover) with no separate segment file, so segments are
reconstructed here the same way as the earlier step2h dashboards: group
consecutive rows sharing the same `seg` value, taking that segment's
quality_idx (-> bitrate Mbps) and summing `stall` ticks (0.5s each) since
the previous segment boundary as that segment's stall_duration_s.
"""
import csv, os, sys
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

_HERE = os.path.dirname(os.path.abspath(__file__))
DASH_BASELINE = os.path.join(_HERE, '..', 'dash-baseline')
CSV_PATH = os.path.join(DASH_BASELINE, 'runs', '2026-07-08_bw-mapping-smoke-tests',
                         'test04_step2h.csv')
OUT_PATH = os.path.join(_HERE, 'plots', 'step2h_situation1_style.png')

SAMPLE_DT = 0.5
LADDER_MBPS = {0: 1.0, 1: 2.5, 2: 5.0}
CASE_COLOR = '#1baf7a'   # step2h green, same as this project's other plots
C_DASH = '#2a78d6'       # single consistent accent, same slot CDN/Situation1 use for one series
C_HO = '#e89c00'
RSU_BAND = {
    'rsu1': ('#2a78d6', 0.08), 'rsu2': ('#1baf7a', 0.08),
    'rsu3': ('#eda100', 0.09), 'rsu4': ('#e34948', 0.08),
}
_MU = 1.0
ROW_TITLES = ['Quality of Experience (QoE)', 'RSSI',
              'Allocated Bandwidth (step2h)', 'Packet Loss',
              'Quality / Rendition']


def load(path):
    with open(path) as f:
        return list(csv.DictReader(f))


def col(rows, key, cast=float):
    return [cast(r[key]) for r in rows]


def reconstruct_segments(rows):
    """dash-baseline's CSV has no separate segments.csv -- rebuild one
    segment per unique `seg` value: (timestamp, bitrate_mbps, stall_s)."""
    segs = []
    last_seg, stall_accum = None, 0.0
    for r in rows:
        stall_accum += SAMPLE_DT if int(r['stall']) else 0.0
        seg = int(r['seg'])
        if seg != last_seg and seg > 0:
            segs.append(dict(t=float(r['t']), bitrate=LADDER_MBPS[int(r['quality_idx'])],
                              stall_s=stall_accum))
            stall_accum = 0.0
            last_seg = seg
    return segs


def compute_dash_qoe(seg_rows):
    """Per-segment Yin et al. QoE term: q(R_k) - mu*|q(R_k)-q(R_k-1)| - T_k.
    Identical formula/constants to Situation1_DASH/plot_situation1_percar.py
    and CDN_baseline/dash_cdn_comparison.py's compute_dash_qoe()."""
    qoes, prev_bitrate = [], None
    for s in seg_rows:
        switch_penalty = (_MU * abs(s['bitrate'] - prev_bitrate)
                          if prev_bitrate is not None else 0.0)
        qoes.append(s['bitrate'] - switch_penalty - s['stall_s'])
        prev_bitrate = s['bitrate']
    return qoes


def zone_spans(t, rsu, tmax):
    spans, i = [], 0
    while i < len(t):
        a = rsu[i]; j = i
        while j < len(t) and rsu[j] == a:
            j += 1
        t_end = t[j] if j < len(t) else tmax
        spans.append((t[i], t_end, a))
        i = j
    return spans


def zone_transitions(t, rsu):
    return [(t[i], f'{rsu[i-1].upper()}→{rsu[i].upper()}')
            for i in range(1, len(rsu)) if rsu[i] != rsu[i - 1]]


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
            ax.text(ct, ylim[0] + (ylim[1] - ylim[0]) * 0.03, a.upper(),
                    ha='center', va='bottom', fontsize=6.5, color=color,
                    alpha=0.85, zorder=1)
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


def make_plot(rows, seg, out_path):
    fig, axes = plt.subplots(5, 1, figsize=(4.6, 15.0), facecolor='white', squeeze=False)
    fig.subplots_adjust(hspace=0.6, wspace=0.32, top=0.90)
    fig.suptitle('Step2h Baseline — SDN+DASH, 1 car (4-RSU)',
                  fontsize=13, fontweight='bold', color='#1a1a1a', y=0.995)

    t = col(rows, 't')
    rssi = col(rows, 'rssi')
    bw = col(rows, 'bw_mbps')
    loss = col(rows, 'loss')
    rsu = ['rsu' + r['rsu'] for r in rows]
    tmin, tmax = min(t), max(t)
    trans = zone_transitions(t, rsu)

    seg_t = [s['t'] for s in seg]
    qoe = compute_dash_qoe(seg)
    net_qoe = sum(qoe) if qoe else 0.0

    col_i = 0
    axes[0, col_i].annotate(
        f'car1  (n={len(rows)})', xy=(0.5, 1.22), xycoords='axes fraction',
        ha='center', va='bottom', fontsize=11, fontweight='bold', color=CASE_COLOR)

    # 1. QoE (segment cadence)
    ax = axes[0, col_i]
    ax.plot(seg_t, qoe, color=C_DASH, lw=1.8, marker='o', markersize=3, zorder=4)
    ax.fill_between(seg_t, qoe, alpha=0.15, color=C_DASH)
    ax.set_xlim(tmin, tmax)
    ax.set_title(ROW_TITLES[0], fontsize=9.5, fontweight='semibold', pad=4)
    ax.set_ylabel('QoE', fontsize=9)
    ax.text(0.99, 0.06, f'Net={net_qoe:.1f} (avg {net_qoe/len(qoe):.3f})',
            transform=ax.transAxes, ha='right', va='bottom', fontsize=7,
            fontweight='semibold',
            bbox=dict(boxstyle='round,pad=0.25', facecolor='white',
                      edgecolor='#cccccc', alpha=0.85))
    add_rsu_bands(ax, t, rsu, tmax, ax.get_ylim())
    add_handover_lines(ax, trans, ax.get_ylim())

    # 2. RSSI
    ax = axes[1, col_i]
    ax.plot(t, rssi, color=C_DASH, lw=1.4, zorder=3, alpha=0.6)
    ax.scatter(t, rssi, s=10, color=C_DASH, zorder=5, marker='o', edgecolors='none')
    rssi_ylim = (min(rssi) - 5, max(rssi) + 5)
    ax.set_ylim(*rssi_ylim); ax.set_xlim(tmin, tmax)
    ax.set_title(ROW_TITLES[1], fontsize=9.5, fontweight='semibold', pad=4)
    ax.set_ylabel('RSSI (dBm)', fontsize=9)
    add_rsu_bands(ax, t, rsu, tmax, rssi_ylim, show_label=False)
    add_handover_lines(ax, trans, rssi_ylim, show_label=False)

    # 3. Bandwidth
    ax = axes[2, col_i]
    bw_max = max(max(bw) * 1.15, 1)
    ax.step(t, bw, color=C_DASH, lw=1.8, where='post', zorder=4)
    ax.fill_between(t, bw, step='post', alpha=0.15, color=C_DASH)
    ax.set_ylim(0, bw_max); ax.set_xlim(tmin, tmax)
    ax.set_title(ROW_TITLES[2], fontsize=9.5, fontweight='semibold', pad=4)
    ax.set_ylabel('Bandwidth (Mbps)', fontsize=9)
    add_rsu_bands(ax, t, rsu, tmax, (0, bw_max), show_label=False)
    add_handover_lines(ax, trans, (0, bw_max), show_label=False)

    # 4. Packet loss
    ax = axes[3, col_i]
    ax.plot(t, loss, color=C_DASH, lw=1.8, marker='o', markersize=3, zorder=4)
    ax.fill_between(t, loss, alpha=0.15, color=C_DASH)
    loss_max = max(max(loss) * 1.2, 5)
    ax.set_ylim(0, loss_max); ax.set_xlim(tmin, tmax)
    ax.set_title(ROW_TITLES[3], fontsize=9.5, fontweight='semibold', pad=4)
    ax.set_ylabel('Loss (%)', fontsize=9)
    add_rsu_bands(ax, t, rsu, tmax, (0, loss_max), show_label=False)
    add_handover_lines(ax, trans, (0, loss_max), show_label=False)

    # 5. Quality / Rendition
    ax = axes[4, col_i]
    qbr = [s['bitrate'] for s in seg]
    ax.step(seg_t, qbr, color=C_DASH, lw=1.8, where='post', zorder=4)
    ax.fill_between(seg_t, qbr, step='post', alpha=0.15, color=C_DASH)
    ax.set_ylim(0, 6); ax.set_xlim(tmin, tmax)
    ax.set_yticks([1.0, 2.5, 5.0])
    ax.set_yticklabels(['360p', '720p', '1080p'], fontsize=8)
    ax.set_ylabel('Rendition', fontsize=9)
    ax.set_xlabel('Time (s)', fontsize=9)
    ax.set_title(ROW_TITLES[4], fontsize=9.5, fontweight='semibold', pad=4)
    add_rsu_bands(ax, t, rsu, tmax, (0, 6), show_label=False)
    add_handover_lines(ax, trans, (0, 6), show_label=False)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches='tight', facecolor='white', edgecolor='none')
    print(f'saved -> {out_path}  Net QoE={net_qoe:.2f} (avg {net_qoe/len(qoe):.3f}, K={len(qoe)})')
    plt.close(fig)


if __name__ == '__main__':
    rows = load(CSV_PATH)
    seg = reconstruct_segments(rows)
    make_plot(rows, seg, OUT_PATH)
