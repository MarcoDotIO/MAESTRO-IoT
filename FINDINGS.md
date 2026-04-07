# MAESTRO Simulated PoC Findings

## Run Summary

- Command: `uv run maestro-sim sweep configs/comparison_matrix.toml --output-root clean_runs`
- Runtime: `12m 44.41s`
- Dataset: `2,430` scenarios x `3` arms = `7,290` arm-level results
- Output root: `clean_runs/comparison_sweep`
- Key aggregate artifacts:
  - `clean_runs/comparison_sweep/manifest.csv`
  - `clean_runs/comparison_sweep/analysis_summary.csv`
  - `clean_runs/comparison_sweep/plots/`

The matrix covered:

- Node counts: `10`, `20`, `30`
- Load levels: `low`, `medium`, `high`
- Payload modes: `small`, `near_budget`, `fragmenting`
- Disruptions: `router_power_off`, `link_degradation`, `border_router_loss`
- Repetitions per condition: `30`

## Overall Results

Mean values across all `2,430` scenarios per arm:

| Arm | Delivery Ratio | Avg RTT (s) | Command P95 (s) | Recovery Avg (s) | Outage Avg (s) | Fragment Count | Retransmission Rate | Relative Energy | Ack Timeouts |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `zigbee` | 0.8699 | 0.3835 | 0.3900 | 4.9990 | 5.9191 | 244.5630 | 0.8157 | 1915.6142 | 187.8564 |
| `maestro` | 0.8447 | 0.4385 | 0.4842 | 5.2348 | 7.0522 | 190.9132 | 0.8973 | 1946.3595 | 188.8905 |
| `matter_thread` | 0.7694 | 0.4826 | 0.4985 | 6.3391 | 10.2346 | 243.5214 | 1.1475 | 2840.2288 | 258.6156 |

## Headline Findings

1. `MAESTRO` clearly improved over baseline `matter_thread`.
   - Delivery ratio improved by `+9.8%`.
   - Command P95 improved by `-2.9%`.
   - Recovery time improved by `-17.4%`.
   - Application outage window improved by `-31.1%`.
   - Fragment count improved by `-21.6%`.
   - Retransmission rate improved by `-21.8%`.
   - Relative energy cost improved by `-31.5%`.
   - Ack timeouts improved by `-27.0%`.

2. `MAESTRO` did not beat `zigbee` overall in this simulated PoC.
   - Delivery ratio was `-2.9%` lower than Zigbee overall.
   - Command P95 was `+24.1%` slower than Zigbee overall.
   - Recovery time was `+4.7%` longer than Zigbee overall.
   - The main MAESTRO advantage versus Zigbee was lower fragmentation: `-21.9%`.

3. The strongest MAESTRO gains appeared where fragmentation pressure was high.
   - In `near_budget`, MAESTRO beat baseline Matter/Thread by `+0.2466` delivery ratio, `-128.7` fragments, `-0.7543` retransmission rate, and `-8.52s` outage window.
   - In `fragmenting`, MAESTRO still reduced fragments and energy versus baseline Matter/Thread, but it slightly underperformed on delivery ratio (`-0.0127`) and outage (`+0.2556s`).

4. The value of MAESTRO increased with network size.
   - At `10` nodes, MAESTRO was only slightly better than baseline Matter/Thread on delivery (`+0.0104`).
   - At `20` nodes, that gap widened to `+0.0807`.
   - At `30` nodes, MAESTRO beat baseline Matter/Thread by `+0.1345` delivery ratio and also beat Zigbee on delivery (`0.6738` vs `0.6493`), while reducing energy versus Zigbee by about `305.9` relative units.

5. The hardest condition was `link_degradation`.
   - MAESTRO still improved on baseline Matter/Thread in that disruption class:
     - delivery ratio `+0.0625`
     - recovery time `-0.9255s`
     - outage window `-5.0330s`
     - fragment count `-53.1`
   - But Zigbee still held the lower command P95 and lower outage window under the same condition.

