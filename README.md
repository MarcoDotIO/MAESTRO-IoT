# MAESTRO Simulated PoC

`maestro-sim` is a Python 3.13 discrete-event simulation harness for the MAESTRO project.

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

## Quick start

```bash
uv sync
uv run maestro-sim run configs/validation.toml
uv run maestro-sim sweep configs/comparison_matrix.toml
uv run maestro-sim analyze outputs/<run-dir>
```

To reproduce the full findings run in a clean output root:

```bash
uv run maestro-sim sweep configs/comparison_matrix.toml --output-root clean_runs
```

## Repo layout

- `src/maestro_sim/`: simulator, policy, CLI, analysis pipeline
- `configs/`: validation and full matrix configs
- `tests/`: policy, run, analysis, and sweep coverage
- `FINDINGS.md`: summary of the completed clean matrix run

## Deliverables

- Three experiment arms with a shared workload surface:
  - `zigbee`
  - `matter_thread`
  - `maestro`
- Stable driver boundary for future hardware migration
- Validation run config and full comparison matrix config
- Plot and CSV generation for reproducible analysis
- Test suite runnable with `uv run pytest`

## Notes

- The simulation is behaviorally faithful, not packet-accurate.
- MAESTRO is implemented as the only adaptive difference in the `maestro` arm.
- Energy values are relative cost estimates derived from radio state and retries.
- Generated run directories such as `outputs/` and `clean_runs/` are intentionally ignored from git.
