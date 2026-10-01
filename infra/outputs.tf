# Copyright 2026 Joey Parrish
# SPDX-License-Identifier: GPL-3.0-or-later

output "web_url" {
  value = local.web_url
}

output "worker_url" {
  value = local.worker_url
}

output "public_url" {
  value = local.public_url
}

output "oauth_redirect_uri" {
  value = "${local.public_url}/oauth/callback"
}

output "dns_records" {
  description = "DNS records to create for custom_domain, if set."
  value       = try(google_cloud_run_domain_mapping.web[0].status[0].resource_records, [])
}

output "image_repository" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "kms_key" {
  value = google_kms_crypto_key.credentials.id
}
