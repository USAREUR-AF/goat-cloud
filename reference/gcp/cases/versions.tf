terraform {
  required_version = ">= 1.7.0"

  # Rule 1. The owner's state bucket is given at init:
  # -backend-config=bucket=<state_bucket>. No CI identity holds a role on it.
  backend "gcs" {
    prefix = "cases"
  }

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0"
    }
  }
}

provider "google" {
  project = var.project_id
}
