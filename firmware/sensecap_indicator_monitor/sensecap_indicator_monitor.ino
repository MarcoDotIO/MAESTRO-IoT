#include <Arduino.h>
#include <Arduino_GFX_Library.h>
#include <ArduinoJson.h>
#include <PCA95x5.h>

#define GFX_BL 45

namespace {

constexpr uint16_t kColorBackground = 0x0841;
constexpr uint16_t kColorPanel = 0x1082;
constexpr uint16_t kColorAccent = 0xFD20;
constexpr uint16_t kColorText = 0xFFFF;
constexpr uint16_t kColorMuted = 0xAD55;
constexpr uint16_t kColorGood = 0x2FEA;
constexpr uint16_t kColorWarn = 0xFD20;
constexpr uint16_t kColorBad = 0xF800;

struct NodeState {
  String node_id;
  String role;
  String parent;
  bool active = false;
};

struct DashboardState {
  String node_id = "sensecap-monitor";
  String role = "monitor";
  String board = "sensecap_indicator_d1";
  String arm = "idle";
  String run_name = "Waiting for host";
  bool display_enabled = true;
  bool run_active = false;
  bool clean_stop = false;
  float elapsed_s = 0.0f;
  float delivery_ratio = 0.0f;
  float avg_rtt_s = 0.0f;
  float p95_rtt_s = 0.0f;
  float retransmission_rate = 0.0f;
  float relative_energy_cost = 0.0f;
  uint32_t total_messages = 0;
  uint32_t delivered_messages = 0;
  uint32_t fragment_count = 0;
  uint32_t ack_timeouts = 0;
  uint32_t queue_depth_peak = 0;
  size_t node_count = 0;
  NodeState nodes[6];
  unsigned long last_frame_ms = 0;
};

DashboardState g_state;
char g_line_buffer[4096];
size_t g_line_length = 0;

Arduino_DataBus *bus = new Arduino_SWSPI(
    GFX_NOT_DEFINED /* DC */, PCA95x5::Port::P04 /* CS */,
    41 /* SCK */, 48 /* MOSI */, GFX_NOT_DEFINED /* MISO */);
Arduino_ESP32RGBPanel *rgbpanel = new Arduino_ESP32RGBPanel(
    18 /* DE */, 17 /* VSYNC */, 16 /* HSYNC */, 21 /* PCLK */,
    4 /* R0 */, 3 /* R1 */, 2 /* R2 */, 1 /* R3 */, 0 /* R4 */,
    10 /* G0 */, 9 /* G1 */, 8 /* G2 */, 7 /* G3 */, 6 /* G4 */, 5 /* G5 */,
    15 /* B0 */, 14 /* B1 */, 13 /* B2 */, 12 /* B3 */, 11 /* B4 */,
    1 /* hsync_polarity */, 10 /* hsync_front_porch */, 8 /* hsync_pulse_width */, 50 /* hsync_back_porch */,
    1 /* vsync_polarity */, 10 /* vsync_front_porch */, 8 /* vsync_pulse_width */, 20 /* vsync_back_porch */);
Arduino_RGB_Display *gfx = new Arduino_RGB_Display(
    480 /* width */, 480 /* height */, rgbpanel, 2 /* rotation */, true /* auto_flush */,
    bus, GFX_NOT_DEFINED /* RST */, st7701_type1_init_operations, sizeof(st7701_type1_init_operations));

void emit_json(JsonDocument &doc) {
  serializeJson(doc, Serial);
  Serial.println();
}

void emit_identify() {
  StaticJsonDocument<256> doc;
  doc["timestamp_s"] = millis() / 1000.0;
  doc["event"] = "identify";
  doc["node_id"] = g_state.node_id;
  doc["role"] = g_state.role;
  doc["board"] = g_state.board;
  doc["firmware"] = "sensecap_indicator_monitor";
  doc["target"] = "esp32s3";
  emit_json(doc);
}

void emit_ack(const char *command, const char *status) {
  StaticJsonDocument<192> doc;
  doc["timestamp_s"] = millis() / 1000.0;
  doc["event"] = "ack";
  doc["node_id"] = g_state.node_id;
  doc["command"] = command;
  doc["status"] = status;
  emit_json(doc);
}

void draw_label_value(int x, int y, const char *label, const String &value, uint16_t value_color = kColorText) {
  gfx->setTextSize(1);
  gfx->setTextColor(kColorMuted);
  gfx->setCursor(x, y);
  gfx->print(label);
  gfx->setTextColor(value_color);
  gfx->setCursor(x, y + 18);
  gfx->setTextSize(2);
  gfx->print(value);
}

void draw_metric_box(int x, int y, int w, int h, const char *label, const String &value, uint16_t value_color = kColorText) {
  gfx->fillRoundRect(x, y, w, h, 12, kColorPanel);
  draw_label_value(x + 14, y + 12, label, value, value_color);
}

String percent_string(float ratio) {
  char buffer[16];
  snprintf(buffer, sizeof(buffer), "%.1f%%", ratio * 100.0f);
  return String(buffer);
}

String float_string(float value, int precision = 2) {
  char buffer[24];
  snprintf(buffer, sizeof(buffer), "%.*f", precision, value);
  return String(buffer);
}

String stale_status() {
  if (g_state.last_frame_ms == 0) {
    return "Waiting";
  }
  unsigned long age_ms = millis() - g_state.last_frame_ms;
  if (g_state.clean_stop) {
    return "Complete";
  }
  if (g_state.run_active && age_ms > 3000) {
    return "Stale";
  }
  return g_state.run_active ? "Live" : "Idle";
}

uint16_t stale_status_color() {
  if (g_state.last_frame_ms == 0) {
    return kColorWarn;
  }
  unsigned long age_ms = millis() - g_state.last_frame_ms;
  if (g_state.clean_stop) {
    return kColorGood;
  }
  if (g_state.run_active && age_ms > 3000) {
    return kColorBad;
  }
  return g_state.run_active ? kColorGood : kColorWarn;
}

void render_dashboard() {
  gfx->fillScreen(kColorBackground);

  gfx->fillRoundRect(18, 18, 444, 70, 16, kColorPanel);
  gfx->setTextColor(kColorAccent);
  gfx->setTextSize(3);
  gfx->setCursor(32, 34);
  gfx->print("MAESTRO Hardware");

  gfx->setTextSize(1);
  gfx->setTextColor(kColorMuted);
  gfx->setCursor(34, 70);
  gfx->print(g_state.run_name);

  gfx->setTextColor(stale_status_color());
  gfx->setCursor(360, 34);
  gfx->setTextSize(2);
  gfx->print(stale_status());

  draw_metric_box(18, 106, 138, 90, "Arm", g_state.arm, kColorAccent);
  draw_metric_box(170, 106, 138, 90, "Delivery", percent_string(g_state.delivery_ratio),
                  g_state.delivery_ratio >= 0.95f ? kColorGood : kColorWarn);
  draw_metric_box(322, 106, 140, 90, "Elapsed", float_string(g_state.elapsed_s, 1) + "s", kColorText);

  draw_metric_box(18, 210, 138, 90, "Avg RTT", float_string(g_state.avg_rtt_s, 3) + "s", kColorText);
  draw_metric_box(170, 210, 138, 90, "P95 RTT", float_string(g_state.p95_rtt_s, 3) + "s", kColorText);
  draw_metric_box(322, 210, 140, 90, "Retries", float_string(g_state.retransmission_rate, 3), kColorText);

  draw_metric_box(18, 314, 138, 90, "Messages", String(g_state.delivered_messages) + "/" + String(g_state.total_messages), kColorText);
  draw_metric_box(170, 314, 138, 90, "Fragments", String(g_state.fragment_count), kColorText);
  draw_metric_box(322, 314, 140, 90, "Energy", float_string(g_state.relative_energy_cost, 2), kColorText);

  gfx->fillRoundRect(18, 418, 444, 44, 12, kColorPanel);
  gfx->setTextSize(1);
  gfx->setTextColor(kColorMuted);
  gfx->setCursor(30, 432);
  gfx->print("Nodes");

  int x = 90;
  for (size_t index = 0; index < g_state.node_count && index < 4; ++index) {
    const NodeState &node = g_state.nodes[index];
    uint16_t color = node.active ? kColorGood : kColorBad;
    gfx->fillRoundRect(x, 426, 88, 28, 10, color);
    gfx->setTextSize(1);
    gfx->setTextColor(kColorBackground);
    gfx->setCursor(x + 8, 435);
    gfx->print(node.node_id);
    x += 96;
  }
}

void show_boot_screen() {
  gfx->fillScreen(kColorBackground);
  gfx->setTextColor(kColorAccent);
  gfx->setTextSize(3);
  gfx->setCursor(36, 80);
  gfx->print("SenseCAP D1");
  gfx->setTextSize(2);
  gfx->setTextColor(kColorText);
  gfx->setCursor(36, 130);
  gfx->print("Benchmark Monitor");
  gfx->setTextSize(2);
  gfx->setTextColor(kColorMuted);
  gfx->setCursor(36, 190);
  gfx->print("Waiting for host frames");
}

void apply_bind_device(JsonVariantConst root) {
  const char *node_id = root["node_id"] | nullptr;
  const char *role = root["role"] | nullptr;
  const char *board = root["board"] | nullptr;
  if (node_id != nullptr) {
    g_state.node_id = String(node_id);
  }
  if (role != nullptr) {
    g_state.role = String(role);
  }
  if (board != nullptr) {
    g_state.board = String(board);
  }
  g_state.display_enabled = root["display_enabled"] | true;
}

void apply_display_frame(JsonVariantConst root) {
  g_state.arm = String(root["arm"] | "idle");
  g_state.run_name = String(root["run_name"] | "hardware");
  g_state.elapsed_s = root["elapsed_s"] | 0.0f;

  JsonVariantConst summary = root["summary"];
  g_state.delivery_ratio = summary["delivery_ratio"] | 0.0f;
  g_state.avg_rtt_s = summary["avg_rtt_s"] | 0.0f;
  g_state.p95_rtt_s = summary["p95_rtt_s"] | 0.0f;
  g_state.retransmission_rate = summary["retransmission_rate"] | 0.0f;
  g_state.relative_energy_cost = summary["relative_energy_cost"] | 0.0f;
  g_state.total_messages = summary["total_messages"] | 0U;
  g_state.delivered_messages = summary["delivered_messages"] | 0U;
  g_state.fragment_count = summary["fragment_count"] | 0U;
  g_state.ack_timeouts = summary["ack_timeouts"] | 0U;
  g_state.queue_depth_peak = summary["queue_depth_peak"] | 0U;

  g_state.node_count = 0;
  JsonArrayConst nodes = root["nodes"].as<JsonArrayConst>();
  for (JsonVariantConst node : nodes) {
    if (g_state.node_count >= 6) {
      break;
    }
    NodeState &slot = g_state.nodes[g_state.node_count++];
    slot.node_id = String(node["node_id"] | "");
    slot.role = String(node["role"] | "");
    slot.parent = String(node["parent"] | "");
    slot.active = node["active"] | false;
  }

  g_state.run_active = true;
  g_state.clean_stop = false;
  g_state.last_frame_ms = millis();
}

void handle_json_line(char *line) {
  StaticJsonDocument<3072> doc;
  DeserializationError error = deserializeJson(doc, line);
  if (error) {
    return;
  }

  const char *cmd = doc["cmd"] | "";
  if (strcmp(cmd, "identify") == 0) {
    emit_identify();
    return;
  }
  if (strcmp(cmd, "bind_device") == 0) {
    apply_bind_device(doc.as<JsonVariantConst>());
    emit_ack("bind_device", "ok");
    render_dashboard();
    return;
  }
  if (strcmp(cmd, "display_frame") == 0) {
    apply_display_frame(doc.as<JsonVariantConst>());
    emit_ack("display_frame", "ok");
    render_dashboard();
    return;
  }
  if (strcmp(cmd, "start_run") == 0) {
    g_state.run_active = true;
    g_state.clean_stop = false;
    emit_ack("start_run", "ok");
    render_dashboard();
    return;
  }
  if (strcmp(cmd, "stop_run") == 0) {
    g_state.run_active = false;
    g_state.clean_stop = true;
    emit_ack("stop_run", "ok");
    render_dashboard();
    return;
  }
  if (strcmp(cmd, "configure") == 0) {
    emit_ack("configure", "ok");
    return;
  }
}

void process_serial() {
  while (Serial.available() > 0) {
    char c = static_cast<char>(Serial.read());
    if (c == '\r') {
      continue;
    }
    if (c == '\n') {
      g_line_buffer[g_line_length] = '\0';
      if (g_line_length > 0) {
        handle_json_line(g_line_buffer);
      }
      g_line_length = 0;
      continue;
    }
    if (g_line_length + 1 >= sizeof(g_line_buffer)) {
      g_line_length = 0;
      continue;
    }
    g_line_buffer[g_line_length++] = c;
  }
}

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(200);

  if (!gfx->begin()) {
    Serial.println("{\"event\":\"boot_error\",\"reason\":\"gfx_begin_failed\"}");
  }

  pinMode(GFX_BL, OUTPUT);
  digitalWrite(GFX_BL, HIGH);

  show_boot_screen();
  emit_identify();
}

void loop() {
  process_serial();

  if (g_state.run_active && g_state.last_frame_ms > 0 && millis() - g_state.last_frame_ms > 5000) {
    g_state.run_active = false;
    g_state.clean_stop = false;
    render_dashboard();
    g_state.last_frame_ms = millis();
  }

  delay(10);
}
