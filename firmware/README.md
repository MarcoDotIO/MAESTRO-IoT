# Firmware Scaffolding

This directory contains firmware-facing scaffolding for the host hardware benchmark runner.

## Contents

- `esp32_serial_benchmark_agent/`
  - ESP-IDF app scaffold for ESP32-class nodes that speak the JSON-line control protocol expected by the Python host runner

## Scope

The scaffold is intentionally focused on the control plane:

- identify
- configure
- start/stop run
- node active/inactive state
- link degradation acknowledgements
- metric snapshot emission

It does not yet contain a validated radio-stack implementation for:

- Matter over Thread
- true Zigbee benchmarking
- SenseCAP Indicator display rendering

Use it as the firmware shell that the real transport-specific logic plugs into.
