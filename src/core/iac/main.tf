# Azure Subscription
data "azurerm_subscription" "current" {}

resource "random_password" "internal_auth_token" {
  length  = 32
  special = false
}

# Orchestrator Function
module "cloudo_orchestrator" {
  source                                   = "git::https://github.com/pagopa/terraform-azurerm-v4//IDH/app_service_function?ref=28d56d27f9e6a58d01f501ef9fe4d1a9b785e2e6" #v9.10.3
  env                                      = var.env
  idh_resource_tier                        = var.cloudo_function_tier
  location                                 = var.location
  name                                     = "${var.prefix}-cloudo-orchestrator"
  product_name                             = var.product_name
  resource_group_name                      = var.resource_group_name
  application_insights_instrumentation_key = data.azurerm_application_insights.this.instrumentation_key

  default_storage_enable     = false
  storage_account_name       = module.storage_account.name
  storage_account_access_key = module.storage_account.primary_access_key
  app_service_plan_name      = "${var.prefix}-cloudo-orchestrator-service-plan"
  export_keys                = true

  app_settings = merge(
    {
      "TABLE_SCHEMA_NAME"                   = azurerm_storage_table.runbook_schemas.name
      "TABLE_LOGGER_NAME"                   = azurerm_storage_table.runbook_logger.name
      "SLACK_TOKEN_DEFAULT"                 = var.slack_integration.token
      "SLACK_CHANNEL_DEFAULT"               = var.slack_integration.channel
      "OPSGENIE_API_KEY_DEFAULT"            = var.opsgenie_api_key
      "GITHUB_REPO"                         = var.github_repo_info.repo_name
      "GITHUB_BRANCH"                       = var.github_repo_info.repo_branch
      "GITHUB_TOKEN"                        = var.orchestrator_image.registry_password
      "GITHUB_PATH_PREFIX"                  = var.github_repo_info.runbook_path
      "WEBSITES_ENABLE_APP_SERVICE_STORAGE" = false
      "APPROVAL_TTL_MIN"                    = var.approval_runbook.ttl_min
      "APPROVAL_SECRET"                     = var.approval_runbook.secret
      "CLOUDO_SECRET_KEY"                   = random_password.internal_auth_token.result
      "NEXTJS_URL"                          = "${var.prefix}-cloudo-ui.azurewebsites.net"
      "FEATURE_DEV"                         = var.env == "dev" ? "true" : "false"
    },
    local.orchestrator_smart_routing_app_settings
  )


  docker_image             = var.orchestrator_image.image_name
  docker_image_tag         = var.orchestrator_image.image_tag
  docker_registry_url      = var.orchestrator_image.registry_url
  docker_registry_password = var.orchestrator_image.registry_password
  docker_registry_username = var.orchestrator_image.registry_username
  tags                     = var.tags

  # which subnet is allowed to reach this app service
  allowed_subnet_ids           = [var.vpn_subnet_id]
  allowed_service_tags         = ["ActionGroup"]
  private_endpoint_dns_zone_id = data.azurerm_private_dns_zone.this.id

  embedded_subnet = {
    enabled      = true
    vnet_name    = var.vnet_name
    vnet_rg_name = var.vnet_rg
  }

  autoscale_settings = {
    max_capacity                  = 1
    scale_up_requests_threshold   = 250
    scale_down_requests_threshold = 150
  }

  user_identity_ids = [azurerm_user_assigned_identity.identity.id]

  always_on = true
}


# UI App Service
module "cloudo_ui" {
  count               = var.enable_ui ? 1 : 0
  source              = "git::https://github.com/pagopa/terraform-azurerm-v4//IDH/app_service_webapp?ref=28d56d27f9e6a58d01f501ef9fe4d1a9b785e2e6" #v9.10.3
  env                 = var.env
  idh_resource_tier   = var.cloudo_ui_tier
  location            = var.location
  name                = "${var.prefix}-cloudo-ui"
  product_name        = var.product_name
  resource_group_name = var.resource_group_name

