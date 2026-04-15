# Hardware Benchmark

The repo now contains a hardware benchmark path beside the simulator.

## What is implemented

- `maestro-sim hardware-discover`
  - lists serial devices visible to the host
- `maestro-sim hardware-run <config>`
  - runs one hardware arm from a TOML/JSON config
  - writes the same artifact family as the simulator where possible:
    - `events.csv`
    - `node_state.csv`
    - `message_traces.csv`
    - `node_metrics.csv`
    - `policy_decisions.csv`
    - `summary.json`
    - `combined_summary.csv`
    - `plots/*.png`
- sample configs:
  - [configs/hardware_matter_thread.example.toml](/Users/marcodotio/Developer/MAESTRO-IoT/configs/hardware_matter_thread.example.toml)
  - [configs/hardware_maestro.example.toml](/Users/marcodotio/Developer/MAESTRO-IoT/configs/hardware_maestro.example.toml)
- an ESP-IDF serial benchmark agent scaffold:
  - [firmware/esp32_serial_benchmark_agent/README.md](/Users/marcodotio/Developer/MAESTRO-IoT/firmware/esp32_serial_benchmark_agent/README.md)

## Current boundary

The host runner is complete enough to orchestrate attached boards once their firmware emits the agreed JSON-line events.

The repo does **not** yet include:

- a verified SenseCAP Indicator RP2040 display app
- a verified OpenThread/Matter radio implementation for the attached Thread devkit
- a real Zigbee hardware arm

Those remain hardware/toolchain follow-through tasks. The host-side protocol and artifact pipeline are now in place.

## Typical flow

```bash
uv sync
uv run maestro-sim hardware-discover --json
cp configs/hardware_maestro.example.toml /tmp/hardware_maestro.toml
# edit the serial ports in the copied config
uv run maestro-sim hardware-run /tmp/hardware_maestro.toml
```

## Serial event contract

The host runner expects newline-delimited JSON from each device.

Useful event types:

- `identify`
- `node_state`
- `message_result`
- `policy_decision`
- `metric_snapshot`

The minimum useful record for KPI generation is `message_result`.

Example:

```json
{
  "timestamp_s": 1.42,
  "event": "message_result",
  "message_id": "maestro-000123",
  "kind": "telemetry",
  "source": "sensor-a",
  "target": "controller",
  "created_at_s": 1.31,
  "completed_at_s": 1.42,
  "delivered": true,
  "payload_bytes": 76,
  "fragments": 1,
  "retries": 0,
  "path": ["sensor-a", "thread-br", "controller"],
  "rtt_s": 0.11,
  "urgent": false
}
```
