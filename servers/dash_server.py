#!/usr/bin/env python3
"""
============================================================================
  DASH Video Server  (SDN-DASH architecture)
----------------------------------------------------------------------------
  Serves MPEG-DASH content (index.mpd + .m4s segments) over HTTP and logs
  every request with a timestamp. The request log is how we measure bitrate
  adaptation: which quality stream the client pulled, and when.

      chunk-stream0-*  ->  quality 0  (lowest  bitrate)
      chunk-stream1-*  ->  quality 1  (medium  bitrate)
      chunk-stream2-*  ->  quality 2  (highest bitrate)

  Usage:
      python3 dash_server.py --dir /path/to/bbb_multi --port 8080 \
                             --log /tmp/dash_server.log
----------------------------------------------------------------------------
  Project : SDN-CDN vs SDN-DASH  |  Author: Hadis Rodpradit
============================================================================
"""

import os
import sys
import argparse
import datetime
import http.server
import socketserver


class DASHRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Static file handler with DASH-correct MIME types + request logging."""

    log_path = None       # set from main()

    # ---- correct MIME types so VLC / dash.js accept the content ---------
    def guess_type(self, path):
        if path.endswith('.mpd'):
            return 'application/dash+xml'
        if path.endswith('.m4s'):
            return 'video/iso.segment'
        if path.endswith('.mp4'):
            return 'video/mp4'
        return super().guess_type(path)

    # ---- log every request with a precise timestamp --------------------
    def log_message(self, fmt, *args):
        ts = datetime.datetime.now().isoformat(timespec='milliseconds')
        line = f"{ts}\t{self.client_address[0]}\t{fmt % args}\n"
        if DASHRequestHandler.log_path:
            try:
                with open(DASHRequestHandler.log_path, 'a') as f:
                    f.write(line)
            except OSError:
                pass
        sys.stderr.write(line)


def main():
    ap = argparse.ArgumentParser(description='DASH video server')
    ap.add_argument('--dir', required=True,
                    help='directory containing index.mpd and .m4s segments')
    ap.add_argument('--port', type=int, default=8080)
    ap.add_argument('--log', default='/tmp/dash_server.log')
    args = ap.parse_args()

    content_dir = os.path.abspath(args.dir)
    if not os.path.isfile(os.path.join(content_dir, 'index.mpd')):
        sys.exit(f"[ERROR] index.mpd not found in {content_dir}")

    os.chdir(content_dir)
    DASHRequestHandler.log_path = args.log

    # fresh log per run
    open(args.log, 'w').close()

    # allow quick restart on the same port
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(('0.0.0.0', args.port),
                                         DASHRequestHandler) as httpd:
        banner = (f"[DASH SERVER] serving {content_dir}\n"
                  f"[DASH SERVER] port={args.port}  log={args.log}\n")
        sys.stderr.write(banner)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            sys.stderr.write("\n[DASH SERVER] stopped\n")


if __name__ == '__main__':
    main()
