#!/usr/bin/env python3
"""
============================================================================
  Adaptive DASH Client  (used by BOTH SDN-DASH and SDN-CDN)
----------------------------------------------------------------------------
  A headless MPEG-DASH client that emulates a real adaptive video player.
  The SAME client is used for both architectures so the only variable that
  differs between experiments is WHERE the content is served from
  (central server vs. edge cache). This keeps the comparison fair.

  Adaptation logic : Throughput-based (rate-based) ABR.
      - Standard "conventional" DASH adaptation.
      - Pick the highest representation whose bitrate <= measured throughput
        x a safety factor, constrained by the current buffer level.
      - On buffer underrun -> a stall (rebuffering) event is recorded.

  Segment timing   : Parsed from the MPD <SegmentTimeline> (variable segment
      durations) using the AdaptationSet timescale. This makes the buffer /
      stall model accurate to the real content rather than assuming a fixed
      segment length.

  Reference model  : MPEG-DASH (ISO/IEC 23009-1); rate-based adaptation as
      surveyed in Seufert et al., "A Survey on QoE of HTTP Adaptive
      Streaming," IEEE Comm. Surveys & Tutorials, 2015.

  Metrics logged (CSV) -> one row per downloaded segment:
      seg_index, timestamp, representation, bitrate_kbps, seg_dur_s,
      seg_bytes, download_time_s, throughput_kbps, buffer_s,
      stall_events, stall_duration_s, action
  Summary logged (JSON) -> per-session aggregate for the run.
----------------------------------------------------------------------------
  Project : SDN-CDN vs SDN-DASH  |  Author: Hadis Rodpradit
============================================================================
"""

import os
import re
import sys
import csv
import json
import time
import argparse
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET


# ---------------------------------------------------------------------------
#  Player constants (standard buffer-based playback model)
# ---------------------------------------------------------------------------
BUFFER_TARGET = 24.0      # seconds: target buffer we try to keep filled
BUFFER_MIN    = 4.0       # seconds: below this we throttle quality down
SAFETY_FACTOR = 0.90      # use 90% of measured throughput for safety
HTTP_TIMEOUT  = 15.0      # per-request timeout (seconds)


class Representation:
    """One quality level from the MPD."""
    def __init__(self, rep_id, bandwidth, init_tmpl, media_tmpl):
        self.id           = rep_id
        self.bandwidth    = bandwidth          # bits per second
        self.bitrate_kbps = bandwidth / 1000.0
        self.init_tmpl    = init_tmpl
        self.media_tmpl   = media_tmpl

    def _fill(self, tmpl, number=None):
        path = tmpl.replace('$RepresentationID$', self.id)
        if number is not None:
            path = re.sub(r'\$Number%(\d+)d\$',
                          lambda m: str(number).zfill(int(m.group(1))), path)
            path = path.replace('$Number$', str(number))
        return path

    def media_url(self, base, number):
        return urllib.parse.urljoin(base, self._fill(self.media_tmpl, number))

    def init_url(self, base):
        return urllib.parse.urljoin(base, self._fill(self.init_tmpl))


