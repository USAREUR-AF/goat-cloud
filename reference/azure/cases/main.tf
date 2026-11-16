# Verification cases: one empty resource group per case. On Azure the resource
# group is the unit a scan grades, and an empty resource group is free. Each
# case breaks exactly one rule of the contract; every other tag is valid.
#
# ../truth.yaml states the result a scan must report for each.
#
# This root is a test fixture: it keeps local state and exists for minutes.

locals {
  conforming = {
    Repository  = var.repository_url
    Project     = "reference"
    Environment = "dev"
    Lifecycle   = "persistent"
    ManagedBy   = "terraform"
  }

  cases = {
    "case-1-conforming" = local.conforming

    "case-2-missing-environment" = {
      for key, value in local.conforming : key => value if key != "Environment"
    }

    "case-3-expired" = merge(local.conforming, {
      Lifecycle = "temporary"
      ExpiresAt = "2020-01-01T00:00:00Z"
    })

    "case-4-expiry-unreadable" = merge(local.conforming, {
      Lifecycle = "temporary"
      ExpiresAt = "next-week"
    })

    "case-5-invalid-managed-by" = merge(local.conforming, {
      ManagedBy = "chef"
    })

    "case-6-protected" = merge(local.conforming, {
      Protected = "true"
    })

    # Case 7 holds one resource of a type that cannot carry tags: a budget.
    "case-7-untaggable-member" = local.conforming

    "case-8-temporary-no-expiry" = merge(local.conforming, {
      Lifecycle = "temporary"
    })

    "case-9-experiment-no-expiry" = merge(local.conforming, {
      Lifecycle = "experiment"
    })
  }
}

resource "azurerm_resource_group" "case" {
  for_each = local.cases

  name     = "${var.prefix}-${each.key}"
  location = var.location
  tags     = each.value
}

# Case 7: Microsoft.Consumption/budgets has no tags property.
resource "azurerm_consumption_budget_resource_group" "untaggable" {
  name              = "${var.prefix}-case-7-budget"
  resource_group_id = azurerm_resource_group.case["case-7-untaggable-member"].id
  amount            = 1
  time_grain        = "Monthly"

  time_period {
    start_date = "${var.budget_start_month}-01T00:00:00Z"
  }

  notification {
    threshold      = 100
    threshold_type = "Actual"
    operator       = "GreaterThanOrEqualTo"
    contact_roles  = ["Owner"]
  }

  lifecycle {
    ignore_changes = [time_period]
  }
}
