# Plan-only tests against mocked providers: no credentials, no API calls.
mock_provider "google" {}
mock_provider "random" {}

variables {
  project_id = "example-project"
  location   = "us-central1"
  slug       = "example-service"
  owner      = "payments-team"
  workload   = "order-events"
}

override_resource {
  target = random_id.suffix
  values = {
    hex = "1a2b"
  }
}

run "owner_and_stack_state_are_separate_buckets" {
  command = apply

  assert {
    condition     = output.state_bucket == "example-service-tfstate-1a2b" && output.stack_state_bucket == "example-service-stack-tfstate-1a2b"
    error_message = "The owner's state and the stack's state are two buckets."
  }

  assert {
    condition     = google_storage_bucket.state["stack_state"].labels["role"] == "iac-registry" && !contains(keys(google_storage_bucket.state["state"].labels), "role")
    error_message = "Only the stack's state bucket, which holds the registry entry, carries role=iac-registry."
  }

  assert {
    condition     = alltrue([for b in google_storage_bucket.state : b.versioning[0].enabled && b.uniform_bucket_level_access && b.public_access_prevention == "enforced"])
    error_message = "Both buckets are versioned, uniform-access and closed to the public."
  }

  assert {
    condition     = !startswith(output.state_bucket, "example-service_") && !startswith(output.stack_state_bucket, "example-service_")
    error_message = "No state bucket starts with the stack's bucket prefix <slug>_."
  }
}
