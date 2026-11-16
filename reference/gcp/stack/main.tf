# The stack's own resources: two example resources that cost nothing while
# empty. Their API, pubsub.googleapis.com, is enabled by ../platform (rule 7),
# so the deploy identity holds no Service Usage role.

resource "random_id" "bucket" {
  byte_length = 2
}

resource "google_pubsub_topic" "example" {
  project = var.project_id
  name    = "${var.slug}-example"
}

# The name starts with <slug>_, the prefix ../platform lets the deploy
# identity manage.
resource "google_storage_bucket" "example" {
  name     = "${var.slug}_example-${random_id.bucket.hex}"
  project  = var.project_id
  location = var.location

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  # The example bucket is temporary: destroy removes it with its objects and
  # keeps no soft-deleted copy.
  force_destroy = true

  soft_delete_policy {
    retention_duration_seconds = 0
  }
}
