# Identity
resource "azurerm_user_assigned_identity" "identity" {
  location            = var.location
  name                = "${var.prefix}-cloudo-identity"
  resource_group_name = var.resource_group_name
}

resource "azurerm_role_assignment" "role_assignment" {
  for_each = merge(
    {
      for aks_key, _ in var.aks_integration : "${aks_key}:AKSClusterUser" =>
      { role = "Azure Kubernetes Service Cluster User Role", key = aks_key, kind = "aks" }
    },
    {
      for aks_key, _ in var.aks_integration : "${aks_key}:AKSServiceAccount" =>
      { role = "Azure Kubernetes Service RBAC Admin", key = aks_key, kind = "aks" }
    },
    {
      "subscription:Reader" = {
        role = "Reader"
        key  = null
        kind = "subscription"
      }
    },
    // Custom per-AKS (backward compatibility)
    merge([
      for aks_key, roles in var.custom_roles_per_aks : {
        for role_name in roles :
        "${aks_key}:custom:${role_name}" => { role = role_name, key = aks_key, kind = "aks" }
      }
    ]...),
    // Custom at subscription level (backward compatibility)
    {
      for role_name in var.custom_roles_subscription :
      "subscription:custom:${role_name}" => { role = role_name, key = null, kind = "subscription" }
    },
    // Generic custom assignments for any scope/resource
    {
      for i, ra in var.custom_role_assignments :
      "custom:${i}" => {
        role         = ra.role
        scope_custom = ra.scope
        principal    = try(ra.principal_id, null)
        kind         = "custom"
        key          = null
      }
    }
  )

  scope = each.value.kind == "aks" ? var.aks_integration[each.value.key].cluster_id : (each.value.kind == "subscription" ? "/subscriptions/${var.subscription_id}" : each.value.scope_custom)

  // Use role_definition_name when a role name is provided; otherwise use role_definition_id when a full role definition ID is provided
  role_definition_name = can(regex("^/subscriptions/", each.value.role)) ? null : each.value.role
  role_definition_id   = can(regex("^/subscriptions/", each.value.role)) ? each.value.role : null

  // Allow overriding the principal; fallback to the module-managed identity
  principal_id = coalesce(
    try(each.value.principal, null),
    azurerm_user_assigned_identity.identity.principal_id
  )
}

resource "azurerm_key_vault_access_policy" "key_vault_reader" {
  for_each = {
    for vault in data.azurerm_key_vault.key_vaults : vault.name => vault
  }
  key_vault_id = each.value.id
  object_id    = azurerm_user_assigned_identity.identity.principal_id
  tenant_id    = azurerm_user_assigned_identity.identity.tenant_id

  secret_permissions = [
    "Get",
    "List"
  ]
}