  app_service_plan_name = module.cloudo_orchestrator.service_plan_name
  app_service_plan_id   = module.cloudo_orchestrator.service_plan_id
  plan_type             = "external"

  app_settings = {
    "ORCHESTRATOR_URL"                    = "https://${module.cloudo_orchestrator.default_hostname}"
    "API_URL"                             = "https://${module.cloudo_orchestrator.default_hostname}/api"
    "FUNCTION_KEY"                        = module.cloudo_orchestrator.default_key
    "CLOUDO_KEY"                          = random_password.internal_auth_token.result
    "GOOGLE_CLIENT_ID"                    = var.cloudo_google_sso_integration_client_id
    "WEBSITES_ENABLE_APP_SERVICE_STORAGE" = false
    "FEATURE_DEV"                         = var.env == "dev" ? "true" : "false"
  }

  docker_image             = var.ui_image.image_name
  docker_image_tag         = var.ui_image.image_tag
  docker_registry_url      = var.ui_image.registry_url
  docker_registry_password = var.ui_image.registry_password
  docker_registry_username = var.ui_image.registry_username
  tags                     = var.tags

  # which subnet is allowed to reach this app service
  allowed_subnet_ids           = [var.vpn_subnet_id]
  private_endpoint_dns_zone_id = data.azurerm_private_dns_zone.this.id

  embedded_subnet = {
    enabled      = true
    vnet_name    = var.vnet_name
    vnet_rg_name = var.vnet_rg
  }

  autoscale_settings = {
    max_capacity                  = 1
    scale_up_requests_threshold   = 250
    scale_down_requests_threshold = 150
  }

  always_on = true
}

# Workers Function Module
module "cloudo_worker" {
  source   = "git::https://github.com/pagopa/terraform-azurerm-v4//IDH/app_service_function?ref=28d56d27f9e6a58d01f501ef9fe4d1a9b785e2e6" #v9.10.3
  for_each = var.workers_config.workers

  env                                      = var.env
  idh_resource_tier                        = var.cloudo_function_tier
  name                                     = "${var.prefix}-cloudo-${each.key}"
  location                                 = var.location
  product_name                             = var.product_name
  resource_group_name                      = var.resource_group_name
  application_insights_instrumentation_key = data.azurerm_application_insights.this.instrumentation_key

  default_storage_enable     = false
  storage_account_name       = module.storage_account.name
  storage_account_access_key = module.storage_account.primary_access_key
  app_service_plan_name      = "${var.prefix}-cloudo-${each.value}-${each.key}-service-plan"

  app_settings = {
    "QUEUE_NAME"                          = azurerm_storage_queue.this[each.key].name
    "TABLE_SCHEMA_NAME"                   = azurerm_storage_table.runbook_schemas.name
    "TABLE_LOGGER_NAME"                   = azurerm_storage_table.runbook_logger.name
    "GITHUB_REPO"                         = var.github_repo_info.repo_name
    "GITHUB_BRANCH"                       = var.github_repo_info.repo_branch
    "GITHUB_TOKEN"                        = var.workers_config.registry_password
    "GITHUB_PATH_PREFIX"                  = var.github_repo_info.runbook_path
    "AZURE_TENANT_ID"                     = azurerm_user_assigned_identity.identity.tenant_id
    "AZURE_CLIENT_ID"                     = azurerm_user_assigned_identity.identity.client_id
    "AZURE_SUBSCRIPTION_ID"               = data.azurerm_subscription.current.subscription_id
    "AzureWebJobsFeatureFlags"            = "EnableWorkerIndexing"
    "FUNCTIONS_WORKER_PROCESS_COUNT"      = 1
    "FUNCTIONS_WORKER_RUNTIME"            = "python"
    "DOTNET_RUNNING_IN_CONTAINER"         = true
    "WEBSITES_ENABLE_APP_SERVICE_STORAGE" = false
    "ORCHESTRATOR_URL"                    = "https://${module.cloudo_orchestrator.default_hostname}/api/workers/register"
    "CLOUDO_SECRET_KEY"                   = random_password.internal_auth_token.result
    "WORKER_CAPABILITY"                   = each.value
    "CLOUDO_ENVIRONMENT"                  = var.env
    "CLOUDO_ENVIRONMENT_SHORT"            = substr(var.env, 0, 1)
    "FEATURE_DEV"                         = var.env == "dev" ? "true" : "false"
  }