class DASHClient:
    def __init__(self, mpd_url, run_id, out_dir, max_segments=None, max_duration=None):
        self.mpd_url      = mpd_url
        self.base_url     = mpd_url.rsplit('/', 1)[0] + '/'
        self.run_id       = run_id
        self.out_dir      = out_dir
        self.max_segments = max_segments
        self.max_duration = max_duration

        self.reps          = []        # sorted ascending by bandwidth
        self.seg_durations = []        # seconds per segment, from timeline
        self.timescale     = 1
        self.buffer        = 0.0
        self.stall_count   = 0
        self.stall_time    = 0.0
        self.rows          = []
        self.total_bytes   = 0
        self.session_start = None

        os.makedirs(out_dir, exist_ok=True)
        self.csv_path  = os.path.join(out_dir, f'{run_id}_segments.csv')
        self.json_path = os.path.join(out_dir, f'{run_id}_summary.json')

    # ---- MPD parsing -----------------------------------------------------
    def fetch_mpd(self):
        # Retry the initial MPD fetch: right after a (re)association the
        # OpenFlow rules / ARP entries may not be installed yet, so the very
        # first request can fail with "No route to host". A real adaptive
        # player also retries instead of giving up, so this is realistic.
        data = None
        last_err = None
        for attempt in range(1, 21):   # up to ~20 tries
            try:
                with urllib.request.urlopen(
                        urllib.request.Request(self.mpd_url),
                        timeout=HTTP_TIMEOUT) as r:
                    data = r.read().decode('utf-8', errors='ignore')
                break
            except Exception as e:
                last_err = e
                sys.stderr.write(
                    f'[CLIENT] MPD fetch attempt {attempt} failed '
                    f'({e}); retrying...\n')
                time.sleep(1.0)
        if data is None:
            sys.exit(f'[ERROR] Could not fetch MPD after retries: {last_err}')
        root = ET.fromstring(data)
        ns = root.tag.split('}')[0] + '}' if root.tag.startswith('{') else ''

        seg_tmpl_global = root.find(f'.//{ns}SegmentTemplate')
        g_init = seg_tmpl_global.get('initialization', '') if seg_tmpl_global is not None else ''
        g_media = seg_tmpl_global.get('media', '') if seg_tmpl_global is not None else ''

        for rep in root.iter(f'{ns}Representation'):
            rep_id = rep.get('id')
            bw     = int(rep.get('bandwidth', '0'))
            st = rep.find(f'{ns}SegmentTemplate')
            i_t = st.get('initialization', g_init) if st is not None else g_init
            m_t = st.get('media', g_media) if st is not None else g_media
            self.reps.append(Representation(rep_id, bw, i_t, m_t))

            # parse timeline once (durations identical across representations)
            if not self.seg_durations and st is not None:
                self.timescale = int(st.get('timescale', '1'))
                tl = st.find(f'{ns}SegmentTimeline')
                if tl is not None:
                    for s in tl.findall(f'{ns}S'):
                        d = int(s.get('d'))
                        repeat = int(s.get('r', '0'))   # r = extra repeats
                        for _ in range(repeat + 1):
                            self.seg_durations.append(d / self.timescale)

        self.reps.sort(key=lambda x: x.bandwidth)
        if not self.reps:
            sys.exit('[ERROR] No representations found in MPD')

        total_segs = len(self.seg_durations)
        sys.stderr.write(
            f'[CLIENT] {len(self.reps)} quality levels: '
            + ', '.join(f'{r.bitrate_kbps:.0f}k' for r in self.reps) + '\n')
        sys.stderr.write(
            f'[CLIENT] {total_segs} segments from timeline '
            f'(timescale={self.timescale})\n')

    def seg_duration(self, idx):
        """idx is 1-based segment number."""
        if 1 <= idx <= len(self.seg_durations):
            return self.seg_durations[idx - 1]
        return 4.0   # fallback

    # ---- HTTP download with timing --------------------------------------
    def download(self, url):
        t0 = time.time()
        with urllib.request.urlopen(urllib.request.Request(url),
                                    timeout=HTTP_TIMEOUT) as r:
            payload = r.read()
        return len(payload), max(time.time() - t0, 1e-6)

    # ---- ABR: throughput-based representation selection -----------------
    def choose_representation(self, last_throughput_kbps):
        if self.buffer < BUFFER_MIN:
            return self.reps[0]                 # buffer guard -> lowest
        budget = last_throughput_kbps * SAFETY_FACTOR
        chosen = self.reps[0]
        for rep in self.reps:
            if rep.bitrate_kbps <= budget:
                chosen = rep
            else:
                break
        return chosen

    # ---- Main streaming loop --------------------------------------------
    def run(self):
        self.fetch_mpd()
        self.session_start = time.time()
        last_tp = self.reps[0].bitrate_kbps
        seg_number = 1

        # init segment of lowest rep (not counted as media playback)
        try:
            init_b, _ = self.download(self.reps[0].init_url(self.base_url))
            self.total_bytes += init_b
        except Exception as e:
            sys.stderr.write(f'[WARN] init segment failed: {e}\n')

        n_total = len(self.seg_durations)
        while True:
            # Stop if we've run past the allotted wall-clock duration
            # (emulates the vehicle leaving the coverage area)
            if self.max_duration is not None:
                elapsed = time.time() - self.session_start
                if elapsed >= self.max_duration:
                    sys.stderr.write(
                        f'[CLIENT] Reached max_duration '
                        f'({self.max_duration:.1f}s), stopping.\n')
                    break
            if self.max_segments and seg_number > self.max_segments:
                break
            if n_total and seg_number > n_total:
                break

            rep = self.choose_representation(last_tp)
            url = rep.media_url(self.base_url, seg_number)
            # Retry the segment a few times: during a handover the route is
            # briefly unavailable. We treat that gap as buffering time rather
            # than ending the session, which mirrors real player behaviour.
            seg_bytes, dt = None, None
            handover_wait = 0.0
            for dl_attempt in range(1, 9):   # up to 8 quick retries
                try:
                    seg_bytes, dt = self.download(url)
                    break
                except Exception:
                    # Stop retrying if we've already exceeded the duration cap
                    if self.max_duration is not None:
                        if (time.time() - self.session_start) >= self.max_duration:
                            break
                    handover_wait += 0.5
                    time.sleep(0.5)
            if seg_bytes is None:
                # Could not fetch this segment (likely past end-of-run or a
                # long outage). If we have produced rows already, stop cleanly
                # so the summary is still written; otherwise break out.
                if self.max_duration is not None and \
                   (time.time() - self.session_start) >= self.max_duration:
                    break
                # account the lost time as a stall, then move on
                self.buffer -= handover_wait
                if self.buffer < 0:
                    self.stall_count += 1
                    self.stall_time += -self.buffer
                    self.buffer = 0.0
                seg_number += 1
                continue

            throughput_kbps = (seg_bytes * 8 / 1000.0) / dt
            last_tp = throughput_kbps
            self.total_bytes += seg_bytes

            seg_dur = self.seg_duration(seg_number)

            # buffer + stall model (variable segment duration)
            stall_this = 0.0
            self.buffer -= dt
            if self.buffer < 0:
                stall_this = -self.buffer
                self.stall_count += 1
                self.stall_time += stall_this
                self.buffer = 0.0
            self.buffer = min(self.buffer + seg_dur, BUFFER_TARGET)

            ts = round(time.time() - self.session_start, 3)
            switched = bool(self.rows) and self.rows[-1]['representation'] != rep.id
            self.rows.append({
                'seg_index': seg_number,
                'timestamp': ts,
                'representation': rep.id,
                'bitrate_kbps': round(rep.bitrate_kbps, 1),
                'seg_dur_s': round(seg_dur, 3),
                'seg_bytes': seg_bytes,
                'download_time_s': round(dt, 4),
                'throughput_kbps': round(throughput_kbps, 1),
                'buffer_s': round(self.buffer, 2),
                'stall_events': self.stall_count,
                'stall_duration_s': round(stall_this, 3),
                'action': 'switch' if switched else 'steady',
            })
            seg_number += 1

        self.write_logs()

    # ---- Output ----------------------------------------------------------
    def write_logs(self):
        if self.rows:
            with open(self.csv_path, 'w', newline='') as f:
                w = csv.DictWriter(f, fieldnames=list(self.rows[0].keys()))
                w.writeheader()
                w.writerows(self.rows)

        duration = time.time() - self.session_start
        n = len(self.rows)
        avg_bitrate = sum(r['bitrate_kbps'] for r in self.rows) / n if n else 0
        avg_tp = sum(r['throughput_kbps'] for r in self.rows) / n if n else 0
        switches = sum(1 for r in self.rows if r['action'] == 'switch')
        played_media = sum(r['seg_dur_s'] for r in self.rows)

        summary = {
            'run_id': self.run_id,
            'mpd_url': self.mpd_url,
            'segments_played': n,
            'media_played_s': round(played_media, 2),
            'session_duration_s': round(duration, 2),
            'avg_bitrate_kbps': round(avg_bitrate, 1),
            'avg_throughput_kbps': round(avg_tp, 1),
            'total_stall_events': self.stall_count,
            'total_stall_duration_s': round(self.stall_time, 3),
            'rebuffering_ratio': round(self.stall_time / (played_media + self.stall_time), 4) if played_media else 0,
            'quality_switches': switches,
            'total_bytes': self.total_bytes,
        }
        with open(self.json_path, 'w') as f:
            json.dump(summary, f, indent=2)

        sys.stderr.write('[CLIENT] ===== SESSION SUMMARY =====\n')
        for k, v in summary.items():
            sys.stderr.write(f'[CLIENT]   {k}: {v}\n')
        sys.stderr.write(f'[CLIENT] CSV  -> {self.csv_path}\n')
        sys.stderr.write(f'[CLIENT] JSON -> {self.json_path}\n')


def main():
    ap = argparse.ArgumentParser(description='Adaptive DASH client (DASH/CDN)')
    ap.add_argument('--url', required=True, help='full URL to index.mpd')
    ap.add_argument('--run-id', required=True, help='unique run id for logs')
    ap.add_argument('--out', default='/tmp/dash_logs', help='output directory')
    ap.add_argument('--max-segments', type=int, default=None)
    ap.add_argument('--duration', type=float, default=None,
                    help='max wall-clock seconds to stream (stops and writes logs)')
    args = ap.parse_args()
    DASHClient(args.url, args.run_id, args.out, args.max_segments,
               max_duration=args.duration).run()


if __name__ == '__main__':
    main()
