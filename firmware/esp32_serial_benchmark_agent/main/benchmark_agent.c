#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "cJSON.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "maestro_agent";

typedef struct {
  bool run_active;
  bool node_active;
  bool route_available;
  bool link_degraded;
  bool auto_fault_profile_enabled;
  bool display_enabled;
  char node_id[32];
  char role[24];
  char board[48];
  char arm[24];
  char route_id[32];
  char command_target[32];
  float telemetry_interval_s;
  float command_interval_s;
  float warmup_s;
  float current_interval_s;
  float ack_timeout_s;
  float retry_backoff_s;
  float service_time_per_fragment_s;
  float queue_energy_factor;
  float urgent_probability;
  float run_started_at_s;
  float next_message_at_s;
  int fragmentation_budget_bytes;
  int payload_base_bytes;
  int urgent_extra_bytes;
  int max_retries;
  int optional_fields[8];
  size_t optional_field_count;
  uint32_t sequence;
  uint32_t sent;
  uint32_t delivered;
  uint32_t dropped;
  uint32_t retries;
  uint32_t fragments;
  uint32_t parent_switches;
  uint32_t ack_timeouts;
  uint32_t queue_depth_peak;
  float energy_cost;
} agent_state_t;

static agent_state_t s_state = {
    .run_active = false,
    .node_active = true,
    .route_available = true,
    .link_degraded = false,
    .auto_fault_profile_enabled = true,
    .display_enabled = false,
    .node_id = "esp32-agent",
    .role = "sensor",
    .board = "esp32",
    .arm = "maestro",
    .route_id = "border-router",
    .command_target = "thread-br",
    .telemetry_interval_s = 0.75f,
    .command_interval_s = 12.0f,
    .warmup_s = 0.5f,
    .current_interval_s = 0.75f,
    .ack_timeout_s = 0.75f,
    .retry_backoff_s = 0.15f,
    .service_time_per_fragment_s = 0.03f,
    .queue_energy_factor = 0.08f,
    .urgent_probability = 0.45f,
    .fragmentation_budget_bytes = 80,
    .payload_base_bytes = 70,
    .urgent_extra_bytes = 24,
    .max_retries = 3,
    .optional_fields = {36, 32, 28},
    .optional_field_count = 3,
};

static double monotonic_seconds(void) {
  return (double)esp_timer_get_time() / 1000000.0;
}

static void emit_json(cJSON *root) {
  char *rendered = cJSON_PrintUnformatted(root);
  if (rendered != NULL) {
    printf("%s\n", rendered);
    fflush(stdout);
    cJSON_free(rendered);
  }
  cJSON_Delete(root);
}

static void emit_identify(void) {
  cJSON *root = cJSON_CreateObject();
  cJSON_AddNumberToObject(root, "timestamp_s", monotonic_seconds());
  cJSON_AddStringToObject(root, "event", "identify");
  cJSON_AddStringToObject(root, "node_id", s_state.node_id);
  cJSON_AddStringToObject(root, "role", s_state.role);
  cJSON_AddStringToObject(root, "board", s_state.board);
  cJSON_AddStringToObject(root, "firmware", "esp32_serial_benchmark_agent");
  cJSON_AddStringToObject(root, "target", CONFIG_IDF_TARGET);
  emit_json(root);
}

static void emit_node_state(const char *reason) {
  cJSON *root = cJSON_CreateObject();
  cJSON_AddNumberToObject(root, "timestamp_s", monotonic_seconds());
  cJSON_AddStringToObject(root, "event", "node_state");
  cJSON_AddStringToObject(root, "node_id", s_state.node_id);
  cJSON_AddStringToObject(root, "role", s_state.role);
  cJSON_AddBoolToObject(root, "active", s_state.node_active);
  cJSON_AddStringToObject(root, "reason", reason);
  cJSON_AddStringToObject(root, "parent", s_state.route_id);
  cJSON_AddBoolToObject(root, "route_available", s_state.route_available);
  cJSON_AddBoolToObject(root, "link_degraded", s_state.link_degraded);
  cJSON_AddNumberToObject(root, "queue_depth_peak", s_state.queue_depth_peak);
  emit_json(root);
}

