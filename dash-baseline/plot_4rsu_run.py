#!/usr/bin/env python3
"""
plot_4rsu_run.py -- plot a single REAL 4-RSU streaming run (baseline_4rsu_run.csv).
    python3 plot_4rsu_run.py baseline_4rsu_run.csv              # combined 4-panel
    python3 plot_4rsu_run.py baseline_4rsu_run.csv --separate   # + 4 standalone figures
Panels: quality | RSSI+imposed BW | wireless loss | rebuffering (buffer & stalls)
RSU positions and handover crossings (from the CSV's `handover` column) are
marked on every panel.
"""
import csv, argparse
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import baseline_4rsu_model as M4

SMOOTH_W = 9


def load(path):
    with open(path) as f:
        return list(csv.DictReader(f))


def smooth(y, w=SMOOTH_W):
    y = np.asarray(y, float)
    if w <= 1 or len(y) < w:
        return y
    k = np.ones(w) / w
    return np.convolve(np.pad(y, w // 2, mode="edge"), k, "valid")[:len(y)]


def _mark_rsus(ax, handover_x):
    for rx in M4.RSU_X:
        ax.axvline(rx, color="gray", ls="--", lw=1)
    for hx in handover_x:
        ax.axvline(hx, color="#e74c3c", ls="-", lw=1.2, alpha=0.7)


def panel_quality(ax, D, standalone=False):
    xs = [xi for xi, yi in zip(D["x"], D["qy"]) if yi is not None]
    ys = [yi for yi in D["qy"] if yi is not None]
    ax.step(xs, ys, where="post", color="#1f4e9c", lw=2.2)
    ax.set_yticks([0, 1, 2]); ax.set_yticklabels(["360p", "720p", "1080p"])
    ax.set_ylabel("Quality"); ax.set_ylim(-0.4, 2.4)
    _mark_rsus(ax, D["handover_x"])
    for i, rx in enumerate(M4.RSU_X):
        ax.text(rx, 2.25, "RSU%d" % (i + 1), ha="center", fontsize=8, color="gray")
    ax.set_title("Rendition chosen by VLC (from server access log)", fontsize=11)
    ax.grid(alpha=0.3)
    if standalone: ax.set_xlabel("Vehicle position x (m)")


def panel_rssi_bw(ax, D, standalone=False):
    ax.plot(D["x"], D["rssi"], color="#c0392b", lw=2)
    ax.set_ylabel("RSSI (dBm)", color="#c0392b")
    axb = ax.twinx()
    axb.plot(D["x"], D["bw"], color="#27ae60", lw=1.6, alpha=0.85)
    axb.set_ylabel("Imposed BW (Mbps)", color="#27ae60")
    _mark_rsus(ax, D["handover_x"])
    ax.set_title("Live RSSI & imposed bandwidth", fontsize=11)
    ax.grid(alpha=0.3)
    if standalone: ax.set_xlabel("Vehicle position x (m)")


def panel_loss(ax, D, standalone=False):
    ax.plot(D["x"], D["loss"], color="#d35400", lw=0.8, alpha=0.25, label="raw (%.1fs)" % M4.SAMPLE_DT)
    ax.plot(D["x"], smooth(D["loss"]), color="#d35400", lw=2.2,
            label="moving avg")
    ax.set_ylabel("Packet loss (%)"); ax.set_ylim(-3, 103)
    _mark_rsus(ax, D["handover_x"])
    ax.set_title("Wireless ICMP packet loss (protected probe)", fontsize=11)
    ax.legend(loc="upper center", fontsize=8, ncol=2); ax.grid(alpha=0.3)
    if standalone: ax.set_xlabel("Vehicle position x (m)")


def panel_rebuffer(ax, D, standalone=False):
    ax.plot(D["x"], D["buf"], color="#6c3483", lw=2, label="buffer level (s)")
    for xi, st in zip(D["x"], D["stall"]):
        if st:
            ax.axvspan(xi - 0.5, xi + 0.5, color="#e74c3c", alpha=0.25)
    ax.axvspan(0, 0, color="#e74c3c", alpha=0.25, label="stall second")
    ax.set_ylabel("Buffer (s)")
    _mark_rsus(ax, D["handover_x"])
    ax.set_title("Rebuffering (buffer-occupancy model)", fontsize=11)
    ax.legend(loc="upper center", fontsize=8, ncol=2); ax.grid(alpha=0.3)
    if standalone: ax.set_xlabel("Vehicle position x (m)")


PANELS = [("quality", panel_quality), ("rssi", panel_rssi_bw),
          ("loss", panel_loss), ("rebuffer", panel_rebuffer)]


def plot(rows, out, separate=False):
    handover_x = [float(r["x"]) for r in rows if int(r.get("handover", 0))]
    D = dict(
        x=[float(r["x"]) for r in rows],
        rssi=[float(r["rssi"]) for r in rows],
        bw=[float(r["bw_mbps"]) for r in rows],
        loss=[float(r["loss"]) for r in rows],
        buf=[float(r.get("buffer_s", 0)) for r in rows],
        stall=[int(r.get("stall", 0)) for r in rows],
        qy=[int(r["quality_idx"]) if int(r["quality_idx"]) >= 0 else None for r in rows],
        handover_x=handover_x,
    )

    fig, ax = plt.subplots(4, 1, figsize=(11, 12), sharex=True)
    fig.suptitle("DASH ABR - 4-RSU handover baseline (REAL stream) - 1 vehicle @ %.0f km/h"
                 % M4.SPEED_KMH, fontsize=14, fontweight="bold")
    for a, (_, fn) in zip(ax, PANELS):
        fn(a, D)
    ax[-1].set_xlabel("Vehicle position x (m)   |   dashed = RSU center, red = handover")
    plt.tight_layout(rect=[0, 0, 1, 0.975]); plt.savefig(out, dpi=130)
    print("saved", out)
    print("handovers detected at x =", handover_x)

    if separate:
        base = out.rsplit(".", 1)[0]
        for name, fn in PANELS:
            f1, a1 = plt.subplots(figsize=(7.2, 3.6))
            fn(a1, D, standalone=True)
            f1.tight_layout()
            p = "%s_%s.png" % (base, name)
            f1.savefig(p, dpi=150); plt.close(f1)
            print("saved", p)


if __name__ == "__main__":
    a = argparse.ArgumentParser()
    a.add_argument("csv"); a.add_argument("-o", "--out", default=None)
    a.add_argument("--separate", action="store_true",
                   help="also save each panel as a standalone figure")
    args = a.parse_args()
    out = args.out or args.csv.rsplit(".", 1)[0] + ".png"
    plot(load(args.csv), out, separate=args.separate)