## Condition Highlights

MAESTRO minus baseline Matter/Thread, averaged by disruption type:

| Disruption | Delivery Ratio | Command P95 (s) | Recovery Avg (s) | Outage Avg (s) | Fragment Count | Retransmission Rate | Relative Energy |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `border_router_loss` | +0.1262 | -0.0039 | -0.4981 | -3.2493 | -51.4506 | -0.3642 | -1526.8387 |
| `link_degradation` | +0.0625 | +0.0016 | -0.9255 | -5.0330 | -53.0642 | -0.2064 | -641.8837 |
| `router_power_off` | +0.0370 | -0.0403 | -1.8892 | -1.2649 | -53.3099 | -0.1798 | -512.8855 |

MAESTRO minus baseline Matter/Thread, averaged by payload mode:

| Payload Mode | Delivery Ratio | Command P95 (s) | Recovery Avg (s) | Outage Avg (s) | Fragment Count | Retransmission Rate | Relative Energy |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `small` | -0.0082 | +0.0105 | -0.1909 | -1.2830 | +0.2037 | -0.0017 | +65.7177 |
| `near_budget` | +0.2466 | -0.0203 | -3.2929 | -8.5198 | -128.6667 | -0.7543 | -2288.2165 |
| `fragmenting` | -0.0127 | -0.0334 | +0.1710 | +0.2556 | -29.3617 | +0.0056 | -459.1091 |

## Scenario Win Counts

MAESTRO vs baseline Matter/Thread across all `2,430` matched scenarios:

| Metric | MAESTRO Wins | Ties | Losses |
| --- | ---: | ---: | ---: |
| Delivery ratio | 1411 | 238 | 781 |
| Command P95 | 1306 | 0 | 1094 |
| Fragment count | 1964 | 101 | 365 |
| Retransmission rate | 1656 | 23 | 751 |
| Relative energy cost | 1727 | 0 | 703 |

MAESTRO vs Zigbee across all `2,430` matched scenarios:

| Metric | MAESTRO Wins | Ties | Losses |
| --- | ---: | ---: | ---: |
| Delivery ratio | 648 | 378 | 1404 |
| Command P95 | 624 | 0 | 1793 |
| Fragment count | 1972 | 114 | 344 |
| Retransmission rate | 871 | 22 | 1537 |
| Relative energy cost | 817 | 0 | 1613 |

## Interpretation Against the Project Hypotheses

### H1

> Under increasing hop count and offered load, Matter over Thread will maintain more stable end-to-end latency and throughput than Zigbee.

This PoC does **not** support H1 for baseline `matter_thread` overall. Zigbee remained better on aggregate delivery and latency.

There is, however, a more nuanced result:

- MAESTRO narrowed the gap substantially.
- At `30` nodes, MAESTRO surpassed Zigbee on delivery ratio.
- MAESTRO consistently cut fragmentation relative to Zigbee, which suggests the FAME shaping policy is working as intended under larger and heavier scenarios.

### H2

> A mesh-aware adaptation policy combining Thread link metrics and Matter reliability signals will reduce application-visible outage time and improve p95 latency during topology disruption relative to baseline OpenThread behavior.

This PoC **does support H2**.

Across the full matrix, MAESTRO improved over baseline `matter_thread` on every major disruption-facing metric except a slight regression in command P95 under `link_degradation`:

- better delivery ratio
- lower recovery time
- lower application outage window
- lower fragment count
- lower retransmission rate
- lower relative energy cost
- fewer ack timeouts

## Notes and Caveats

- This is a simulated PoC, not a hardware-backed testbed.
- The `near_budget` workload is effectively a fragmentation-pressure case in the baseline because the configured payload plus optional fields exceed the `B=80` byte fragmentation budget.
- Energy is a relative modeled cost, not a current draw measurement.
- The main conclusion is therefore about comparative simulated behavior, not absolute real-world network performance.
