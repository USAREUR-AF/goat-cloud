resource "azurerm_consumption_budget_resource_group" "app" {
  count = var.budget_enabled ? 1 : 0

  name              = "${azurerm_resource_group.app.name}-budget"
  resource_group_id = azurerm_resource_group.app.id
  amount            = var.budget_amount
  time_grain        = "Monthly"

  time_period {
    start_date = "${var.budget_start_month}-01T00:00:00Z"
  }

  notification {
    threshold      = 80
    threshold_type = "Actual"
    operator       = "GreaterThanOrEqualTo"
    contact_emails = var.budget_contact_emails
    contact_roles  = var.budget_contact_roles
  }

  notification {
    threshold      = 100
    threshold_type = "Forecasted"
    operator       = "GreaterThanOrEqualTo"
    contact_emails = var.budget_contact_emails
    contact_roles  = var.budget_contact_roles
  }

  lifecycle {
    ignore_changes = [time_period]
  }
}
