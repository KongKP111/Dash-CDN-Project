#!/usr/bin/env python3
"""
plot_run.py -- plot a single REAL streaming run (baseline_run.csv).
    python3 plot_run.py baseline_run.csv              # combined 4-panel figure
    python3 plot_run.py baseline_run.csv --separate   # + 4 standalone figures
Panels: quality | RSSI+imposed BW | wireless loss | rebuffering (buffer & stalls)
"""
import csv, argparse, os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

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


# ---- per-panel drawing functions (reused by combined & separate) -------------
def panel_quality(ax, D, standalone=False):
    xs = [xi for xi, yi in zip(D["x"], D["qy"]) if yi is not None]
    ys = [yi for yi in D["qy"] if yi is not None]
    ax.step(xs, ys, where="post", color="#1f4e9c", lw=2.2)
    ax.set_yticks([0, 1, 2]); ax.set_yticklabels(["360p", "720p", "1080p"])
    ax.set_ylabel("Quality"); ax.set_ylim(-0.4, 2.4)
    ax.axvline(0, color="gray", ls="--", lw=1)
    ax.text(0, 2.25, "RSU", ha="center", fontsize=9, color="gray")
    ax.set_title("Rendition chosen by VLC (from server access log)", fontsize=11)
    ax.grid(alpha=0.3)
    if standalone: ax.set_xlabel("Vehicle position x (m)   |   RSU at x = 0")


def panel_rssi_bw(ax, D, standalone=False):
    ax.plot(D["x"], D["rssi"], color="#c0392b", lw=2)
    ax.set_ylabel("RSSI (dBm)", color="#c0392b")
    ax.axhline(-70, color="#c0392b", ls=":", lw=1, alpha=0.6)
    ax.axvline(0, color="gray", ls="--", lw=1)
    axb = ax.twinx()
    axb.plot(D["x"], D["bw"], color="#27ae60", lw=1.6, alpha=0.85)
    axb.set_ylabel("Imposed BW (Mbps)", color="#27ae60")
    ax.set_title("Live RSSI & imposed bandwidth", fontsize=11)
    ax.grid(alpha=0.3)
    if standalone: ax.set_xlabel("Vehicle position x (m)   |   RSU at x = 0")


def panel_loss(ax, D, standalone=False):
    ax.plot(D["x"], D["loss"], color="#d35400", lw=0.8, alpha=0.25, label="raw (1 s)")
    ax.plot(D["x"], smooth(D["loss"]), color="#d35400", lw=2.2,
            label="moving avg (%d s)" % SMOOTH_W)
    ax.set_ylabel("Packet loss (%)"); ax.set_ylim(-3, 103)
    ax.axvline(0, color="gray", ls="--", lw=1)
    ax.set_title("Wireless ICMP packet loss (protected probe)", fontsize=11)
    ax.legend(loc="upper center", fontsize=8, ncol=2); ax.grid(alpha=0.3)
    if standalone: ax.set_xlabel("Vehicle position x (m)   |   RSU at x = 0")


def panel_rebuffer(ax, D, standalone=False):
    ax.plot(D["x"], D["buf"], color="#6c3483", lw=2, label="buffer level (s)")
    for xi, st in zip(D["x"], D["stall"]):
        if st:
            ax.axvspan(xi - 0.5, xi + 0.5, color="#e74c3c", alpha=0.25)
    ax.axvspan(0, 0, color="#e74c3c", alpha=0.25, label="stall second")
    ax.set_ylabel("Buffer (s)")
    ax.axvline(0, color="gray", ls="--", lw=1)
    ax.set_title("Rebuffering (buffer-occupancy model)", fontsize=11)
    ax.legend(loc="upper center", fontsize=8, ncol=2); ax.grid(alpha=0.3)
    if standalone: ax.set_xlabel("Vehicle position x (m)   |   RSU at x = 0")


PANELS = [("quality", panel_quality), ("rssi", panel_rssi_bw),
          ("loss", panel_loss), ("rebuffer", panel_rebuffer)]


def plot(rows, out, separate=False):
    D = dict(
        x=[float(r["x"]) for r in rows],
        rssi=[float(r["rssi"]) for r in rows],
        bw=[float(r["bw_mbps"]) for r in rows],
        loss=[float(r["loss"]) for r in rows],
        buf=[float(r.get("buffer_s", 0)) for r in rows],
        stall=[int(r.get("stall", 0)) for r in rows],
        qy=[int(r["quality_idx"]) if int(r["quality_idx"]) >= 0 else None for r in rows],
    )

    # combined 4-panel
    fig, ax = plt.subplots(4, 1, figsize=(10, 12), sharex=True)
    fig.suptitle("DASH ABR Baseline (REAL stream) - 1 vehicle, 1 RSU, 1 m/s",
                 fontsize=14, fontweight="bold")
    for a, (_, fn) in zip(ax, PANELS):
        fn(a, D)
    ax[-1].set_xlabel("Vehicle position x (m)   |   RSU at x = 0")
    plt.tight_layout(rect=[0, 0, 1, 0.975]); plt.savefig(out, dpi=130)
    print("saved", out)

    # separate standalone figures
    if separate:
        base = out.rsplit(".", 1)[0]
        for name, fn in PANELS:
            f1, a1 = plt.subplots(figsize=(6.4, 3.6))
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
