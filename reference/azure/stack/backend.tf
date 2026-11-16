# The storage account and its resource group are the outputs of ../bootstrap
# and are passed at init time:
#
#   terraform init \
#     -backend-config="resource_group_name=<state_resource_group>" \
#     -backend-config="storage_account_name=<state_storage_account>"
#
# In CI, ARM_USE_OIDC=true selects workload identity federation.

terraform {
  backend "azurerm" {
    container_name   = "tfstate"
    key              = "stack.tfstate"
    use_azuread_auth = true
  }
}
