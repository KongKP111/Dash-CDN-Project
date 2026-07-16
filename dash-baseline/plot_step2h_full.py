import csv
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

RSU_X = [0, 500, 1000, 1500]
HO_X = [250, 750, 1250]
C_MAIN = "#1baf7a"
C_RSSI = "#2a78d6"
C_LOSS = "#e34948"
C_OUTAGE = "#eb6834"
C_BUF = "#4a3aa7"

DARK_BG = "#141414"; CARD_BG = "#1c1c1c"; GRID_C = "#2c2c2a"
TEXT_C = "#e8e6e0"; MUTED_C = "#8a897f"

rows = list(csv.DictReader(open("runs/2026-07-08_bw-mapping-smoke-tests/test04_step2h.csv")))
x = np.array([float(r["x"]) for r in rows])
rssi = np.array([float(r["rssi"]) for r in rows])
bw = np.array([float(r["bw_mbps"]) for r in rows])
q = np.array([int(r["quality_idx"]) for r in rows])
loss = np.array([float(r["loss"]) for r in rows])
stall = np.array([int(r["stall"]) for r in rows])
buf = np.array([float(r["buffer_s"]) for r in rows])
ho = np.array([int(r["handover"]) for r in rows])

OUTAGE_THRESH = 50.0
outage = (loss >= OUTAGE_THRESH).astype(float)
cum_outage_s = np.cumsum(outage) * 0.5
total_dur = len(rows) * 0.5
n_ho = int(ho.sum())
outage_pct = 100 * cum_outage_s[-1] / total_dur
rebuffer_pct = 100 * (stall.sum() * 0.5) / total_dur

fig, axes = plt.subplots(6, 1, figsize=(13, 17), facecolor=DARK_BG,
                          gridspec_kw={"hspace": 0.6, "top": 0.95, "bottom": 0.035})

fig.suptitle("Step2h — full metric dashboard (test04_step2h.csv, 2026-07-08)",
             fontsize=16, fontweight="bold", color=TEXT_C, x=0.02, ha="left", y=0.995)
fig.text(0.02, 0.978,
          "4-RSU, 20 km/hr, n=1 (single confirmed run) — Handovers: %d | Cum. outage: %.1fs (%.2f%%) | Rebuffer ratio: %.1f%%"
          % (n_ho, cum_outage_s[-1], outage_pct, rebuffer_pct),
          fontsize=10.5, color=MUTED_C, ha="left")

def style(ax, title):
    ax.set_facecolor(CARD_BG)
    ax.set_title(title, color=TEXT_C, fontsize=11.5, loc="left", pad=8)
    ax.tick_params(colors=MUTED_C)
    ax.grid(color=GRID_C, linewidth=0.6)
    for s in ax.spines.values(): s.set_color(GRID_C)
    ax.set_xlim(-300, 1800)
    for rx in RSU_X:
        ax.axvline(rx, color=MUTED_C, ls="--", lw=0.8, alpha=0.6)
    for hx in HO_X:
        ax.axvline(hx, color=C_OUTAGE, ls=":", lw=0.9, alpha=0.7)

# 1. Quality
ax = axes[0]; style(ax, "Quality")
ax.step(x, q, where="post", color=C_MAIN, lw=2)
ax.set_yticks([0,1,2]); ax.set_yticklabels(["360p","720p","1080p"], color=TEXT_C)
ax.set_ylim(-0.3, 2.3)
for i, rx in enumerate(RSU_X):
    ax.text(rx, 2.15, "RSU%d"%(i+1), color=MUTED_C, fontsize=8, ha="center")

# 2. RSSI
ax = axes[1]; style(ax, "RSSI (dBm)")
ax.plot(x, rssi, color=C_RSSI, lw=1.6)
ax.set_ylabel("dBm", color=MUTED_C, fontsize=9)

# 3. Bandwidth
ax = axes[2]; style(ax, "Imposed bandwidth (bw_mbps)")
ax.step(x, bw, where="post", color=C_MAIN, lw=1.6)
ax.set_ylabel("Mbps", color=MUTED_C, fontsize=9)
ax.set_ylim(0, 10.8)

# 4. Loss + outage threshold
ax = axes[3]; style(ax, "Packet loss %% (outage threshold = %.0f%%)" % OUTAGE_THRESH)
ax.fill_between(x, 0, loss, color=C_LOSS, alpha=0.35, step="post")
ax.plot(x, loss, color=C_LOSS, lw=1.2)
ax.axhline(OUTAGE_THRESH, color=C_OUTAGE, ls="--", lw=1, alpha=0.8)
ax.set_ylabel("%", color=MUTED_C, fontsize=9)
ax.set_ylim(0, 105)

# 5. Cumulative outage
ax = axes[4]; style(ax, "Cumulative outage time (cum_outage_s)")
ax.plot(x, cum_outage_s, color=C_OUTAGE, lw=2)
ax.fill_between(x, 0, cum_outage_s, color=C_OUTAGE, alpha=0.15)
ax.set_ylabel("seconds", color=MUTED_C, fontsize=9)

# 6. Buffer (rebuffer context) + handover markers
ax = axes[5]; style(ax, "Playback buffer (buffer_s) -- rebuffer_ratio_pct = %.1f%%" % rebuffer_pct)
ax.plot(x, buf, color=C_BUF, lw=1.6)
ax.set_ylabel("seconds", color=MUTED_C, fontsize=9)
ax.set_xlabel("Position x (m)", color=MUTED_C, fontsize=9.5)

fig.text(0.02, 0.003,
         "Outage defined as loss >= %.0f%% in a 0.5s sample. Orange dotted lines = handover trigger points (x=250/750/1250)."
         % OUTAGE_THRESH, fontsize=8.3, color=MUTED_C, ha="left")

plt.savefig("runs/2026-07-08_bw-mapping-smoke-tests/step2h_full_dashboard.png",
            dpi=150, facecolor=DARK_BG, bbox_inches="tight")
print("saved: runs/2026-07-08_bw-mapping-smoke-tests/step2h_full_dashboard.png")
print("n_ho=%d cum_outage_s=%.2f outage_pct=%.2f rebuffer_pct=%.2f" % (n_ho, cum_outage_s[-1], outage_pct, rebuffer_pct))
