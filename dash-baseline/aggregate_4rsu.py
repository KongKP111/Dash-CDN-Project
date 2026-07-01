#!/usr/bin/env python3
"""
aggregate_4rsu.py -- combine N 4-RSU baseline runs into mean +/- spread (IEEE-style).
    python3 aggregate_4rsu.py results_4rsu                # combined 4-panel
    python3 aggregate_4rsu.py results_4rsu --separate      # + 4 standalone figures
Same structure as aggregate_runs.py (1-RSU) but marks all 4 RSU centers and
reports handover crossing rate per position. Writes aggregate_4rsu.csv and
aggregate_4rsu.png (+ aggregate_4rsu_<panel>.png if --separate).
"""
import os, csv, glob, argparse
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import baseline_4rsu_model as M4


def load(path):
    with open(path) as f:
        return list(csv.DictReader(f))


def stack(runs, key, missing_if_neg=False):
    xs = sorted({round(float(r["x"]), 1) for run in runs for r in run})
    idx = {x: j for j, x in enumerate(xs)}
    M = np.full((len(runs), len(xs)), np.nan)
    for i, run in enumerate(runs):
        for r in run:
            v = float(r.get(key, "nan"))
            if missing_if_neg and v < 0:
                v = np.nan
            M[i, idx[round(float(r["x"]), 1)]] = v
    return np.array(xs), M