static void emit_metric_snapshot(void) {
  cJSON *root = cJSON_CreateObject();
  cJSON_AddNumberToObject(root, "timestamp_s", monotonic_seconds());
  cJSON_AddStringToObject(root, "event", "metric_snapshot");
  cJSON_AddNumberToObject(root, "sent", s_state.sent);
  cJSON_AddNumberToObject(root, "delivered", s_state.delivered);
  cJSON_AddNumberToObject(root, "dropped", s_state.dropped);
  cJSON_AddNumberToObject(root, "retries", s_state.retries);
  cJSON_AddNumberToObject(root, "fragments", s_state.fragments);
  cJSON_AddNumberToObject(root, "parent_switches", s_state.parent_switches);
  cJSON_AddNumberToObject(root, "ack_timeouts", s_state.ack_timeouts);
  cJSON_AddNumberToObject(root, "queue_depth_peak", s_state.queue_depth_peak);
  cJSON_AddNumberToObject(root, "energy_cost", s_state.energy_cost);
  emit_json(root);
}

static void emit_ack(const char *command, const char *status) {
  cJSON *root = cJSON_CreateObject();
  cJSON_AddNumberToObject(root, "timestamp_s", monotonic_seconds());
  cJSON_AddStringToObject(root, "event", "ack");
  cJSON_AddStringToObject(root, "command", command);
  cJSON_AddStringToObject(root, "status", status);
  emit_json(root);
}

static int total_optional_bytes(void) {
  int total = 0;
  for (size_t index = 0; index < s_state.optional_field_count; ++index) {
    total += s_state.optional_fields[index];
  }
  return total;
}

static int fragment_count_for_payload(int payload_bytes) {
  if (s_state.fragmentation_budget_bytes <= 0) {
    return 1;
  }
  return (payload_bytes + s_state.fragmentation_budget_bytes - 1) / s_state.fragmentation_budget_bytes;
}

static bool is_maestro_arm(void) {
  return strcmp(s_state.arm, "maestro") == 0;
}

static bool is_monitor_role(void) {
  return strcmp(s_state.role, "monitor") == 0 || s_state.display_enabled;
}

static bool sequence_is_urgent(uint32_t sequence) {
  int threshold = (int)(s_state.urgent_probability * 100.0f + 0.5f);
  if (threshold <= 0) {
    return false;
  }
  return (int)((sequence * 37U) % 100U) < threshold;
}

static void reset_run_metrics(void) {
  s_state.sent = 0;
  s_state.delivered = 0;
  s_state.dropped = 0;
  s_state.retries = 0;
  s_state.fragments = 0;
  s_state.parent_switches = 0;
  s_state.ack_timeouts = 0;
  s_state.queue_depth_peak = 0;
  s_state.energy_cost = 0.0f;
  s_state.sequence = 0;
  s_state.current_interval_s = s_state.telemetry_interval_s;
  s_state.run_started_at_s = monotonic_seconds();
  s_state.next_message_at_s = s_state.run_started_at_s + s_state.warmup_s;
}

static void apply_auto_fault_profile(double now) {
  if (!s_state.auto_fault_profile_enabled || is_monitor_role() || !s_state.run_active) {
    return;
  }

  const double elapsed = now - (double)s_state.run_started_at_s;
  const bool route_available = !(elapsed >= 50.0 && elapsed < 55.0);
  const bool link_degraded = (elapsed >= 58.0 && elapsed < 63.0);

  if (route_available != s_state.route_available) {
    s_state.route_available = route_available;
    emit_node_state("auto_fault_profile_route");
  }
  if (link_degraded != s_state.link_degraded) {
    s_state.link_degraded = link_degraded;
    emit_node_state("auto_fault_profile_link");
  }
}

static void emit_policy_decision(uint32_t sequence, bool urgent, int payload_before_bytes,
                                 int payload_after_bytes, int optional_fields_dropped,
                                 float interval_before_s, float interval_after_s,
                                 bool switched_parent) {
  cJSON *root = cJSON_CreateObject();
  cJSON_AddNumberToObject(root, "timestamp_s", monotonic_seconds());
  cJSON_AddStringToObject(root, "event", "policy_decision");
  cJSON_AddStringToObject(root, "node_id", s_state.node_id);
  cJSON_AddStringToObject(root, "reason", urgent ? "urgent_telemetry" : "telemetry");
  cJSON_AddStringToObject(root, "current_parent", s_state.route_id);
  cJSON_AddStringToObject(root, "selected_parent", switched_parent ? "fallback-parent" : s_state.route_id);
  cJSON_AddBoolToObject(root, "switched", switched_parent);
  cJSON_AddNumberToObject(root, "payload_before_bytes", payload_before_bytes);
  cJSON_AddNumberToObject(root, "payload_after_bytes", payload_after_bytes);
  cJSON_AddNumberToObject(root, "optional_fields_dropped", optional_fields_dropped);
  cJSON_AddNumberToObject(root, "interval_before_s", interval_before_s);
  cJSON_AddNumberToObject(root, "interval_after_s", interval_after_s);
  cJSON_AddNumberToObject(root, "ehat", 0.15f + 0.05f * (float)(sequence % 5U));
  cJSON_AddNumberToObject(root, "rhat", s_state.link_degraded ? 0.45f : 0.10f);
  cJSON_AddNumberToObject(root, "fhat", payload_before_bytes > s_state.fragmentation_budget_bytes ? 0.60f : 0.10f);
  cJSON_AddNumberToObject(root, "lhat", s_state.route_available ? 0.05f : 0.90f);
  cJSON_AddNumberToObject(root, "score_selected", switched_parent ? 0.67f : 0.58f);
  cJSON_AddNumberToObject(root, "score_current", 0.51f);
  emit_json(root);
}

