# MAESTRO Simulation + Hardware Benchmark

`maestro-sim` is a Python 3.13 project for the MAESTRO simulator and the first hardware benchmark control path.

It models three experiment arms with a shared workload surface:

- `zigbee`
- `matter_thread`
- `maestro`

The simulator produces:

- `events.csv`
- `node_state.csv`
- `message_traces.csv`
- `policy_decisions.csv`
- `summary.json`
- `plots/*.png`

The hardware runner produces the same artifact family for attached-device runs when the boards speak the benchmark serial protocol.

## Quick start

```bash
uv sync
uv run maestro-sim run configs/validation.toml
uv run maestro-sim sweep configs/comparison_matrix.toml
uv run maestro-sim analyze outputs/<run-dir>
uv run maestro-sim hardware-discover --json
# edit a copied example config with real serial ports
uv run maestro-sim hardware-run /tmp/hardware_maestro.toml
```

To reproduce the full findings run in a clean output root:

```bash
uv run maestro-sim sweep configs/comparison_matrix.toml --output-root clean_runs
```

## Repo layout

- `src/maestro_sim/`: simulator, hardware runner, policy, CLI, analysis pipeline
- `configs/`: simulation and hardware example configs
- `tests/`: policy, simulation, analysis, and hardware-runner coverage
- `firmware/`: ESP32 benchmark-agent scaffold
- `FINDINGS.md`: summary of the completed clean matrix run
- `HARDWARE_BENCHMARK.md`: host-runner protocol and workflow notes

## Deliverables

- Three experiment arms with a shared workload surface:
  - `zigbee`
  - `matter_thread`
  - `maestro`
- Stable driver boundary for future hardware migration
- Host-side hardware benchmark runner with serial discovery and artifact normalization
- Validation run config and full comparison matrix config
- Sample hardware benchmark configs for `matter_thread` and `maestro`
- Plot and CSV generation for reproducible analysis
- Test suite runnable with `uv run pytest`

## Notes

- The simulation is behaviorally faithful, not packet-accurate.
- MAESTRO is implemented as the only adaptive difference in the `maestro` arm.
- Energy values are relative cost estimates derived from radio state and retries.
- The hardware runner is ready for attached boards that emit JSON-line events; radio-stack bring-up on the actual ESP32/Thread hardware remains a board/toolchain follow-through step.
- Generated run directories such as `outputs/` and `clean_runs/` are intentionally ignored from git.
