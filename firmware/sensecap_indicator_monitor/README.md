## SenseCAP Indicator Monitor

Arduino sketch for the SenseCAP Indicator D1 ESP32-S3 side.

It renders the hardware benchmark dashboard sent by the host runner over the
board's `USB-SERIAL CH340` serial port.

Expected host behavior:

- send `identify`
- send `bind_device`
- send `display_frame`
- optionally send `start_run` / `stop_run`

The sketch is intentionally display-only. It does not participate in the
benchmark traffic path.
