# iac-registry
# Writes one registry entry per repository as the blob iac-registry/<repo_slug>.json,
# in the container that holds the repository's Terraform state.
#
# A scanner finds every state storage account by the tag role=iac-registry,
# lists the blobs under iac-registry/ and reads each state named there. The
# entry holds no timestamp, so an apply that changes no input changes no blob.

data "azurerm_storage_account" "state" {
  name                = var.storage_account_name
  resource_group_name = var.resource_group_name

  lifecycle {
    postcondition {
      # Azure tag names are case-insensitive; the value is compared exactly.
      condition     = contains([for k, v in self.tags : "${lower(k)}=${v}"], "role=iac-registry")
      error_message = "The state storage account must carry the tag role=iac-registry, or no scanner finds this entry."
    }
  }
}

locals {
  # owner, contact and expires_at are part of the entry only when set; lifecycle
  # always is.
  entry = merge(
    {
      repo_url = var.repo_url
      iac_type = lower(var.iac_type)
      state_location = {
        backend         = "azurerm"
        storage_account = var.storage_account_name
        container       = var.container_name
        key             = var.state_key
      }
      environment = var.environment
      tags        = var.tags
      version     = "1.0"
    },
    var.owner != null ? { owner = var.owner } : {},
    var.contact != null ? { contact = var.contact } : {},
    { lifecycle = var.lifecycle_value },
    var.expires_at != null ? { expires_at = var.expires_at } : {},
  )

  container_id = "${data.azurerm_storage_account.state.id}/blobServices/default/containers/${var.container_name}"
}

resource "azurerm_storage_blob" "entry" {
  name                 = "iac-registry/${var.repo_slug}.json"
  storage_container_id = local.container_id
  type                 = "Block"
  content_type         = "application/json"
  source_content       = jsonencode(local.entry)

  lifecycle {
    precondition {
      condition     = contains(local.registry_lifecycle_values, var.lifecycle_value)
      error_message = "lifecycle_value must be one of: ${join(", ", local.registry_lifecycle_values)}."
    }

    precondition {
      condition     = var.expires_at == null || can(regex(local.registry_expires_at_pattern, coalesce(var.expires_at, "-")))
      error_message = "expires_at must match ${local.registry_expires_at_pattern}."
    }

    precondition {
      condition     = !contains(local.registry_expiry_lifecycles, var.lifecycle_value) || var.expires_at != null
      error_message = "lifecycle_value ${var.lifecycle_value} requires expires_at (rule ${var.lifecycle_value}-requires-expires-at)."
    }
  }
}