  docker_image             = var.workers_config.image_name
  docker_image_tag         = var.workers_config.image_tag
  docker_registry_url      = var.workers_config.registry_url
  docker_registry_password = var.workers_config.registry_password
  docker_registry_username = var.workers_config.registry_username
  tags                     = var.tags

  # which subnet is allowed to reach this app service
  allowed_subnet_ids           = [var.vpn_subnet_id]
  private_endpoint_dns_zone_id = data.azurerm_private_dns_zone.this.id

  embedded_subnet = {
    enabled      = true
    vnet_name    = var.vnet_name
    vnet_rg_name = var.vnet_rg
  }

  autoscale_settings = {
    max_capacity                  = var.autoscale_max_capacity
    scale_up_requests_threshold   = 250
    scale_down_requests_threshold = 150
  }

  user_identity_ids = [azurerm_user_assigned_identity.identity.id]

  always_on = true
}

module "storage_account" {
  source = "git::https://github.com/pagopa/terraform-azurerm-v4.git//storage_account?ref=77e0c671b8f4c11c6568e4b0cc87e30332b62090" #v8.5.1

  name                          = replace("${var.prefix}cloudosa", "-", "")
  location                      = var.location
  resource_group_name           = var.resource_group_name
  account_tier                  = "Standard"
  account_replication_type      = "LRS"
  is_hns_enabled                = false
  public_network_access_enabled = true

  tags = var.tags
}

resource "azurerm_storage_queue" "this" {
  for_each             = var.workers_config.workers
  name                 = "${var.prefix}-${each.key}-queue"
  storage_account_name = module.storage_account.name
}

resource "azurerm_storage_queue" "notification" {
  name                 = "cloudo-notification"
  storage_account_name = module.storage_account.name
}

resource "azurerm_storage_table" "runbook_logger" {
  name                 = "RunbookLogs"
  storage_account_name = module.storage_account.name
}

resource "azurerm_storage_table" "runbook_schemas" {
  name                 = "RunbookSchemas"
  storage_account_name = module.storage_account.name
}

resource "azurerm_storage_table" "workers_registry" {
  name                 = "WorkersRegistry"
  storage_account_name = module.storage_account.name
}

resource "azurerm_storage_table" "audit_logs" {
  name                 = "CloudoAuditLogs"
  storage_account_name = module.storage_account.name
}

resource "azurerm_storage_table" "cloudo_schedules" {
  name                 = "CloudoSchedules"
  storage_account_name = module.storage_account.name
}

resource "azurerm_storage_table" "cloudo_settings" {
  name                 = "CloudoSettings"
  storage_account_name = module.storage_account.name
}

resource "azurerm_storage_table" "cloudo_users" {
  name                 = "CloudoUsers"
  storage_account_name = module.storage_account.name
}

resource "azurerm_storage_table_entity" "admin_user" {
  storage_table_id = azurerm_storage_table.cloudo_users.id
  partition_key    = "Operator"
  row_key          = "admin"
  entity           = { password = random_password.admin_password.result, role = "ADMIN", email = "admin@cloudo.local" }
}

resource "azurerm_storage_table_entity" "schemas" {
  for_each = {
    for i in local.entity_executor : i.entity.id => i
  }

  storage_table_id = azurerm_storage_table.runbook_schemas.id

  partition_key = each.value.partition_key
  row_key       = random_uuid.uuid[each.key].result

  entity = merge(
    each.value.entity,
    {
      tags = lookup(each.value.entity, "tags", null) == null ? "terraform" : contains(split(",", each.value.entity.tags), "terraform") ? each.value.entity.tags : "${each.value.entity.tags},terraform"
    }
  )
}
