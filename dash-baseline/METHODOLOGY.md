# Methodology — Single-Vehicle DASH ABR Baseline (draft for the paper)

> Draft wording you can adapt. It states honestly what is measured vs imposed,
> so it is defensible under review. Edit numbers/citations to taste.

## A. Scenario and topology
We evaluate a controlled single-vehicle baseline in which one vehicle traverses
a straight road segment of 600 m, passing a single roadside unit (RSU) located
at the midpoint. The RSU is modelled as an IEEE 802.11-class access point with a
nominal coverage radius of 300 m. The vehicle moves at a constant 1 m/s and is
sampled once per second, yielding 600 samples per pass. Video content is served
from a host attached to the RSU over the wired backhaul. The experiment is built
on Mininet-WiFi with the `wmediumd` interference model; the access point operates
in standalone mode (no external SDN controller is required for a single-AP
baseline).

## B. Channel and received signal
Radio propagation uses the log-distance path-loss model provided by Mininet-WiFi
with path-loss exponent n = 1.9, representative of a largely line-of-sight campus
road. The received signal strength (RSSI) is read directly from the live wireless
interface (`iw … link`) once per second, so all reported RSSI values are measured
from the simulated radio, not assumed. The measured RSSI ranges from approximately
−29 dBm near the RSU to approximately −76 dBm at the 300 m coverage edge.

## C. Imposed bandwidth profile (and why)
For a single vehicle requesting at most 5 Mbps within a 300 m RSU, the 802.11p
link is not the bottleneck: a Shannon estimate over a 10 MHz channel
(C = B·log2(1+SNR), noise floor ≈ −97 dBm) exceeds 12 Mbps for all RSSI above
about −90 dBm, i.e. across the entire route. Consequently the unconstrained link
would never force adaptation. To exercise and validate the adaptive-bitrate (ABR)
logic across its full operating range, we therefore **impose** a controlled
bandwidth profile via Linux traffic control (`tc`/HTB) on the server downlink.
The imposed bandwidth is defined as a single monotonic linear function of the
measured RSSI, mapping [−76, −29] dBm to [0.5, 10.0] Mbps. This is an experimental
stimulus, not a claim about 802.11p capacity, and it is applied identically to the
DASH and CDN configurations so the comparison between them remains fair.

## D. Video content and ABR client
The source is re-encoded into a three-rendition DASH ladder — 640×360 at 1.0 Mbps,
1280×720 at 2.5 Mbps, and 1920×1080 at 5.0 Mbps — with 4 s segments. Playback uses
VLC (3.0.x) with its rate-based adaptive logic; the client performs all rendition
decisions autonomously. To obtain a consistent, conservative startup that matches
production players, the link is pinned to a low bootstrap bandwidth before playback
begins so the client starts at the lowest rendition and then ramps according to the
imposed profile (lowest-first startup policy). The same startup policy is used for
both architectures.

## E. Metrics
1. **Selected rendition.** The rendition actually played is recovered from the
   HTTP server access log by identifying which `chunk-streamN` segment the client
   requests at each instant (N ∈ {0,1,2} → 360p/720p/1080p). This reflects the
   client's real decisions rather than any imposed value.
2. **Packet loss.** A continuous ICMP probe (20 packets/s) runs over the wireless
   path. To measure link reliability rather than transport contention, the probe
   is placed in a dedicated, rate-protected `tc` class so it is never starved by
   the video flow; the reported loss therefore reflects pure wireless reliability.
3. **Rebuffering.** Stall events are derived with a standard playback-buffer model
   driven by the measured segment fetch times (4 s per segment; 8 s start-up
   buffer; 30 s maximum buffer). A stall second is recorded whenever the modelled
   buffer empties during playback.
4. **RSSI and imposed bandwidth** are logged per second as described above.

## F. Repetitions and statistics
Because the system contains real timing variability (player buffering, segment
scheduling, OS scheduling), each configuration is run N = 10 times. Results are
aligned by vehicle position and reported as the mean with dispersion: standard
deviation for the selected rendition, 95% confidence interval for packet loss, and
the fraction of runs stalling for rebuffering. RSSI and imposed bandwidth are
deterministic functions of position and therefore exhibit negligible variance,
which serves as an internal consistency check.

## G. Honest limitations (state these)
- The bandwidth profile is an imposed stimulus, not measured 802.11p throughput;
  it is chosen to sweep the ABR operating range, not to reproduce link capacity.
- Rebuffering is a buffer-model estimate from real fetch timing, not the player's
  internal clock.
- The single-vehicle link is not capacity-limited; meaningful capacity contention
  is expected only under multi-vehicle load and handover, which motivates the
  subsequent multi-vehicle and DASH-vs-CDN experiments.
