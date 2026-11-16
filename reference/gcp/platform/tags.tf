# Rule 3 in project mode: tag keys under the project itself.
# In organization mode the keys live under the organization and this file is
# not used.

locals {
  tags_on = var.tag_mode == "project-keys"

  tag_values = {
    lifecycle   = ["persistent", "temporary", "experiment", "deprecated"]
    environment = ["dev", "staging", "production"]
    protected   = ["true"]
  }

  tag_value_pairs = local.tags_on ? merge([
    for key, values in local.tag_values : { for value in values : "${key}/${value}" => { key = key, value = value } }
  ]...) : {}
}

resource "google_tags_tag_key" "fact" {
  for_each = local.tags_on ? local.tag_values : {}

  parent      = "projects/${var.project_id}"
  short_name  = each.key
  description = "Contract fact ${each.key}"
}

resource "google_tags_tag_value" "fact" {
  for_each = local.tag_value_pairs

  parent     = google_tags_tag_key.fact[each.value.key].id
  short_name = each.value.value
}

resource "google_tags_tag_binding" "project" {
  for_each = local.tags_on && var.bind_project_tags ? {
    lifecycle   = "lifecycle/${var.project_lifecycle}"
    environment = "environment/${var.project_environment}"
  } : {}

  parent    = "//cloudresourcemanager.googleapis.com/projects/${data.google_project.this.number}"
  tag_value = google_tags_tag_value.fact[each.value].id
}
