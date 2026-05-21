output "cloudo_orchestrator_url" {
  value       = "https://${module.cloudo_orchestrator.default_hostname}"
  description = "The URL of the Cloudo Orchestrator"
}

output "cloudo_orchestrator_key" {
  value       = module.cloudo_orchestrator.default_key
  description = "The default key for the Cloudo Orchestrator"
  sensitive   = true
}

output "cloudo_ui_url" {
  value       = length(module.cloudo_ui) > 0 ? "https://${module.cloudo_ui[0].default_site_hostname}" : null
  description = "The URL of the Cloudo UI"
}

output "cloudo_workers_hostnames" {
  value       = { for k, v in module.cloudo_worker : k => v.default_hostname }
  description = "The hostnames of the Cloudo Workers"
}

output "storage_account_name" {
  value       = module.storage_account.name
  description = "The name of the storage account"
}

output "storage_account_primary_access_key" {
  value       = module.storage_account.primary_access_key
  description = "The primary access key of the storage account"
  sensitive   = true
}

output "storage_queues" {
  value = {
    workers      = { for k, v in azurerm_storage_queue.this : k => v.name }
    notification = azurerm_storage_queue.notification.name
  }
  description = "The names of the storage queues"
}

output "storage_tables" {
  value = {
    runbook_logger   = azurerm_storage_table.runbook_logger.name
    runbook_schemas  = azurerm_storage_table.runbook_schemas.name
    workers_registry = azurerm_storage_table.workers_registry.name
    audit_logs       = azurerm_storage_table.audit_logs.name
    cloudo_schedules = azurerm_storage_table.cloudo_schedules.name
    cloudo_settings  = azurerm_storage_table.cloudo_settings.name
    cloudo_users     = azurerm_storage_table.cloudo_users.name
  }
  description = "The names of the storage tables"
}

output "cloudo_internal_auth_token" {
  value       = random_password.internal_auth_token.result
  description = "The internal authentication token for Cloudo services"
  sensitive   = true
}

output "cloudo_action_group_id" {
  value       = azurerm_monitor_action_group.cloudo_trigger.id
  description = "The id of ClouDO action group to trigger alarms."
}

output "cloudo_action_group_name" {
  value       = azurerm_monitor_action_group.cloudo_trigger.name
  description = "The name of ClouDO action group to trigger alarms."
}

# API Management Outputs
output "cloudo_api_name" {
  value       = module.apim_api_cloudo_api_v1.name
  description = "The name of the Cloudo API in API Management"
}

output "cloudo_api_id" {
  value       = module.apim_api_cloudo_api_v1.id
  description = "The ID of the Cloudo API in API Management"
}

output "cloudo_api_url" {
  value       = "https://${var.api_manager_hostname}/${trim(var.api_path, "/")}/Trigger"
  description = "The public URL of the Cloudo Trigger endpoint"
}
