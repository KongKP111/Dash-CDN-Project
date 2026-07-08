#!/usr/bin/env python3
"""
vlc_player.py — real VLC playback + buffering/stall telemetry for the CDN
SDN baseline (cdn_baseline_topo_sdn.py).

Runs as a standalone process inside car1's network namespace (launched via
car1.cmd('python3 vlc_player.py ... &')). Does NOT import config.py /
baseline_model.py — the caller always passes fully-formed URLs, so this
script is testable stand-alone against any plain HTTP server, no mininet
required.

Handover-safe by design: on a "switch" command it captures its own
playback position (get_time()), reloads the new URL (same content, a
different edge), and seeks back — the caller never computes or passes
position, it only tells this process which URL to move to.

Control protocol: file-based, matching this codebase's existing done-file
IPC convention (see cooperative_warm()/_wait_for_coop_warm() in
cdn_baseline_topo_sdn.py). The caller atomically writes a line
"<ap_idx_1based>|<url>" to --ctrl-file; this process polls the file's
mtime and reacts to changes. No sockets/FIFOs needed because mininet hosts
share the real filesystem — only the network namespace differs.
"""

import argparse
import csv
import os
import queue
import signal
import sys
import time
from types import SimpleNamespace

try:
    import vlc
except ImportError:
    sys.stderr.write("FATAL: python-vlc not installed. Run: pip3 install python-vlc\n")
    sys.exit(1)


def parse_args():
    p = argparse.ArgumentParser(description="VLC playback + buffer/stall telemetry")
    p.add_argument('--run-id', required=True)
    p.add_argument('--initial-ap', type=int, required=True)
    p.add_argument('--initial-url', required=True)
    p.add_argument('--ctrl-file', required=True)
    p.add_argument('--telemetry-csv', required=True)
    p.add_argument('--events-csv', required=True)
    p.add_argument('--poll-interval', type=float, default=0.2)
    p.add_argument('--sample-dt', type=float, default=1.0)
    p.add_argument('--network-caching-ms', type=int, default=5000,
                    help='libvlc client buffer target (ms) — a modeling '
                         'choice, not a "real" DASH client value; tunable.')
    p.add_argument('--switch-ready-timeout', type=float, default=30.0,
                    help='Safety-net only (checking is non-blocking): give '
                         'up recovering the resume position if the player '
                         'is not ready this long after a switch. At the '
                         "model's BW_MIN (0.5 Mbps) vs the video's ~4.48 "
                         'Mbps bitrate, filling network-caching can '
                         'legitimately take tens of seconds.')
    p.add_argument('--seek-tolerance-ms', type=int, default=1500)
    p.add_argument('--startup-grace-s', type=float, default=2.0,
                    help='Window after (re)starting playback during which '
                         'buffering dips are treated as startup, not a '
                         'real stall — libvlc can fire Playing slightly '
                         'before a trailing low-cache Buffering event.')
    p.add_argument('--show', action='store_true',
                    help='Open a real video window (needs a reachable X '
                         'display) instead of the default headless '
                         'dummy vout/aout — for manual/demo runs only, '
                         'never for batch runs.')
    return p.parse_args()


