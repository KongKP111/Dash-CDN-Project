#!/usr/bin/env python3
"""
plot_handover_times.py -- control-plane figure from the Ryu controller's own
log (handover_times.csv): OFPBarrierRequest -> OFPBarrierReply round-trip
time for each handover, i.e. how long the SDN controller took to repair the
stale flow once it detected the vehicle's MAC on a new port.

    python3 plot_handover_times.py results_4rsu/handover_times.csv

Each physical handover produces TWO rows (two dpids independently detect and
repair the move): the RSU-side access-point bridge, and the core/backbone
switch (the fork point that had the original "stale flow forever" bug this
whole 4-RSU baseline was built to fix). Rows are grouped into handover
events per run_id by wall-clock proximity, then labelled by handover order
(1st crossing = RSU1->RSU2, 2nd = RSU2->RSU3, 3rd = RSU3->RSU4).
"""
import csv
import argparse
import collections
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

GROUP_WINDOW_S = 1.0   # rows within this many seconds belong to the same handover event
CROSSING_LABELS = ["RSU1→RSU2", "RSU2→RSU3", "RSU3→RSU4"]


def load(path):
    with open(path) as f:
        return list(csv.DictReader(f))


def group_by_run(rows):
    by_run = collections.defaultdict(list)
    for r in rows:
        by_run[r["run_id"]].append(r)
    for run_id in by_run:
        by_run[run_id].sort(key=lambda r: float(r["wall_ts"]))
    return by_run


def classify_events(by_run):
    """-> list of dicts: {run_id, crossing_idx, ap_ms, core_ms}"""
    events = []
    for run_id, rows in by_run.items():
        # cluster consecutive rows into handover events by time proximity
        clusters = []
        cur = []
        prev_ts = None
        for r in rows:
            ts = float(r["wall_ts"])
            if prev_ts is not None and ts - prev_ts > GROUP_WINDOW_S:
                clusters.append(cur); cur = []
            cur.append(r)
            prev_ts = ts
        if cur:
            clusters.append(cur)

        for i, cluster in enumerate(clusters):
            ap_ms = [float(r["handover_exec_ms"]) for r in cluster if r["dpid"] != "1"]
            core_ms = [float(r["handover_exec_ms"]) for r in cluster if r["dpid"] == "1"]
            events.append(dict(
                run_id=run_id,
                crossing_idx=i,
                ap_ms=ap_ms[0] if ap_ms else None,
                core_ms=core_ms[0] if core_ms else None,
            ))
    return events


def main(path, out):
    rows = load(path)
    by_run = group_by_run(rows)
    events = classify_events(by_run)
    n_runs = len(by_run)

    n_crossings = max(e["crossing_idx"] for e in events) + 1
    ap_by_crossing = [[] for _ in range(n_crossings)]
    core_by_crossing = [[] for _ in range(n_crossings)]
    for e in events:
        if e["ap_ms"] is not None:
            ap_by_crossing[e["crossing_idx"]].append(e["ap_ms"])
        if e["core_ms"] is not None:
            core_by_crossing[e["crossing_idx"]].append(e["core_ms"])

    labels = [CROSSING_LABELS[i] if i < len(CROSSING_LABELS) else "crossing %d" % (i + 1)
              for i in range(n_crossings)]

    ap_mean = [np.mean(v) if v else 0 for v in ap_by_crossing]
    ap_std  = [np.std(v) if v else 0 for v in ap_by_crossing]
    core_mean = [np.mean(v) if v else 0 for v in core_by_crossing]
    core_std  = [np.std(v) if v else 0 for v in core_by_crossing]

    x = np.arange(n_crossings)
    w = 0.35

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    ax1.bar(x - w/2, ap_mean, w, yerr=ap_std, capsize=4,
            label="RSU-side AP bridge", color="#2980b9")
    ax1.bar(x + w/2, core_mean, w, yerr=core_std, capsize=4,
            label="Core/backbone switch (fork point)", color="#c0392b")
    ax1.set_xticks(x); ax1.set_xticklabels(labels)
    ax1.set_ylabel("Handover execution time (ms)\n(flow-mod → barrier reply)")
    ax1.set_title("Mean handover exec time by crossing (n=%d runs)" % n_runs)
    ax1.legend(fontsize=9); ax1.grid(alpha=0.3, axis="y")

    all_ap = [v for group in ap_by_crossing for v in group]
    all_core = [v for group in core_by_crossing for v in group]
    ax2.boxplot([all_ap, all_core], labels=["RSU-side AP bridge", "Core switch"])
    ax2.set_ylabel("Handover execution time (ms)")
    ax2.set_title("Distribution over all %d handovers (%d runs × %d crossings)"
                   % (len(all_ap), n_runs, n_crossings))
    ax2.grid(alpha=0.3, axis="y")

    fig.suptitle("Control-plane handover execution time (Ryu OFPBarrierRequest/Reply)",
                 fontsize=13, fontweight="bold")
    plt.tight_layout(rect=[0, 0, 1, 0.95])
    plt.savefig(out, dpi=130)
    print("saved", out)

    print()
    print("Summary (ms):")
    for i, lab in enumerate(labels):
        print("  %s: AP-side %.2f +/- %.2f (n=%d) | core %.2f +/- %.2f (n=%d)"
              % (lab, ap_mean[i], ap_std[i], len(ap_by_crossing[i]),
                 core_mean[i], core_std[i], len(core_by_crossing[i])))
    print("  overall: AP-side %.2f +/- %.2f (n=%d) | core %.2f +/- %.2f (n=%d)"
          % (np.mean(all_ap), np.std(all_ap), len(all_ap),
             np.mean(all_core), np.std(all_core), len(all_core)))


if __name__ == "__main__":
    a = argparse.ArgumentParser()
    a.add_argument("csv", help="handover_times.csv (control-plane log)")
    a.add_argument("-o", "--out", default="handover_times.png")
    args = a.parse_args()
    main(args.csv, args.out)
