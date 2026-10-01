# Copyright 2026 Joey Parrish
# SPDX-License-Identifier: GPL-3.0-or-later

terraform {
  required_version = ">= 1.8"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0"
    }
  }
  # Bucket is passed at init: tofu init -backend-config=bucket=<state bucket>
  backend "gcs" {
    prefix = "calendar-sync"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