def main():
    args = parse_args()

    stop_flag = {'stop': False}

    def _on_signal(signum, frame):
        stop_flag['stop'] = True

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    tel_f = open(args.telemetry_csv, 'w', newline='')
    evt_f = open(args.events_csv, 'w', newline='')
    tel_w = csv.writer(tel_f)
    evt_w = csv.writer(evt_f)
    tel_w.writerow(['t_epoch', 't_elapsed_s', 'ap', 'url', 'position_s',
                     'duration_s', 'state', 'is_buffering', 'buffer_pct',
                     'cum_stall_events', 'cum_stall_time_s'])
    evt_w.writerow(['t_epoch', 'event_type', 'ap', 'url', 'position_s', 'detail'])
    tel_f.flush()
    evt_f.flush()

    if args.show:
        # Real window, but force plain X11 (xcb_x11) instead of letting
        # libvlc auto-pick GL/GLX: this process runs as root (via sudo)
        # against the invoking user's X display, and GL-accelerated output
        # needs XDG_RUNTIME_DIR/DRI access that root doesn't have in that
        # session — it fails with "X Error ... GLXBadWindow" (confirmed
        # empirically). Plain XCB blit needs none of that.
        os.environ.setdefault('XDG_RUNTIME_DIR', '/tmp/vlc-xdg-root')
        os.makedirs(os.environ['XDG_RUNTIME_DIR'], exist_ok=True, mode=0o700)
        vlc_args = ['--vout=xcb_x11', '--quiet',
                     '--network-caching=%d' % args.network_caching_ms]
    else:
        # Headless (default): decode-but-discard, no display needed —
        # this is what every batch/automated run uses.
        vlc_args = ['--vout=dummy', '--aout=dummy', '--quiet', '--no-xlib',
                     '--network-caching=%d' % args.network_caching_ms]
    instance = vlc.Instance(vlc_args)
    player = instance.media_player_new()

    q = queue.Queue()

    def _mk_cb(kind):
        def _cb(event):
            if kind == 'BUFFERING':
                q.put((kind, time.time(), event.u.new_cache))
            else:
                q.put((kind, time.time(), None))
        return _cb

    em = player.event_manager()
    em.event_attach(vlc.EventType.MediaPlayerBuffering, _mk_cb('BUFFERING'))
    em.event_attach(vlc.EventType.MediaPlayerPlaying, _mk_cb('PLAYING'))
    em.event_attach(vlc.EventType.MediaPlayerPaused, _mk_cb('PAUSED'))
    em.event_attach(vlc.EventType.MediaPlayerStopped, _mk_cb('STOPPED'))
    em.event_attach(vlc.EventType.MediaPlayerEndReached, _mk_cb('ENDED'))
    em.event_attach(vlc.EventType.MediaPlayerEncounteredError, _mk_cb('ERROR'))

    state = SimpleNamespace(
        current_ap=args.initial_ap,
        current_url=args.initial_url,
        has_reached_playing_once=False,
        startup_buffering_started=False,
        currently_stalling=False,
        stall_start_ts=None,
        cum_stall_events=0,
        cum_stall_time_s=0.0,
        last_buffer_pct=0.0,
        in_handover_reload=False,
        startup_grace_until=None,
        pending_seek_ms=None,
        pending_seek_deadline=None,
    )

    def write_event(ts, event_type, pos_s=None, detail=''):
        if pos_s is None:
            try:
                raw = player.get_time()
                pos_s = raw / 1000.0 if raw and raw >= 0 else -1.0
            except Exception:
                pos_s = -1.0
        evt_w.writerow(['%.3f' % ts, event_type, state.current_ap,
                         state.current_url, '%.3f' % pos_s, detail])
        evt_f.flush()

    def handle_queue_item(kind, ts, val):
        if kind == 'BUFFERING':
            pct = val
            state.last_buffer_pct = pct
            if state.in_handover_reload:
                return  # deliberate reload dip — not a real stall, don't classify
            in_grace = (state.startup_grace_until is not None
                        and ts < state.startup_grace_until)
            if pct >= 99.9:
                if state.currently_stalling:
                    dur = ts - state.stall_start_ts
                    state.cum_stall_time_s += dur
                    write_event(ts, 'STALL_END', detail='duration_s=%.2f' % dur)
                    state.currently_stalling = False
                elif not state.has_reached_playing_once:
                    write_event(ts, 'STARTUP_BUFFERING_END', detail='pct=100')
                state.has_reached_playing_once = True
            else:
                if in_grace or not state.has_reached_playing_once:
                    if not state.startup_buffering_started:
                        write_event(ts, 'STARTUP_BUFFERING_START', detail='pct=%.1f' % pct)
                        state.startup_buffering_started = True
                else:
                    if not state.currently_stalling:
                        state.currently_stalling = True
                        state.cum_stall_events += 1
                        state.stall_start_ts = ts
                        write_event(ts, 'STALL_START', detail='pct=%.1f' % pct)
        elif kind == 'PLAYING':
            write_event(ts, 'PLAYING')
            # NOTE: deliberately do NOT flip has_reached_playing_once here —
            # libvlc can fire Playing slightly before a trailing low-cache
            # Buffering event, which would otherwise be misclassified as a
            # real stall right at startup (confirmed empirically). Rely on
            # the Buffering(100)/grace-window signal instead.
        elif kind in ('PAUSED', 'STOPPED', 'ENDED', 'ERROR'):
            write_event(ts, kind)

    def do_switch(new_ap, new_url):
        """Non-blocking: kick off the reload, then hand off to
        check_pending_seek() (polled from the main loop) to finish the seek
        whenever the player actually becomes ready. A synchronous blocking
        wait here would stall the whole process — including ctrl-file
        polling for a *subsequent* handover — for as long as the network is
        slow, which is exactly the condition (low-bandwidth AP-edge zones)
        this experiment cares most about.
        """
        pos_ms = player.get_time()
        pos_ms = pos_ms if pos_ms and pos_ms >= 0 else 0
        state.in_handover_reload = True
        write_event(time.time(), 'HANDOVER_RELOAD_START', pos_s=pos_ms / 1000.0,
                    detail='from_ap=%s to_ap=%s' % (state.current_ap, new_ap))

        media = instance.media_new(new_url)
        player.set_media(media)
        player.play()

        state.current_ap, state.current_url = new_ap, new_url
        state.pending_seek_ms = pos_ms
        state.pending_seek_deadline = time.monotonic() + args.switch_ready_timeout

    def check_pending_seek():
        """Called every main-loop tick while a switch is in flight. Seeks
        back to the pre-switch position as soon as the player is ready —
        no matter how long that takes — and only gives up (logging
        SEEK_FAIL, permanently losing the resume position) if the player
        reaches a hard Error state before then.
        """
        if state.pending_seek_ms is None:
            return
        pos_ms = state.pending_seek_ms
        st = player.get_state()
        if st in (vlc.State.Playing, vlc.State.Paused) and player.is_seekable():
            player.set_time(pos_ms)
            time.sleep(0.15)
            cur = player.get_time()
            if cur is None or cur < 0 or abs(cur - pos_ms) > args.seek_tolerance_ms:
                player.set_time(pos_ms)  # libvlc can silently drop an early set_time()
            write_event(time.time(), 'HANDOVER_RELOAD_SEEK_OK', pos_s=pos_ms / 1000.0)
            state.in_handover_reload = False
            state.startup_grace_until = time.time() + args.startup_grace_s
            state.pending_seek_ms = None
            state.pending_seek_deadline = None
        elif st == vlc.State.Error:
            write_event(time.time(), 'HANDOVER_RELOAD_SEEK_FAIL', pos_s=pos_ms / 1000.0,
                        detail='player entered Error state')
            state.in_handover_reload = False
            state.pending_seek_ms = None
            state.pending_seek_deadline = None
        elif time.monotonic() > state.pending_seek_deadline:
            write_event(time.time(), 'HANDOVER_RELOAD_SEEK_FAIL', pos_s=pos_ms / 1000.0,
                        detail='not ready within %.1fs — giving up, resume '
                               'position lost' % args.switch_ready_timeout)
            state.in_handover_reload = False
            state.pending_seek_ms = None
            state.pending_seek_deadline = None

    # ── Initial playback ────────────────────────────────────────────────
    media = instance.media_new(args.initial_url)
    player.set_media(media)
    player.play()
    state.startup_grace_until = time.time() + args.startup_grace_s

    # ── Main loop ───────────────────────────────────────────────────────
    last_ctrl_mtime = None
    last_sample_t = 0.0
    t0 = time.time()

    while not stop_flag['stop']:
        try:
            mtime = os.stat(args.ctrl_file).st_mtime_ns
        except FileNotFoundError:
            mtime = None
        if mtime is not None and mtime != last_ctrl_mtime:
            last_ctrl_mtime = mtime
            try:
                with open(args.ctrl_file) as f:
                    line = f.read().strip()
                ap_str, url = line.split('|', 1)
                new_ap = int(ap_str)
                if url != state.current_url:
                    do_switch(new_ap, url)
            except Exception as e:
                write_event(time.time(), 'ERROR', detail='ctrl parse: %s' % e)

        check_pending_seek()

        while True:
            try:
                kind, ts, val = q.get_nowait()
            except queue.Empty:
                break
            handle_queue_item(kind, ts, val)

        now = time.time()
        if now - last_sample_t >= args.sample_dt:
            last_sample_t = now
            pos_ms = player.get_time()
            length_ms = player.get_length()
            st = player.get_state()
            tel_w.writerow([
                '%.3f' % now, '%.3f' % (now - t0),
                state.current_ap, state.current_url,
                '%.3f' % (max(pos_ms, 0) / 1000.0 if pos_ms else 0.0),
                '%.3f' % (max(length_ms, 0) / 1000.0 if length_ms else 0.0),
                str(st), int(state.currently_stalling),
                '%.1f' % state.last_buffer_pct,
                state.cum_stall_events, '%.3f' % state.cum_stall_time_s,
            ])
            tel_f.flush()

        if player.get_state() == vlc.State.Ended:
            write_event(time.time(), 'ENDED')
            break

        time.sleep(args.poll_interval)

    # ── Graceful shutdown ───────────────────────────────────────────────
    try:
        player.stop()
    except Exception:
        pass
    try:
        player.release()
        instance.release()
    except Exception:
        pass
    tel_f.close()
    evt_f.close()
    sys.exit(0)


if __name__ == '__main__':
    main()