static void emit_message_result(uint32_t sequence, const char *kind, bool urgent,
                                int payload_bytes, int fragments, int retries,
                                bool delivered, float created_at_s, float completed_at_s,
                                float rtt_s, const char *failure_reason, bool switched_parent,
                                float energy_cost) {
  char message_id[48];
  snprintf(message_id, sizeof(message_id), "%s-%06lu", s_state.node_id, (unsigned long)sequence);

  cJSON *root = cJSON_CreateObject();
  cJSON_AddNumberToObject(root, "timestamp_s", monotonic_seconds());
  cJSON_AddStringToObject(root, "event", "message_result");
  cJSON_AddStringToObject(root, "message_id", message_id);
  cJSON_AddStringToObject(root, "kind", kind);
  cJSON_AddStringToObject(root, "source", s_state.node_id);
  cJSON_AddStringToObject(root, "target", strcmp(kind, "command") == 0 ? s_state.command_target : "controller");
  cJSON_AddNumberToObject(root, "created_at_s", created_at_s);
  if (delivered) {
    cJSON_AddNumberToObject(root, "completed_at_s", completed_at_s);
    cJSON_AddNumberToObject(root, "rtt_s", rtt_s);
  }
  cJSON_AddBoolToObject(root, "delivered", delivered);
  cJSON_AddNumberToObject(root, "payload_bytes", payload_bytes);
  cJSON_AddNumberToObject(root, "fragments", fragments);
  cJSON_AddNumberToObject(root, "retries", retries);
  cJSON_AddBoolToObject(root, "urgent", urgent);
  cJSON_AddNumberToObject(root, "queue_depth_peak", s_state.queue_depth_peak);
  cJSON_AddNumberToObject(root, "energy_cost", energy_cost);
  if (!delivered && failure_reason != NULL) {
    cJSON_AddStringToObject(root, "failure_reason", failure_reason);
  }

  cJSON *path = cJSON_CreateArray();
  cJSON_AddItemToArray(path, cJSON_CreateString(s_state.node_id));
  cJSON_AddItemToArray(path, cJSON_CreateString(switched_parent ? "fallback-parent" : s_state.route_id));
  cJSON_AddItemToArray(path, cJSON_CreateString("controller"));
  cJSON_AddItemToObject(root, "path", path);
  emit_json(root);
}