def sm(y, w=7):
    y = np.asarray(y, float)
    if len(y) < w: return y
    k = np.ones(w) / w
    return np.convolve(np.pad(y, w // 2, mode="edge"), k, "valid")[:len(y)]


def _mark_rsus(ax):
    for rx in M4.RSU_X:
        ax.axvline(rx, color="gray", ls="--", lw=1)


# ---- per-panel drawing -------------------------------------------------------
def panel_quality(ax, S, standalone=False):
    ax.plot(S["x"], S["q_mean"], color="#1f4e9c", lw=2)
    ax.fill_between(S["x"], S["q_mean"] - S["q_std"], S["q_mean"] + S["q_std"],
                    color="#1f4e9c", alpha=0.2, label="+/- std")
    ax.set_yticks([0, 1, 2]); ax.set_yticklabels(["360p", "720p", "1080p"])
    ax.set_ylabel("Quality"); ax.set_ylim(-0.4, 2.4)
    _mark_rsus(ax)
    for i, rx in enumerate(M4.RSU_X):
        ax.text(rx, 2.25, "RSU%d" % (i + 1), ha="center", fontsize=8, color="gray")
    ax.set_title("Mean selected rendition (n=%d)" % S["n"], fontsize=11)
    ax.legend(loc="upper right", fontsize=8); ax.grid(alpha=0.3)
    if standalone: ax.set_xlabel("Vehicle position x (m)")


def panel_rssi_bw(ax, S, standalone=False):
    ax.plot(S["x"], S["rssi_mean"], color="#c0392b", lw=2)
    ax.set_ylabel("RSSI (dBm)", color="#c0392b")
    _mark_rsus(ax)
    axb = ax.twinx()
    axb.plot(S["x"], S["bw_mean"], color="#27ae60", lw=1.6, alpha=0.85)
    axb.set_ylabel("Imposed BW (Mbps)", color="#27ae60")
    ax.set_title("Mean RSSI & imposed bandwidth", fontsize=11); ax.grid(alpha=0.3)
    if standalone: ax.set_xlabel("Vehicle position x (m)")


def panel_loss(ax, S, standalone=False):
    lm, lc = sm(S["loss_mean"]), sm(S["loss_ci"])
    ax.plot(S["x"], lm, color="#d35400", lw=2.2, label="mean")
    ax.fill_between(S["x"], np.clip(lm - lc, 0, 100), np.clip(lm + lc, 0, 100),
                    color="#d35400", alpha=0.25, label="95% CI")
    ax.set_ylabel("Packet loss (%)"); ax.set_ylim(-3, 103)
    _mark_rsus(ax)
    ax.set_title("Wireless ICMP loss (mean +/- 95%% CI, n=%d)" % S["n"], fontsize=11)
    ax.legend(loc="upper center", fontsize=8, ncol=2); ax.grid(alpha=0.3)
    if standalone: ax.set_xlabel("Vehicle position x (m)")


def panel_rebuffer(ax, S, standalone=False):
    sp = sm(S["stall_pct"])
    ax.plot(S["x"], sp, color="#6c3483", lw=2.2)
    ax.fill_between(S["x"], 0, sp, color="#6c3483", alpha=0.2)
    ax.set_ylabel("Stall probability (%)"); ax.set_ylim(-3, 103)
    _mark_rsus(ax)
    ax.set_title("Rebuffering frequency (%% of runs stalling, n=%d)" % S["n"], fontsize=11)
    ax.grid(alpha=0.3)
    if standalone: ax.set_xlabel("Vehicle position x (m)")


PANELS = [("quality", panel_quality), ("rssi", panel_rssi_bw),
          ("loss", panel_loss), ("rebuffer", panel_rebuffer)]


def main(folder, out, separate=False):
    files = sorted(glob.glob(os.path.join(folder, "run_*.csv")))
    if not files:
        print("no run_*.csv in", folder); return
    runs = [load(f) for f in files]; n = len(runs)
    print("loaded %d runs" % n)

    x, RSSI = stack(runs, "rssi")
    _, BW    = stack(runs, "bw_mbps")
    _, LOSS  = stack(runs, "loss")
    _, QIDX  = stack(runs, "quality_idx", missing_if_neg=True)
    _, STALL = stack(runs, "stall")
    _, HO    = stack(runs, "handover")

    loss_std = np.nanstd(LOSS, axis=0)
    nval = np.sum(~np.isnan(LOSS), axis=0)
    S = dict(
        x=x, n=n,
        rssi_mean=np.nanmean(RSSI, axis=0),
        bw_mean=np.nanmean(BW, axis=0),
        loss_mean=np.nanmean(LOSS, axis=0),
        loss_ci=1.96 * loss_std / np.sqrt(np.maximum(nval, 1)),
        q_mean=np.nanmean(QIDX, axis=0),
        q_std=np.nanstd(QIDX, axis=0),
        stall_pct=100.0 * np.nanmean(STALL, axis=0),
        ho_pct=100.0 * np.nanmean(HO, axis=0),
    )

    with open("aggregate_4rsu.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["x", "rssi_mean", "bw_mean", "q_mean", "q_std",
                    "loss_mean", "loss_ci95", "stall_pct", "handover_pct", "n"])
        for i in range(len(x)):
            w.writerow([x[i], round(S["rssi_mean"][i], 2), round(S["bw_mean"][i], 3),
                        round(S["q_mean"][i], 3), round(S["q_std"][i], 3),
                        round(S["loss_mean"][i], 3), round(S["loss_ci"][i], 3),
                        round(S["stall_pct"][i], 2), round(S["ho_pct"][i], 2), int(nval[i])])
    print("wrote aggregate_4rsu.csv")

    fig, ax = plt.subplots(4, 1, figsize=(11, 12.5), sharex=True)
    fig.suptitle("DASH ABR 4-RSU Handover Baseline - aggregate of %d runs (mean +/- spread)" % n,
                 fontsize=14, fontweight="bold")
    for a, (_, fn) in zip(ax, PANELS):
        fn(a, S)
    ax[-1].set_xlabel("Vehicle position x (m)   |   dashed = RSU center")
    plt.tight_layout(rect=[0, 0, 1, 0.975]); plt.savefig(out, dpi=130)
    print("saved", out)

    if separate:
        base = out.rsplit(".", 1)[0]
        for name, fn in PANELS:
            f1, a1 = plt.subplots(figsize=(7.2, 3.6))
            fn(a1, S, standalone=True)
            f1.tight_layout()
            p = "%s_%s.png" % (base, name)
            f1.savefig(p, dpi=150); plt.close(f1)
            print("saved", p)


if __name__ == "__main__":
    a = argparse.ArgumentParser()
    a.add_argument("folder"); a.add_argument("-o", "--out", default="aggregate_4rsu.png")
    a.add_argument("--separate", action="store_true",
                   help="also save each panel as a standalone figure")
    args = a.parse_args()
    main(args.folder, args.out, separate=args.separate)
