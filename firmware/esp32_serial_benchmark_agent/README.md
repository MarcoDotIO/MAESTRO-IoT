# ESP32 Serial Benchmark Agent

Minimal ESP-IDF scaffold for the MAESTRO hardware runner.

## Purpose

This app provides the serial JSON-line control surface used by:

- `maestro-sim hardware-run`
- `maestro-sim hardware-discover`

It is a firmware shell, not a finished radio benchmark implementation.

## Supported commands

- `identify`
- `configure`
- `start_run`
- `stop_run`
- `set_active`
- `set_link_profile`
- `display_frame`

## Current behavior

- emits `identify` at boot
- acknowledges commands with state/heartbeat events
- emits a `metric_snapshot` when a run is stopped

## Build

Requires ESP-IDF on the host.

```bash
idf.py set-target esp32s3
idf.py build
idf.py -p /dev/cu.YOUR_PORT flash monitor
```

## Next integration step

Wire the transport-specific telemetry and command paths into the command handlers so the device emits real `message_result` and `policy_decision` events during a run.
