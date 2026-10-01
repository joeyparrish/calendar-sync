# Copyright 2026 Joey Parrish
# SPDX-License-Identifier: GPL-3.0-or-later

data "google_project" "this" {}

locals {
  apis = [
    "run.googleapis.com",
    "cloudscheduler.googleapis.com",
    "firestore.googleapis.com",
    "cloudkms.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "monitoring.googleapis.com",
    "logging.googleapis.com",
    "calendar-json.googleapis.com",
    "iam.googleapis.com",
  ]
  # Cloud Run URLs are predictable, which lets the OAuth client be created before deploying.
  web_url    = "https://calendar-sync-web-${data.google_project.this.number}.${var.region}.run.app"
  worker_url = "https://calendar-sync-worker-${data.google_project.this.number}.${var.region}.run.app"
  # Where users sign in. The app redirects every other hostname here.
  public_url = var.custom_domain == "" ? local.web_url : "https://${var.custom_domain}"
}

resource "google_project_service" "apis" {
  for_each           = toset(local.apis)
  service            = each.value
  disable_on_destroy = false
}

resource "google_firestore_database" "default" {
  name                    = "(default)"
  location_id             = var.region
  type                    = "FIRESTORE_NATIVE"
  delete_protection_state = "DELETE_PROTECTION_ENABLED"
  deletion_policy         = "ABANDON"
  depends_on              = [google_project_service.apis]
}

resource "google_kms_key_ring" "this" {
  name       = "calendar-sync"
  location   = var.region
  depends_on = [google_project_service.apis]
}

resource "google_kms_crypto_key" "credentials" {
  name     = "credentials"
  key_ring = google_kms_key_ring.this.id
  lifecycle {
    prevent_destroy = true
  }
}

resource "google_secret_manager_secret" "oauth_client" {
  secret_id = "oauth-client"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret" "session_key" {
  secret_id = "session-key"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_artifact_registry_repository" "images" {
  repository_id = "calendar-sync"
  location      = var.region
  format        = "DOCKER"
  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 3
    }
  }
  cleanup_policies {
    id     = "delete-old"
    action = "DELETE"
    condition {
      older_than = "604800s"
    }
  }
  depends_on = [google_project_service.apis]
}

resource "google_service_account" "web" {
  account_id   = "calendar-sync-web"
  display_name = "calendar-sync web"
}

resource "google_service_account" "worker" {
  account_id   = "calendar-sync-worker"
  display_name = "calendar-sync worker"
}

resource "google_service_account" "scheduler" {
  account_id   = "calendar-sync-scheduler"
  display_name = "calendar-sync scheduler (invokes the worker)"
}

locals {
  runtime_accounts = {
    web    = google_service_account.web.member
    worker = google_service_account.worker.member
  }
}

resource "google_project_iam_member" "firestore" {
  for_each = local.runtime_accounts
  project  = var.project_id
  role     = "roles/datastore.user"
  member   = each.value
}

resource "google_kms_crypto_key_iam_member" "credentials" {
  for_each      = local.runtime_accounts
  crypto_key_id = google_kms_crypto_key.credentials.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = each.value
}

resource "google_secret_manager_secret_iam_member" "oauth_client" {
  for_each  = local.runtime_accounts
  secret_id = google_secret_manager_secret.oauth_client.id
  role      = "roles/secretmanager.secretAccessor"
  member    = each.value
}

resource "google_secret_manager_secret_iam_member" "session_key" {
  secret_id = google_secret_manager_secret.session_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.web.member
}

locals {
  common_env = {
    GOOGLE_CLOUD_PROJECT = var.project_id
    KMS_KEY              = google_kms_crypto_key.credentials.id
    ALLOWED_DOMAIN       = var.allowed_domain
    PUBLIC_URL           = local.public_url
    OAUTH_CLIENT_FILE    = "/secrets/oauth/client.json"
  }
}

resource "google_cloud_run_v2_service" "web" {
  name                 = "calendar-sync-web"
  location             = var.region
  deletion_protection  = false
  ingress              = "INGRESS_TRAFFIC_ALL"
  invoker_iam_disabled = true # public sign-in page; works under domain-restricted sharing

  template {
    service_account = google_service_account.web.email
    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }
    containers {
      image = var.image
      env {
        name  = "ROLE"
        value = "web"
      }
      dynamic "env" {
        for_each = local.common_env
        content {
          name  = env.key
          value = env.value
        }
      }
      env {
        name = "SESSION_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.session_key.secret_id
            version = "latest"
          }
        }
      }
      volume_mounts {
        name       = "oauth"
        mount_path = "/secrets/oauth"
      }
      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        cpu_idle = true
      }
    }
    volumes {
      name = "oauth"
      secret {
        secret = google_secret_manager_secret.oauth_client.secret_id
        items {
          path    = "client.json"
          version = "latest"
        }
      }
    }
  }
  depends_on = [
    google_project_service.apis,
    google_secret_manager_secret_iam_member.oauth_client,
    google_secret_manager_secret_iam_member.session_key,
  ]
}

# Optional custom hostname (Cloud Run domain mapping, a pre-GA feature). Point a DNS-only
# CNAME at the records in the dns_records output; Google then issues the certificate.
resource "google_cloud_run_domain_mapping" "web" {
  count    = var.custom_domain == "" ? 0 : 1
  name     = var.custom_domain
  location = var.region
  metadata {
    namespace = var.project_id
  }
  spec {
    # By name, not by reference, so the mapping can be created alone first (tofu apply
    # -target) and its certificate issued before the public URL switches to it.
    route_name = "calendar-sync-web"
  }
}

resource "google_cloud_run_v2_service" "worker" {
  name                = "calendar-sync-worker"
  location            = var.region
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.worker.email
    timeout         = "300s"
    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }
    containers {
      image = var.image
      env {
        name  = "ROLE"
        value = "worker"
      }
      dynamic "env" {
        for_each = local.common_env
        content {
          name  = env.key
          value = env.value
        }
      }
      volume_mounts {
        name       = "oauth"
        mount_path = "/secrets/oauth"
      }
      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        cpu_idle = true
      }
    }
    volumes {
      name = "oauth"
      secret {
        secret = google_secret_manager_secret.oauth_client.secret_id
        items {
          path    = "client.json"
          version = "latest"
        }
      }
    }
  }
  depends_on = [
    google_project_service.apis,
    google_secret_manager_secret_iam_member.oauth_client,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "scheduler_invokes_worker" {
  name     = google_cloud_run_v2_service.worker.name
  location = var.region
  role     = "roles/run.invoker"
  member   = google_service_account.scheduler.member
}

resource "google_cloud_scheduler_job" "sync" {
  name             = "calendar-sync"
  region           = var.region
  schedule         = "*/10 * * * *"
  time_zone        = "Etc/UTC"
  attempt_deadline = "300s"
  # No retries: the API's default, so it is not set here (the API omits it, which would
  # otherwise show as a change on every plan). The next run is 10 minutes away anyway.
  http_target {
    http_method = "POST"
    uri         = "${local.worker_url}/sync"
    oidc_token {
      service_account_email = google_service_account.scheduler.email
      audience              = local.worker_url
    }
  }
  depends_on = [google_cloud_run_v2_service_iam_member.scheduler_invokes_worker]
}
