terraform {
  required_version = ">= 1.7.0"

  # Rule 1. The owner's state bucket is given at init:
  # -backend-config=bucket=<state_bucket>. No CI identity holds a role on it.
  backend "gcs" {
    prefix = "platform"
  }

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.6"
    }
  }
}

provider "google" {
  project = var.project_id
}
