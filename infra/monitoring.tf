# Copyright 2026 Joey Parrish
# SPDX-License-Identifier: GPL-3.0-or-later

resource "google_monitoring_notification_channel" "admin" {
  display_name = "calendar-sync admin"
  type         = "email"
  labels = {
    email_address = var.admin_email
  }
  depends_on = [google_project_service.apis]
}

locals {
  alerts = {
    errors = {
      name   = "calendar-sync: error logged"
      filter = "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=~\"^calendar-sync-\" AND severity>=ERROR"
    }
    scheduler = {
      name   = "calendar-sync: scheduler could not reach the worker"
      filter = "resource.type=\"cloud_scheduler_job\" AND resource.labels.job_id=\"calendar-sync\" AND severity>=ERROR"
    }
    needs_login = {
      name   = "calendar-sync: a user needs to sign in again"
      filter = "resource.type=\"cloud_run_revision\" AND jsonPayload.event=\"needs_login\""
    }
  }
}

resource "google_monitoring_alert_policy" "alerts" {
  for_each     = local.alerts
  display_name = each.value.name
  combiner     = "OR"
  conditions {
    display_name = each.value.name
    condition_matched_log {
      filter = each.value.filter
      label_extractors = {
        account = "EXTRACT(jsonPayload.account)"
      }
    }
  }
  alert_strategy {
    notification_rate_limit {
      period = "3600s"
    }
    auto_close = "86400s"
  }
  documentation {
    content   = "See Logs Explorer for the matching entries. The `account` label names the user, if any."
    mime_type = "text/markdown"
  }
  notification_channels = [google_monitoring_notification_channel.admin.id]
}
