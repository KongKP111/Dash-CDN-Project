"""
config.py — CDN Baseline
Edit USER and paths when moving to a new machine.
Mirrors dash-baseline/config.py structure for fair comparison.
"""
import os

DEFAULT_USER = "kongpop"
_sudo_user = os.environ.get("SUDO_USER") or ""
# When nested sudo is used (e.g. sudo bash script.sh → sudo python3),
# SUDO_USER becomes "root". Fall back to DEFAULT_USER in that case.
USER = _sudo_user if (_sudo_user and _sudo_user != "root") else DEFAULT_USER
HOME = "/home/%s" % USER

# nginx video files directory (must contain Video.mp4 and Video2.mp4)
CONTENT_DIR = os.path.join(HOME, "PSU_Project", "Dash-CDN-Project",
                           "CDN", "origin")

# nginx ports
ORIGIN_PORT = 8080   # origin  (always MISS path — WAN delay applied)
EDGE_PORT   = 8081   # edge cache (HIT or MISS depending on popularity)

ORIGIN_IP   = "10.0.0.100"
EDGE_IP     = "10.0.0.100"   # same host as origin; nginx listens on two ports (8080/8081)

# Popular video  → proxy_cache_min_uses=1   → always HIT after first request
VIDEO_HIT  = "Video.mp4"

# Unpopular video → proxy_cache_min_uses=1000 → always MISS
VIDEO_MISS = "Video2.mp4"