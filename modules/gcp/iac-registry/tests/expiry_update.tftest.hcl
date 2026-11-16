# Two applies in one file: the second changes expires_at and the entry follows.
mock_provider "google" {}

variables {
  repo_slug    = "example-service"
  repo_url     = "https://git.example/example-org/example-service"
  project      = "example-app"
  environment  = "dev"
  state_bucket = "example-project-example-service-tfstate"
  state_prefix = "example-service"
}

override_data {
  target = data.google_storage_bucket.state[0]
  values = {
    labels           = { role = "iac-registry" }
    effective_labels = { role = "iac-registry" }
  }
}

run "expiry_set" {
  command = apply

  variables {
    lifecycle_value = "temporary"
    expires_at      = "20301201t0000z"
  }
}

run "changing_expires_at_updates_the_entry" {
  command = apply

  variables {
    lifecycle_value = "temporary"
    expires_at      = "20310601t0000z"
  }

  assert {
    condition     = jsondecode(google_storage_bucket_object.registration.content).expires_at == "20310601t0000z"
    error_message = "An extended expires_at reaches the entry on the next apply."
  }
}
