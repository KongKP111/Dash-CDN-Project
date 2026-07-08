import csv
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

RSU_X = [0, 500, 1000, 1500]
HO_X = [250, 750, 1250]
C_LINEAR = "#2a78d6"
C_STEP2H = "#1baf7a"
C_NEG = "#e34948"

def load_agg(path):
    rows = list(csv.DictReader(open(path)))
    x = np.array([float(r["x"]) for r in rows])
    qm = np.array([float(r["q_mean"]) for r in rows])
    qs = np.array([float(r["q_std"]) for r in rows])
    bw = np.array([float(r["bw_mean"]) for r in rows])
    return x, qm, qs, bw

def load_run(path):
    rows = list(csv.DictReader(open(path)))
    x = np.array([float(r["x"]) for r in rows])
    q = np.array([int(r["quality_idx"]) for r in rows])
    bw = np.array([float(r["bw_mbps"]) for r in rows])
    return x, q, bw

lx, lqm, lqs, lbw = load_agg("runs/2026-07-02_linear-baseline/aggregate_4rsu.csv")
sx, sq, sbw = load_run("test04_step2h.csv")

# QoE decomposition (Yin et al. 2015), per segment -- precomputed
qoe = {
    "linear": dict(util=1.2808, switch=0.1202, rebuf=0.0, net=1.1606),
    "step2h": dict(util=3.2000, switch=0.2667, rebuf=0.0, net=2.9333),
}

fig = plt.figure(figsize=(12, 12), facecolor="#141414")
gs = fig.add_gridspec(3, 1, height_ratios=[1, 1, 1.1], hspace=0.42, top=0.92, bottom=0.06)

DARK_BG = "#141414"
CARD_BG = "#1c1c1c"
GRID_C = "#2c2c2a"
TEXT_C = "#e8e6e0"
MUTED_C = "#8a897f"

for ax in []:
    pass

fig.suptitle("Linear baseline (10-run) vs Step2h (run_04, 2026-07-08) — 4-RSU, 20 km/hr",
             fontsize=15, fontweight="bold", color=TEXT_C, x=0.02, ha="left", y=0.98)
fig.text(0.02, 0.955, "Quality & imposed bandwidth vs vehicle position  ·  QoE per segment (Yin et al. 2015 linear model)",
          fontsize=10.5, color=MUTED_C, ha="left")

# --- Panel 1: Quality vs x ---
ax1 = fig.add_subplot(gs[0])
ax1.set_facecolor(CARD_BG)
ax1.fill_between(lx, np.clip(lqm - lqs, 0, 2), np.clip(lqm + lqs, 0, 2), color=C_LINEAR, alpha=0.18, linewidth=0)
ax1.plot(lx, lqm, color=C_LINEAR, linewidth=2, label="Linear, n=10 mean ± std")
ax1.step(sx, sq, where="post", color=C_STEP2H, linewidth=2, label="Step2h, run_04 (n=1)")
for rx in RSU_X:
    ax1.axvline(rx, color=MUTED_C, linestyle="--", linewidth=0.8, alpha=0.6)
for hx in HO_X:
    ax1.axvline(hx, color="#eb6834", linestyle=":", linewidth=0.8, alpha=0.7)
for i, rx in enumerate(RSU_X):
    ax1.text(rx, 2.15, "RSU%d" % (i+1), color=MUTED_C, fontsize=8, ha="center")
ax1.set_yticks([0, 1, 2]); ax1.set_yticklabels(["360p", "720p", "1080p"], color=TEXT_C)
ax1.set_ylim(-0.3, 2.35)
ax1.set_xlim(-300, 1800)
ax1.set_title("Selected quality vs position", color=TEXT_C, fontsize=11.5, loc="left", pad=8)
ax1.tick_params(colors=MUTED_C)
ax1.grid(color=GRID_C, linewidth=0.6)
for spine in ax1.spines.values(): spine.set_color(GRID_C)
ax1.legend(loc="lower center", ncol=2, fontsize=9, facecolor=CARD_BG, edgecolor=GRID_C, labelcolor=TEXT_C)

