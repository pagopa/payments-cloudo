resource "azurerm_monitor_metric_alert" "function_failures" {
  for_each = merge(
    { (module.cloudo_orchestrator.name) = module.cloudo_orchestrator.id },
    { for a in module.cloudo_agent : a.name => a.id },
    { for w in module.cloudo_worker : w.name => w.id }
  )

  name                = "${var.prefix}-${var.env}-cloudo-func-fail-${each.key}"
  resource_group_name = var.resource_group_name
  scopes              = [each.value]
  description         = "Alert when function execution failures occur"
  severity            = 2

  criteria {
    metric_namespace = "Microsoft.Web/sites"
    metric_name      = "Http5xx"
    aggregation      = "Total"
    operator         = "GreaterThan"
    threshold        = 0
  }
}

resource "azurerm_monitor_metric_alert" "function_duration" {
  for_each = merge(
    { (module.cloudo_orchestrator.name) = module.cloudo_orchestrator.id },
    { for a in module.cloudo_agent : a.name => a.id },
    { for w in module.cloudo_worker : w.name => w.id }
  )

  name                = "${var.prefix}-${var.env}-cloudo-func-duration-${each.key}"
  resource_group_name = var.resource_group_name
  scopes              = [each.value]
  description         = "Alert when function execution duration exceeds threshold"
  severity            = 2

  criteria {
    metric_namespace       = "Microsoft.Web/sites"
    metric_name            = "AverageResponseTime"
    aggregation            = "Average"
    operator               = "GreaterThan"
    threshold              = 1000
    skip_metric_validation = true
  }
}

resource "azurerm_monitor_metric_alert" "queue_message_count" {
  name                = "${var.prefix}-${var.env}-cloudo-queue-msg-count"
  resource_group_name = var.resource_group_name

  scopes = ["${module.storage_account.id}/queueServices/default"]

  description = "Alert when queue message count exceeds threshold"
  severity    = 2

  window_size = "PT1H"
  frequency   = "PT1H"

  criteria {
    metric_namespace       = "Microsoft.Storage/storageAccounts/queueServices"
    metric_name            = "QueueMessageCount"
    aggregation            = "Average"
    operator               = "GreaterThan"
    threshold              = 500
    skip_metric_validation = true
  }
}
