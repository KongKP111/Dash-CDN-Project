#!/usr/bin/env python3
"""
plot_baseline.py  --  plot the baseline CSV (works for preview OR real run)

    python3 plot_baseline.py baseline_run.csv
    python3 plot_baseline.py baseline_run.csv -o baseline_run.png
"""
import csv
import argparse
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def load(path):
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            rows.append(r)
    return rows


def plot(rows, out):
    x    = [float(r["x"]) for r in rows]
    rssi = [float(r["rssi"]) for r in rows]
    rate = [float(r["rate_mbps"]) for r in rows]
    qoe  = [float(r["qoe"]) for r in rows]
    loss = [float(r["loss"]) for r in rows]
    thr  = [float(r["throughput"]) for r in rows]

    rung_y = {"360p": 0, "720p": 1, "1080p": 2}
    rend_idx = [rung_y[r["rendition"]] for r in rows]

    fig, ax = plt.subplots(4, 1, figsize=(10, 11), sharex=True)
    fig.suptitle("DASH ABR Baseline - 1 vehicle, 1 RSU (300 m), 1 m/s",
                 fontsize=14, fontweight="bold")

    ax[0].step(x, rend_idx, where="post", color="#1f4e9c", linewidth=2.2)
    ax[0].set_yticks([0, 1, 2]); ax[0].set_yticklabels(["360p", "720p", "1080p"])
    ax[0].set_ylabel("Quality"); ax[0].set_ylim(-0.4, 2.4)
    ax[0].axvline(0, color="gray", ls="--", lw=1)
    ax[0].text(0, 2.25, "RSU", ha="center", fontsize=9, color="gray")
    ax[0].set_title("Selected rendition vs position", fontsize=11)
    ax[0].grid(alpha=0.3)

    ax2 = ax[1]
    ax2.plot(x, rssi, color="#c0392b", lw=2, label="RSSI (dBm)")
    ax2.set_ylabel("RSSI (dBm)", color="#c0392b")
    ax2.axhline(-70, color="#c0392b", ls=":", lw=1, alpha=0.6)
    ax2.axvline(0, color="gray", ls="--", lw=1)
    ax2b = ax2.twinx()
    ax2b.plot(x, thr, color="#27ae60", lw=1.6, alpha=0.8)
    ax2b.set_ylabel("Throughput (Mbps)", color="#27ae60")
    ax2.set_title("Channel: RSSI & available throughput", fontsize=11)
    ax2.grid(alpha=0.3)

    ax[2].plot(x, qoe, color="#8e44ad", lw=2)
    ax[2].set_ylabel("QoE (MOS)"); ax[2].set_ylim(0.8, 5.2)
    ax[2].axvline(0, color="gray", ls="--", lw=1)
    ax[2].set_title("Quality of Experience", fontsize=11)
    ax[2].grid(alpha=0.3)

    ax[3].plot(x, loss, color="#d35400", lw=2)
    ax[3].set_ylabel("Packet loss (%)")
    ax[3].set_xlabel("Vehicle position x (m)   |   RSU at x = 0")
    ax[3].axvline(0, color="gray", ls="--", lw=1)
    ax[3].set_title("Packet loss", fontsize=11)
    ax[3].grid(alpha=0.3)

    plt.tight_layout(rect=[0, 0, 1, 0.97])
    plt.savefig(out, dpi=130)
    print("saved", out)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("csv")
    p.add_argument("-o", "--out", default=None)
    a = p.parse_args()
    out = a.out or a.csv.rsplit(".", 1)[0] + ".png"
    plot(load(a.csv), out)
