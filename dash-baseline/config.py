"""
config.py  --  edit these when moving to a new machine, then everything else
just works. Keeping them here avoids hard-coding paths across files and avoids
the `sudo -> /root` home-directory pitfall.
"""
import os

# Your login name. Auto-detected under sudo via SUDO_USER; if that is empty
# (e.g. nested sudo in the batch script) it falls back to DEFAULT_USER instead
# of root, so paths never resolve to /root by accident.
DEFAULT_USER = "diz"                     # <-- set to your username
USER = os.environ.get("SUDO_USER") or DEFAULT_USER

HOME = "/home/%s" % USER                 # <-- edit if your home is elsewhere

# Where the encoded 3-rung DASH content lives (must contain index.mpd)
CONTENT_DIR = os.path.join(HOME, "sdn-vanet-project", "bbb_3rung")

# In-experiment server addressing (rarely needs changing)
SRV_IP   = "10.0.0.100"
SRV_PORT = 8000
