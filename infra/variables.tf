# Copyright 2026 Joey Parrish
# SPDX-License-Identifier: GPL-3.0-or-later

variable "project_id" {
  type = string
}

variable "region" {
  type    = string
  default = "us-west1"
}

variable "allowed_domain" {
  description = "Google Workspace domain whose members may sign in."
  type        = string
}

variable "admin_email" {
  description = "Receives alert emails."
  type        = string
}

variable "custom_domain" {
  description = "Optional hostname for the web UI, e.g. calsync.example.com. Empty uses the run.app address."
  type        = string
  default     = ""
}

variable "image" {
  description = "Full image reference, e.g. us-west1-docker.pkg.dev/PROJECT/calendar-sync/app:TAG"
  type        = string
}