# --- Panel 2: Bandwidth vs x ---
ax2 = fig.add_subplot(gs[1])
ax2.set_facecolor(CARD_BG)
ax2.plot(lx, lbw, color=C_LINEAR, linewidth=2, label="Linear, n=10 mean")
ax2.step(sx, sbw, where="post", color=C_STEP2H, linewidth=2, label="Step2h, run_04")
for rx in RSU_X:
    ax2.axvline(rx, color=MUTED_C, linestyle="--", linewidth=0.8, alpha=0.6)
for hx in HO_X:
    ax2.axvline(hx, color="#eb6834", linestyle=":", linewidth=0.8, alpha=0.7)
ax2.set_ylim(0, 10.8)
ax2.set_xlim(-300, 1800)
ax2.set_ylabel("Mbps", color=MUTED_C, fontsize=9.5)
ax2.set_xlabel("Position x (m)", color=MUTED_C, fontsize=9.5)
ax2.set_title("Imposed bandwidth vs position", color=TEXT_C, fontsize=11.5, loc="left", pad=8)
ax2.tick_params(colors=MUTED_C)
ax2.grid(color=GRID_C, linewidth=0.6)
for spine in ax2.spines.values(): spine.set_color(GRID_C)
ax2.legend(loc="upper right", ncol=1, fontsize=9, facecolor=CARD_BG, edgecolor=GRID_C, labelcolor=TEXT_C)

# --- Panel 3: QoE decomposition bar chart ---
ax3 = fig.add_subplot(gs[2])
ax3.set_facecolor(CARD_BG)
cats = ["Utility\nΣq(Rk)", "− Switch\npenalty", "− Rebuffer\npenalty", "= Net QoE\n/segment"]
lin_vals = [qoe["linear"]["util"], -qoe["linear"]["switch"], -qoe["linear"]["rebuf"], qoe["linear"]["net"]]
s2h_vals = [qoe["step2h"]["util"], -qoe["step2h"]["switch"], -qoe["step2h"]["rebuf"], qoe["step2h"]["net"]]
xpos = np.arange(len(cats))
w = 0.32
b1 = ax3.bar(xpos - w/2, lin_vals, w, color=C_LINEAR, label="Linear (n=10)")
b2 = ax3.bar(xpos + w/2, s2h_vals, w, color=C_STEP2H, label="Step2h (run_04)")
ax3.axhline(0, color=MUTED_C, linewidth=1)
for bars, vals in [(b1, lin_vals), (b2, s2h_vals)]:
    for bar, v in zip(bars, vals):
        va = "bottom" if v >= 0 else "top"
        off = 0.06 if v >= 0 else -0.06
        ax3.text(bar.get_x() + bar.get_width()/2, v + off, "%.2f" % abs(v), ha="center", va=va,
                  fontsize=9.5, fontweight="bold", color=bar.get_facecolor())
ax3.set_xticks(xpos); ax3.set_xticklabels(cats, color=TEXT_C, fontsize=9.5)
ax3.set_ylabel("Mbps / segment", color=MUTED_C, fontsize=9.5)
ax3.set_title("QoE decomposition (Yin et al. 2015)", color=TEXT_C, fontsize=11.5, loc="left", pad=8)
ax3.tick_params(colors=MUTED_C)
ax3.grid(color=GRID_C, linewidth=0.6, axis="y")
for spine in ax3.spines.values(): spine.set_color(GRID_C)
ax3.legend(loc="upper left", fontsize=9, facecolor=CARD_BG, edgecolor=GRID_C, labelcolor=TEXT_C)

# --- stats footer ---
stats_txt = (
    "1080p time: 0.1%→37.4%   |   Quality switches: 8.2→12   |   "
    "QoE/segment: 1.16→2.93 (+153%)   |   K (segments): 104→105\n"
    "Caveat: Linear = n=10 mean±std (2026-07-02).  Step2h = single run (run_04, 2026-07-08) — 10-run confirmation batch planned next."
)
fig.text(0.02, 0.005, stats_txt, fontsize=8.7, color=MUTED_C, ha="left", va="bottom")

fig.patch.set_facecolor(DARK_BG)
plt.savefig("runs/2026-07-08_summary_run04_vs_linear10run.png", dpi=160, facecolor=DARK_BG, bbox_inches="tight")
print("saved: runs/2026-07-08_summary_run04_vs_linear10run.png")
