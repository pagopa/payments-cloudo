
# App Insights
data "azurerm_application_insights" "this" {
  name                = var.application_insights_name
  resource_group_name = var.application_insights_rg
}

# Function APP Keys
# data "azurerm_function_app_host_keys" "orchestrator" {
#   name                = module.cloudo_orchestrator.name
#   resource_group_name = module.cloudo_orchestrator.resource_group_name
#
#   depends_on = [module.cloudo_orchestrator]
# }

# Random UUID for RowKey
resource "random_uuid" "uuid" {
  for_each = {
    for i in local.entity_executor : i.entity.id => i
  }
}

# Random admin password
resource "random_password" "admin_password" {
  length           = 16
  special          = true
  override_special = "!#$%&*()-_=+[]{}<>:?"
}

# Private DNS Zone
data "azurerm_private_dns_zone" "this" {
  name = var.private_endpoint_dns_zone_name
}

data "azurerm_key_vault" "key_vaults" {
  for_each = {
    for vault in var.key_vaults_integration : vault => vault
  }
  name                = each.value.name
  resource_group_name = each.value.resource_group
}
