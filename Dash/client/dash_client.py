#!/usr/bin/env python3
"""
============================================================================
  Adaptive DASH Client  (used by BOTH SDN-DASH and SDN-CDN)
----------------------------------------------------------------------------
  A headless MPEG-DASH client that emulates a real adaptive video player
  with a REAL-TIME playout model. The SAME client is used for both
  architectures so the only variable that differs between experiments is
  WHERE the content is served from (central server vs. edge cache).

  Playback model : REAL-TIME buffer-based playout.
      - A virtual playout clock advances with wall-clock time. The buffer is
        drained at 1 s of media per 1 s of real time once playback begins.
      - The player paces itself: when the buffer reaches the target it stops
        fetching ahead and waits, exactly like a real adaptive player. This
        guarantees media_played_s <= session_duration_s.
      - When the buffer underruns (e.g. during a handover gap) playback
        STALLS; the stall duration is the real wall-clock time the player
        waits with an empty buffer, and a rebuffering event is counted.
      - Startup delay (time-to-first-frame) is measured explicitly.

  Adaptation logic : Throughput-based (rate-based) ABR.
      - Pick the highest representation whose bitrate <= measured throughput
        x a safety factor, constrained by the current buffer level.

  Segment timing   : Parsed from the MPD <SegmentTimeline> (variable segment
      durations) using the AdaptationSet timescale, so the buffer / stall
      model is accurate to the real content.

  Reference model  : MPEG-DASH (ISO/IEC 23009-1); rate-based adaptation and
      QoE metrics (startup delay, stall count/duration, rebuffering ratio,
      quality switches) as surveyed in Seufert et al., "A Survey on QoE of
      HTTP Adaptive Streaming," IEEE Comm. Surveys & Tutorials, 2015.

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
BUFFER_TARGET  = 24.0     # seconds: target buffer; player stops fetching above this
BUFFER_MIN     = 4.0      # seconds: below this the ABR throttles quality down
STARTUP_BUFFER = 4.0      # seconds: buffer required before playback starts
SAFETY_FACTOR  = 0.90     # use 90% of measured throughput for safety
HTTP_TIMEOUT   = 15.0     # per-request timeout (seconds)
PACE_SLEEP     = 0.1      # seconds: granularity of the pacing / playout loop


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

        # --- real-time playout state ---
        self.buffer        = 0.0       # seconds of media downloaded, not yet played
        self.played_media  = 0.0       # seconds of media actually played out (real time)
        self.downloaded_media = 0.0    # seconds of media fetched into the buffer
        self.stall_count   = 0
        self.stall_time    = 0.0
        self.playing       = False     # has playback started?
        self.startup_delay = None      # seconds: time-to-first-frame
        self._in_stall     = False
        self._last_tick    = None      # wall-clock of last playout update

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

    # ---- Real-time playout clock ----------------------------------------
    def _advance_playback(self):
        """Advance the virtual playout clock by the real wall-clock time that
        has elapsed since the last call. Once playback has started, the buffer
        is drained 1 s of media per 1 s of real time. If the buffer underruns,
        the remaining real time is counted as stall (rebuffering) and a stall
        event is registered on entry into the stalled state.

        Returns the stall time incurred during this advance (seconds)."""
        now = time.time()
        if self._last_tick is None:
            self._last_tick = now
            return 0.0
        dt = now - self._last_tick
        self._last_tick = now
        if dt <= 0 or not self.playing:
            return 0.0

        stall = 0.0
        if self.buffer >= dt:
            # enough buffered media to cover the elapsed time -> smooth play
            self.buffer -= dt
            self.played_media += dt
            self._in_stall = False
        else:
            # play out what is left, then stall for the remainder
            self.played_media += self.buffer
            stall = dt - self.buffer
            self.buffer = 0.0
            self.stall_time += stall
            if not self._in_stall:        # count one event per stall episode
                self.stall_count += 1
                self._in_stall = True
        return stall

    # ---- Main streaming loop --------------------------------------------
    def run(self):
        self.fetch_mpd()
        self.session_start = time.time()
        self._last_tick    = self.session_start
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
            # 1) advance the real-time playout clock first
            self._advance_playback()

            # 2) stop when the vehicle leaves the coverage area (wall-clock)
            if self.max_duration is not None:
                if (time.time() - self.session_start) >= self.max_duration:
                    sys.stderr.write(
                        f'[CLIENT] Reached max_duration '
                        f'({self.max_duration:.1f}s), stopping.\n')
                    break
            if self.max_segments and seg_number > self.max_segments:
                break
            if n_total and seg_number > n_total:
                break

            # 3) pacing: if the buffer is full, do NOT fetch ahead. Sleep a
            #    little and let the playout clock drain it. This is what makes
            #    the session run in real time (a real player behaves this way).
            if self.playing and self.buffer >= BUFFER_TARGET:
                time.sleep(PACE_SLEEP)
                continue

            # 4) choose quality and fetch the next segment
            rep = self.choose_representation(last_tp)
            url = rep.media_url(self.base_url, seg_number)

            stall_before = self.stall_time
            seg_bytes, dt = None, None
            for dl_attempt in range(1, 9):   # up to 8 quick retries
                try:
                    seg_bytes, dt = self.download(url)
                    break
                except Exception:
                    # during a handover the route is briefly unavailable; the
                    # buffer keeps draining in real time while we wait, which
                    # is exactly what causes a real stall.
                    if self.max_duration is not None and \
                       (time.time() - self.session_start) >= self.max_duration:
                        break
                    time.sleep(0.5)
                    self._advance_playback()
            # account for the real time spent on the (successful) download
            self._advance_playback()

            if seg_bytes is None:
                # could not fetch (long outage or end-of-run)
                if self.max_duration is not None and \
                   (time.time() - self.session_start) >= self.max_duration:
                    break
                seg_number += 1
                continue

            throughput_kbps = (seg_bytes * 8 / 1000.0) / dt
            last_tp = throughput_kbps
            self.total_bytes += seg_bytes

            seg_dur = self.seg_duration(seg_number)
            self.buffer += seg_dur                 # new media enters the buffer
            self.downloaded_media += seg_dur

            # start playback once the startup buffer is reached
            if not self.playing and self.buffer >= STARTUP_BUFFER:
                self.playing = True
                self.startup_delay = round(time.time() - self.session_start, 3)
                self._last_tick = time.time()      # start draining from now

            stall_this = self.stall_time - stall_before

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

        # real-time playout: media actually played out (<= session duration)
        played_media = round(self.played_media, 2)
        rebuf_denom = self.played_media + self.stall_time

        summary = {
            'run_id': self.run_id,
            'mpd_url': self.mpd_url,
            'segments_played': n,
            'media_played_s': played_media,
            'media_downloaded_s': round(self.downloaded_media, 2),
            'session_duration_s': round(duration, 2),
            'startup_delay_s': self.startup_delay if self.startup_delay is not None else round(duration, 3),
            'avg_bitrate_kbps': round(avg_bitrate, 1),
            'avg_throughput_kbps': round(avg_tp, 1),
            'total_stall_events': self.stall_count,
            'total_stall_duration_s': round(self.stall_time, 3),
            'rebuffering_ratio': round(self.stall_time / rebuf_denom, 4) if rebuf_denom else 0,
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
