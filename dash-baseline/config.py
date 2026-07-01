"""
config.py  --  edit these when moving to a new machine, then everything else
just works. Keeping them here avoids hard-coding paths across files and avoids
the `sudo -> /root` home-directory pitfall.
"""
import os

# Your login name. Auto-detected under sudo via SUDO_USER; if that is empty
# OR "root" (nested sudo -- e.g. `sudo ./run_4rsu_multi.sh` wrapping an inner
# `sudo python3 ...`, where the inner call's invoking user IS root) it falls
# back to DEFAULT_USER instead, so paths never resolve to /root by accident.
DEFAULT_USER = "diz"                     # <-- set to your username
_sudo_user = os.environ.get("SUDO_USER")
USER = _sudo_user if (_sudo_user and _sudo_user != "root") else DEFAULT_USER

HOME = "/home/%s" % USER                 # <-- edit if your home is elsewhere

# Where the encoded 3-rung DASH content lives (must contain index.mpd)
CONTENT_DIR = os.path.join(HOME, "sdn-vanet-project", "bbb_3rung")

# In-experiment server addressing (rarely needs changing)
SRV_IP   = "10.0.0.100"
SRV_PORT = 8000
