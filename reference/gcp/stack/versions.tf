terraform {
  required_version = ">= 1.7.0"

  # Rule 1. The stack's own state bucket is given at init:
  # -backend-config=bucket=<stack_state_bucket>
  backend "gcs" {
    prefix = "stack"
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