static void apply_config(const cJSON *config) {
  const cJSON *arm = cJSON_GetObjectItemCaseSensitive(config, "arm");
  if (cJSON_IsString(arm) && arm->valuestring != NULL) {
    strlcpy(s_state.arm, arm->valuestring, sizeof(s_state.arm));
  }

  const cJSON *traffic = cJSON_GetObjectItemCaseSensitive(config, "traffic");
  if (cJSON_IsObject(traffic)) {
    const cJSON *telemetry_interval =
        cJSON_GetObjectItemCaseSensitive(traffic, "telemetry_interval_s");
    if (cJSON_IsNumber(telemetry_interval)) {
      s_state.telemetry_interval_s = (float)telemetry_interval->valuedouble;
      s_state.current_interval_s = s_state.telemetry_interval_s;
    }
    const cJSON *command_interval =
        cJSON_GetObjectItemCaseSensitive(traffic, "command_interval_s");
    if (cJSON_IsNumber(command_interval)) {
      s_state.command_interval_s = (float)command_interval->valuedouble;
    }
    const cJSON *command_target =
        cJSON_GetObjectItemCaseSensitive(traffic, "command_target");
    if (cJSON_IsString(command_target) && command_target->valuestring != NULL) {
      strlcpy(s_state.command_target, command_target->valuestring, sizeof(s_state.command_target));
    }
    const cJSON *warmup = cJSON_GetObjectItemCaseSensitive(traffic, "warmup_s");
    if (cJSON_IsNumber(warmup)) {
      s_state.warmup_s = (float)warmup->valuedouble;
    }

    const cJSON *payload_profile =
        cJSON_GetObjectItemCaseSensitive(traffic, "payload_profile");
    if (cJSON_IsObject(payload_profile)) {
      const cJSON *base_bytes =
          cJSON_GetObjectItemCaseSensitive(payload_profile, "base_bytes");
      if (cJSON_IsNumber(base_bytes)) {
        s_state.payload_base_bytes = base_bytes->valueint;
      }
      const cJSON *urgent_extra =
          cJSON_GetObjectItemCaseSensitive(payload_profile, "urgent_extra_bytes");
      if (cJSON_IsNumber(urgent_extra)) {
        s_state.urgent_extra_bytes = urgent_extra->valueint;
      }
      const cJSON *urgent_probability =
          cJSON_GetObjectItemCaseSensitive(payload_profile, "urgent_probability");
      if (cJSON_IsNumber(urgent_probability)) {
        s_state.urgent_probability = (float)urgent_probability->valuedouble;
      }
      const cJSON *optional_fields =
          cJSON_GetObjectItemCaseSensitive(payload_profile, "optional_fields");
      if (cJSON_IsArray(optional_fields)) {
        size_t count = 0;
        cJSON *item = NULL;
        cJSON_ArrayForEach(item, optional_fields) {
          if (count >= (sizeof(s_state.optional_fields) / sizeof(s_state.optional_fields[0]))) {
            break;
          }
          if (cJSON_IsNumber(item)) {
            s_state.optional_fields[count++] = item->valueint;
          }
        }
        s_state.optional_field_count = count;
      }
    }
  }

  const cJSON *protocol = cJSON_GetObjectItemCaseSensitive(config, "protocol");
  if (cJSON_IsObject(protocol)) {
    const cJSON *ack_timeout = cJSON_GetObjectItemCaseSensitive(protocol, "ack_timeout_s");
    if (cJSON_IsNumber(ack_timeout)) {
      s_state.ack_timeout_s = (float)ack_timeout->valuedouble;
    }
    const cJSON *max_retries = cJSON_GetObjectItemCaseSensitive(protocol, "max_retries");
    if (cJSON_IsNumber(max_retries)) {
      s_state.max_retries = max_retries->valueint;
    }
    const cJSON *retry_backoff =
        cJSON_GetObjectItemCaseSensitive(protocol, "retry_backoff_s");
    if (cJSON_IsNumber(retry_backoff)) {
      s_state.retry_backoff_s = (float)retry_backoff->valuedouble;
    }
    const cJSON *service_time =
        cJSON_GetObjectItemCaseSensitive(protocol, "service_time_per_fragment_s");
    if (cJSON_IsNumber(service_time)) {
      s_state.service_time_per_fragment_s = (float)service_time->valuedouble;
    }
    const cJSON *queue_energy =
        cJSON_GetObjectItemCaseSensitive(protocol, "queue_energy_factor");
    if (cJSON_IsNumber(queue_energy)) {
      s_state.queue_energy_factor = (float)queue_energy->valuedouble;
    }
  }

  const cJSON *policy = cJSON_GetObjectItemCaseSensitive(config, "policy");
  if (cJSON_IsObject(policy)) {
    const cJSON *budget =
        cJSON_GetObjectItemCaseSensitive(policy, "fragmentation_budget_bytes");
    if (cJSON_IsNumber(budget)) {
      s_state.fragmentation_budget_bytes = budget->valueint;
    }
  }
}

