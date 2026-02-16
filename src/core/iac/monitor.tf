resource "azurerm_monitor_action_group" "cloudo_trigger" {
  name                = "${var.prefix}-cloudo-trigger"
  resource_group_name = var.resource_group_name
  short_name          = "cloudo-trig"

  webhook_receiver {
    name                    = "cloudo-trigger"
    service_uri             = "https://${module.cloudo_orchestrator.default_hostname}/api/Trigger?code=${module.cloudo_orchestrator.default_key}&x-cloudo-key=${random_password.internal_auth_token.result}"
    use_common_alert_schema = true
  }

  enabled = true

  tags = var.tags
}
