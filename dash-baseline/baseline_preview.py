#!/usr/bin/env python3
"""
baseline_preview.py
-------------------
Runs the -300 -> +300 m trajectory using baseline_model.py (NO Mininet, no sudo)
so we can confirm the expected curve BEFORE running the real experiment.

Produces:  baseline_preview.csv  and  baseline_preview.png
"""
import csv
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import baseline_model as M


def run():
    abr = M.ABRController()
    rows = []
    prev_rend = None

    t = 0.0
    x = M.START_X
    while x <= M.END_X + 1e-9:
        d = abs(x - M.RSU_POS_X)
        rssi = M.rssi_from_distance(d)
        thr  = M.throughput_from_rssi(rssi)
        rend, br, stall = abr.update(thr)
        switched = (prev_rend is not None and rend != prev_rend)
        q = M.qoe(rend, switched, stall)
        loss = M.loss_from_rssi(rssi)

        rows.append(dict(t=t, x=x, dist=d, rssi=rssi, throughput=thr,
                         rendition=rend, rate_mbps=br, stall=int(stall),
                         qoe=q, loss=loss))
        prev_rend = rend
        t += M.SAMPLE_DT
        x += M.SPEED_MPS * M.SAMPLE_DT

    return rows


def save_csv(rows, path="baseline_preview.csv"):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def plot(rows, path="baseline_preview.png"):
    x      = [r["x"] for r in rows]
    rssi   = [r["rssi"] for r in rows]
    rate   = [r["rate_mbps"] for r in rows]
    qoe    = [r["qoe"] for r in rows]
    loss   = [r["loss"] for r in rows]
    thr    = [r["throughput"] for r in rows]

    rung_y = {"360p": 0, "720p": 1, "1080p": 2}
    rend_idx = [rung_y[r["rendition"]] for r in rows]

    fig, ax = plt.subplots(4, 1, figsize=(10, 11), sharex=True)
    fig.suptitle("DASH ABR Baseline — 1 vehicle, 1 RSU (300 m), 1 m/s",
                 fontsize=14, fontweight="bold")

    # Panel 1: rendition staircase  (the sketch)
    ax[0].step(x, rend_idx, where="post", color="#1f4e9c", linewidth=2.2)
    ax[0].set_yticks([0, 1, 2]); ax[0].set_yticklabels(["360p", "720p", "1080p"])
    ax[0].set_ylabel("Quality")
    ax[0].set_ylim(-0.4, 2.4)
    ax[0].axvline(0, color="gray", ls="--", lw=1)
    ax[0].text(0, 2.25, "RSU", ha="center", fontsize=9, color="gray")
    ax[0].set_title("Selected rendition vs position", fontsize=11)
    ax[0].grid(alpha=0.3)

    # Panel 2: RSSI + throughput
    ax2 = ax[1]
    ax2.plot(x, rssi, color="#c0392b", lw=2, label="RSSI (dBm)")
    ax2.set_ylabel("RSSI (dBm)", color="#c0392b")
    ax2.axhline(-70, color="#c0392b", ls=":", lw=1, alpha=0.6)
    ax2.axvline(0, color="gray", ls="--", lw=1)
    ax2b = ax2.twinx()
    ax2b.plot(x, thr, color="#27ae60", lw=1.6, alpha=0.8, label="Imposed BW (Mbps)")
    ax2b.set_ylabel("Imposed BW (Mbps)", color="#27ae60")
    ax2.set_title("Channel: RSSI & imposed bandwidth", fontsize=11)
    ax2.grid(alpha=0.3)

    # Panel 3: QoE
    ax[2].plot(x, qoe, color="#8e44ad", lw=2)
    ax[2].set_ylabel("QoE (MOS)")
    ax[2].set_ylim(0.8, 5.2)
    ax[2].axvline(0, color="gray", ls="--", lw=1)
    ax[2].set_title("Quality of Experience", fontsize=11)
    ax[2].grid(alpha=0.3)

    # Panel 4: loss
    ax[3].plot(x, loss, color="#d35400", lw=2)
    ax[3].set_ylabel("Packet loss (%)")
    ax[3].set_xlabel("Vehicle position x (m)   |   RSU at x = 0")
    ax[3].axvline(0, color="gray", ls="--", lw=1)
    ax[3].set_title("Packet loss", fontsize=11)
    ax[3].grid(alpha=0.3)

    plt.tight_layout(rect=[0, 0, 1, 0.97])
    plt.savefig(path, dpi=130)
    print("saved", path)


if __name__ == "__main__":
    rows = run()
    save_csv(rows)
    plot(rows)
    # quick text summary of the staircase transitions
    last = None
    print("\nRendition transitions (position -> rendition):")
    for r in rows:
        if r["rendition"] != last:
            print(f"  x={r['x']:+6.0f} m  rssi={r['rssi']:6.1f}  thr={r['throughput']:4.1f}  -> {r['rendition']}"
                  + ("  [STALL]" if r["stall"] else ""))
            last = r["rendition"]