static void handle_command(const cJSON *root) {
  const cJSON *command = cJSON_GetObjectItemCaseSensitive(root, "cmd");
  if (!cJSON_IsString(command) || command->valuestring == NULL) {
    return;
  }

  if (strcmp(command->valuestring, "identify") == 0) {
    emit_identify();
    return;
  }

  if (strcmp(command->valuestring, "configure") == 0) {
    const cJSON *config = cJSON_GetObjectItemCaseSensitive(root, "config");
    if (cJSON_IsObject(config)) {
      apply_config(config);
    }
    emit_ack("configure", "ok");
    return;
  }

  if (strcmp(command->valuestring, "bind_device") == 0) {
    const cJSON *node_id = cJSON_GetObjectItemCaseSensitive(root, "node_id");
    if (cJSON_IsString(node_id) && node_id->valuestring != NULL) {
      strlcpy(s_state.node_id, node_id->valuestring, sizeof(s_state.node_id));
    }
    const cJSON *role = cJSON_GetObjectItemCaseSensitive(root, "role");
    if (cJSON_IsString(role) && role->valuestring != NULL) {
      strlcpy(s_state.role, role->valuestring, sizeof(s_state.role));
    }
    const cJSON *board = cJSON_GetObjectItemCaseSensitive(root, "board");
    if (cJSON_IsString(board) && board->valuestring != NULL) {
      strlcpy(s_state.board, board->valuestring, sizeof(s_state.board));
    }
    const cJSON *route_id = cJSON_GetObjectItemCaseSensitive(root, "route_id");
    if (cJSON_IsString(route_id) && route_id->valuestring != NULL) {
      strlcpy(s_state.route_id, route_id->valuestring, sizeof(s_state.route_id));
    }
    const cJSON *display_enabled =
        cJSON_GetObjectItemCaseSensitive(root, "display_enabled");
    if (cJSON_IsBool(display_enabled)) {
      s_state.display_enabled = cJSON_IsTrue(display_enabled);
    }
    emit_identify();
    emit_ack("bind_device", "ok");
    return;
  }

  if (strcmp(command->valuestring, "start_run") == 0) {
    s_state.run_active = true;
    s_state.route_available = true;
    reset_run_metrics();
    emit_node_state("start_run");
    emit_ack("start_run", "ok");
    return;
  }

  if (strcmp(command->valuestring, "stop_run") == 0) {
    s_state.run_active = false;
    emit_metric_snapshot();
    emit_ack("stop_run", "ok");
    return;
  }

  if (strcmp(command->valuestring, "set_active") == 0) {
    const cJSON *active = cJSON_GetObjectItemCaseSensitive(root, "active");
    if (cJSON_IsBool(active)) {
      s_state.node_active = cJSON_IsTrue(active);
      emit_node_state("set_active");
    }
    emit_ack("set_active", "ok");
    return;
  }

  if (strcmp(command->valuestring, "set_link_profile") == 0) {
    const cJSON *active = cJSON_GetObjectItemCaseSensitive(root, "active");
    if (cJSON_IsBool(active)) {
      s_state.link_degraded = cJSON_IsTrue(active);
      emit_node_state("set_link_profile");
    }
    emit_ack("set_link_profile", "ok");
    return;
  }

  if (strcmp(command->valuestring, "set_route_active") == 0) {
    const cJSON *active = cJSON_GetObjectItemCaseSensitive(root, "active");
    if (cJSON_IsBool(active)) {
      s_state.route_available = cJSON_IsTrue(active);
      emit_node_state("set_route_active");
    }
    emit_ack("set_route_active", "ok");
    return;
  }

  if (strcmp(command->valuestring, "display_frame") == 0) {
    emit_ack("display_frame", is_monitor_role() ? "ok" : "ignored");
    return;
  }

  emit_ack(command->valuestring, "unknown_command");
}

static void stdin_task(void *arg) {
  (void)arg;
  char buffer[768];

  while (true) {
    if (fgets(buffer, sizeof(buffer), stdin) == NULL) {
      vTaskDelay(pdMS_TO_TICKS(20));
      continue;
    }

    cJSON *root = cJSON_Parse(buffer);
    if (root == NULL) {
      ESP_LOGW(TAG, "failed to parse command");
      continue;
    }
    handle_command(root);
    cJSON_Delete(root);
  }
}

static void heartbeat_task(void *arg) {
  (void)arg;
  while (true) {
    apply_auto_fault_profile(monotonic_seconds());
    cJSON *root = cJSON_CreateObject();
    cJSON_AddNumberToObject(root, "timestamp_s", monotonic_seconds());
    cJSON_AddStringToObject(root, "event", "heartbeat");
    cJSON_AddStringToObject(root, "node_id", s_state.node_id);
    cJSON_AddStringToObject(root, "arm", s_state.arm);
    cJSON_AddBoolToObject(root, "run_active", s_state.run_active);
    cJSON_AddBoolToObject(root, "active", s_state.node_active);
    cJSON_AddBoolToObject(root, "route_available", s_state.route_available);
    cJSON_AddBoolToObject(root, "link_degraded", s_state.link_degraded);
    emit_json(root);
    vTaskDelay(pdMS_TO_TICKS(1000));
  }
}

static void workload_task(void *arg) {
  (void)arg;
  while (true) {
    const double now = monotonic_seconds();
    apply_auto_fault_profile(now);
    if (!s_state.run_active || !s_state.node_active || is_monitor_role()) {
      vTaskDelay(pdMS_TO_TICKS(50));
      continue;
    }
    if (now + 0.001 < s_state.next_message_at_s) {
      vTaskDelay(pdMS_TO_TICKS(50));
      continue;
    }

    s_state.sequence += 1U;
    const uint32_t sequence = s_state.sequence;
    const bool urgent = sequence_is_urgent(sequence);
    const bool is_command = (sequence % 4U) == 0U;
    const char *kind = is_command ? "command" : "telemetry";
    const int payload_before = s_state.payload_base_bytes + total_optional_bytes() +
                               (urgent ? s_state.urgent_extra_bytes : 0);
    int payload_after = payload_before;
    int optional_fields_dropped = 0;
    float interval_before = s_state.current_interval_s;
    float interval_after = s_state.telemetry_interval_s;
    bool switched_parent = false;

    if (is_maestro_arm()) {
      while (payload_after > s_state.fragmentation_budget_bytes &&
             optional_fields_dropped < (int)s_state.optional_field_count) {
        payload_after -=
            s_state.optional_fields[s_state.optional_field_count - 1U - (size_t)optional_fields_dropped];
        optional_fields_dropped += 1;
      }
      if (s_state.link_degraded || !s_state.route_available) {
        interval_after = s_state.telemetry_interval_s * 1.6f;
      } else if (payload_after < payload_before) {
        interval_after = s_state.telemetry_interval_s * 1.2f;
      }
      if (s_state.link_degraded) {
        switched_parent = true;
        s_state.parent_switches += 1U;
      }
      if (payload_after != payload_before || interval_after != interval_before || switched_parent) {
        emit_policy_decision(sequence, urgent, payload_before, payload_after,
                             optional_fields_dropped, interval_before, interval_after,
                             switched_parent);
      }
      s_state.current_interval_s = interval_after;
    } else {
      s_state.current_interval_s = s_state.telemetry_interval_s;
    }

    const int fragments = fragment_count_for_payload(payload_after);
    int retries = s_state.link_degraded ? 1 : 0;
    if (!s_state.route_available) {
      retries = s_state.max_retries;
    } else if (urgent && retries < s_state.max_retries) {
      retries += 1;
    }
    if (retries > s_state.max_retries) {
      retries = s_state.max_retries;
    }

    const float created_at = (float)now;
    const float rtt = 0.020f + 0.010f * (float)fragments +
                      0.030f * (float)retries +
                      (s_state.link_degraded ? 0.050f : 0.0f);
    const bool delivered = s_state.route_available &&
                           (!s_state.link_degraded || (sequence % 5U) != 0U);
    const float completed_at = created_at + rtt;
    const float energy_cost = (float)fragments * (1.0f + (float)retries * s_state.queue_energy_factor);

    s_state.sent += 1U;
    s_state.retries += (uint32_t)retries;
    s_state.fragments += (uint32_t)fragments;
    if ((uint32_t)(fragments + retries + 1) > s_state.queue_depth_peak) {
      s_state.queue_depth_peak = (uint32_t)(fragments + retries + 1);
    }
    s_state.energy_cost += energy_cost;
    if (delivered) {
      s_state.delivered += 1U;
    } else {
      s_state.dropped += 1U;
      s_state.ack_timeouts += 1U;
    }

    emit_message_result(sequence, kind, urgent, payload_after, fragments, retries,
                        delivered, created_at, completed_at, rtt,
                        s_state.route_available ? "ack_timeout" : "route_unavailable",
                        switched_parent, energy_cost);

    s_state.next_message_at_s = now + s_state.current_interval_s;
    vTaskDelay(pdMS_TO_TICKS(25));
  }
}

void app_main(void) {
  setvbuf(stdin, NULL, _IONBF, 0);
  setvbuf(stdout, NULL, _IONBF, 0);

  ESP_LOGI(TAG, "starting serial benchmark agent");
  emit_identify();

  xTaskCreate(stdin_task, "stdin_task", 4096, NULL, 5, NULL);
  xTaskCreate(heartbeat_task, "heartbeat_task", 4096, NULL, 4, NULL);
  xTaskCreate(workload_task, "workload_task", 6144, NULL, 4, NULL);
  reset_run_metrics();
  s_state.run_active = true;
  emit_node_state("auto_start");
}
