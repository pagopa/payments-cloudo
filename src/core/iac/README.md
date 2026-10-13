# ClouDO IAC module

## Installation example

```hcl
module "cloudo" {
  source = "git::https://github.com/pagopa/payments-ClouDO.git//src/core/iac?ref=55a105d25eff99be8b5352db72074c4a2e56cb4a"

  prefix                    = local.product
  env                       = var.env
  location                  = var.location
  resource_group_name       = azurerm_resource_group.rg.name
  service_plan_sku          = "B1"
  application_insights_name = data.azurerm_application_insights.app_insight.name
  application_insights_rg   = data.azurerm_application_insights.app_insight.resource_group_name

  # Runbooks refer repository
  github_repo_info = {
    repo_name    = "organizzation/your_repo"
    repo_branch  = "main"
    repo_token   = "<TOKEN_IF_PRIVATE>"
    runbook_path = "your repo path where is the runbook collection. (e.g. src/runbook)"
  }

  # Multi-AKS cluster integration for RBAC service account profile.
  aks_integration = {
    weu = {
      cluster_id     = data.azurerm_kubernetes_cluster.aks_weu.id
    },
    itn = {
      cluster_id     = data.azurerm_kubernetes_cluster.aks_itn.id
    }
  }

  custom_roles_per_aks = {
    "weu" = [
      "Monitoring Reader",
      "Azure Kubernetes Service Cluster User Role"
    ]
    "itn" = [
      "Reader"
    ]
  }

  custom_roles_subscription = [
    "Reader",
    "Storage Blob Data Reader"
  ]

  custom_role_assignments = [
    {
      // Example: Assign by role name to a resource group
      role  = "Contributor"
      scope = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-app"
    },
    {
      // Example: Assign by role name to a specific resource
      role  = "Storage Blob Data Reader"
      scope = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-app/providers/Microsoft.Storage/storageAccounts/mystg"
    },
    {
      // Example: Assign by role_definition_id (built-in or custom role)
      role  = "/subscriptions/00000000-0000-0000-0000-000000000000/providers/Microsoft.Authorization/roleDefinitions/ba92f5b4-2d11-453d-a403-e96b0029c9fe"
      scope = "/subscriptions/00000000-0000-0000-0000-000000000000"
    },
    {
      // Example: Override principal_id
      role         = "Reader"
      scope        = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-ops"
      principal_id = "11111111-2222-3333-4444-555555555555"
    }
  ]

  app_service_logs = {
    retention_period_days = 3
    disk_quota_mb         = 35
  }

  # (Optional) EXAMPLE Team integration and key routing
  # This is required if UI is not installed.
  # ==========================================
  slack_integration = {
    channel = "#your_slack_channel"
    token   = "APP_TOKEN"
  }
  team_slack_channels = {
    "team-1" = "#cloudo-team1"
    "team-2" = "#cloudo-team2"
    "team-3" = "#cloudo-team3"
  }
  team_slack_tokens = {
    "team-1" = "<token-team1-here>"
    "team-2" = "<token-team2-here>"
    "team-3" = "<token-team3-here>"
  }
  jsm_api_key = "API_KEY_JSM"
  team_jsm_api_keys = {
    "team-1" = "<api-key-team1-here>"
    "team-2" = "<api-key-team2-here>"
    "team-3" = "<api-key-team3-here>"
  }
  # ==========================================

  # Set "aks_login_required": true and configure aks_cluster_name,
  # aks_resource_group and aks_namespace on a schema entity to connect to AKS.
  # Values supplied at runtime in resource_info override these schema defaults.
  schemas = file("YOUR_PATH/schemas.json.tpl")
  schedules = file("YOUR_PATH/schedules.json.tpl")

  orchestrator_image = {
    image_name        = "pagopa/cloudo-orchestrator"
    image_tag         = "VERSION"
    registry_url      = "https://ghcr.io"
    registry_username = "payments-cloud-bot"
    registry_password = data.azurerm_key_vault_secret.github_pat.value
  }

  # (Required) You must deploy almost 1 worker with map <name> = <capability>
  workers_config = {
    workers = {
      "generic-worker" = "generic"
    }
    image_name        = "pagopa/cloudo-worker"
    image_tag         = "VERSION"
    registry_url      = "https://ghcr.io"
    registry_username = "payments-cloud-bot"
    registry_password = data.azurerm_key_vault_secret.github_pat.value
  }

  # (Optional) Enable ClouDO UI to manage. (Default: true)
  enable_ui = true
  ui_image = {
    image_name        = var.cloudo_ui.image_name
    image_tag         = var.cloudo_ui.image_tag
    registry_url      = var.cloudo_ui.registry_url
    registry_username = var.cloudo_ui.registry_username
    registry_password = data.azurerm_key_vault_secret.github_pat.value
  }

  # (Optional) EXAMPLE Routing configs
  # This is required if UI is not installed.
  # ==========================================
  routing_config = {
    teams = {
      payments = {
        slack    = { channel = "#cloudo-test" }
        jsm = { team = "payments-oncall" }
      }
      platform = {
        slack    = { channel = "#cloudo-test" }
        jsm = { team = "platform-oncall" }
      }
      core-crit = {
        jsm = { team = "core-critical" }
      }
      finops = {
        slack = { channel = "#cloudo-test" }
      }
    }
    rules = [
      {
        when = { resourceGroup = "rg-payments" }
        then = [
          { type = "slack", team = "payments" },
          { type = "jsm", team = "payments" }
        ]
      },
      {
        when = { namespace = "kube-system" }
        then = [
          { type = "slack", team = "platform" }
        ]
      },
      {
        when = { namespace = "namespace1" }
        then = [
          { type = "slack", team = "team-1" }
        ]
      },
      {
        when = { subscriptionId = "00000000-0000-0000-0000-000000000000", severityMax = "Sev2" }
        then = [
          { type = "jsm", team = "core-crit" }
        ]
      },
      {
        when = { alertRule = "Costs-OverBudget" }
        then = [
          { type = "slack", team = "finops" }
        ]
      },
      {
        when = { oncall = "true", statusIn = ["failed", "error"], severityMax = "Sev2" }
        then = [
          { type = "jsm", team = "payments" },
          { type = "slack", team = "payments" },
          { type = "slack", team = "platform" }
        ]
      },
      {
        when = { any = "*" }
        then = [
          { type = "slack", team = "payments" }
        ]
      }
    ]
  }
  # ==========================================

  tags = module.tag_config.tags
}
```

### schedules.json.tpl example

```json
{
  "schedule-default": {
    "partition_key": "Schedule",
    "entity": [
      {
        "name": "Cache Health Check",
        "cron": "0 */5 * * * *",
        "runbook": "src/runbooks/aks/cache-health.sh",
        "run_args": "--namespace apiconfig",
        "worker_pool": "generic",
        "enabled": true,
        "oncall": true
      }
    ]
  }
}
```

`id` is generated automatically by Terraform as UUID.

## Requirements

| Name                                                                     | Version  |
| ------------------------------------------------------------------------ | -------- |
| <a name="requirement_terraform"></a> [terraform](#requirement_terraform) | >= 1.9.0 |
| <a name="requirement_azurerm"></a> [azurerm](#requirement_azurerm)       | ~> 4     |
| <a name="requirement_random"></a> [random](#requirement_random)          | 3.7.2    |

## Providers

| Name                                                         | Version |
| ------------------------------------------------------------ | ------- |
| <a name="provider_azurerm"></a> [azurerm](#provider_azurerm) | 4.42.0  |
| <a name="provider_random"></a> [random](#provider_random)    | 3.7.2   |

## Modules

| Name                                                                             | Source                                                                   | Version                                  |
| -------------------------------------------------------------------------------- | ------------------------------------------------------------------------ | ---------------------------------------- |
| <a name="module_function_snet"></a> [function_snet](#module_function_snet)       | git::https://github.com/pagopa/terraform-azurerm-v4//IDH/subnet          | 1b507dcbfc89880e17ff6722fb69b10dfda9368d |
| <a name="module_storage_account"></a> [storage_account](#module_storage_account) | git::https://github.com/pagopa/terraform-azurerm-v4.git//storage_account | 8265f125b07251a5efe7b9ff57707109de8b46ba |

## Resources

| Name                                                                                                                                                     | Type        |
| -------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------- |
| [azurerm_linux_function_app.orchestrator](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/linux_function_app)            | resource    |
| [azurerm_linux_function_app.worker](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/linux_function_app)                  | resource    |
| [azurerm_monitor_action_group.cloudo_trigger](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/monitor_action_group)      | resource    |
| [azurerm_role_assignment.role_assignment](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/role_assignment)               | resource    |
| [azurerm_service_plan.orchestrator](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/service_plan)                        | resource    |
| [azurerm_service_plan.workers](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/service_plan)                             | resource    |
| [azurerm_storage_queue.notification](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/storage_queue)                      | resource    |
| [azurerm_storage_queue.this](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/storage_queue)                              | resource    |
| [azurerm_storage_table.runbook_logger](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/storage_table)                    | resource    |
| [azurerm_storage_table.runbook_schemas](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/storage_table)                   | resource    |
| [azurerm_storage_table.workers_registry](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/storage_table)                  | resource    |
| [azurerm_storage_table_entity.schemas](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/storage_table_entity)             | resource    |
| [azurerm_user_assigned_identity.identity](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/user_assigned_identity)        | resource    |
| [random_password.internal_auth_token](https://registry.terraform.io/providers/hashicorp/random/3.7.2/docs/resources/password)                            | resource    |
| [random_uuid.uuid](https://registry.terraform.io/providers/hashicorp/random/3.7.2/docs/resources/uuid)                                                   | resource    |
| [azurerm_application_insights.this](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/data-sources/application_insights)             | data source |
| [azurerm_function_app_host_keys.orchestrator](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/data-sources/function_app_host_keys) | data source |
| [azurerm_subscription.current](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/data-sources/subscription)                          | data source |

## Inputs

| Name                                                                                                         | Description                                                                                                                                                                                         | Type                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | Default                                                                                                                                                         |                       Required                        |
| ------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------- | :---------------------------------------------------: | --- |
| <a name="input_aks_integration"></a> [aks_integration](#input_aks_integration)                               | Map of AKS cluster configurations including cluster_id for each cluster.                                                                                                                            | <pre>map(object({<br/> cluster_id = string<br/> }))</pre>                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | `{}`                                                                                                                                                            |                          no                           |
| <a name="input_app_service_logs"></a> [app_service_logs](#input_app_service_logs)                            | (Optional) Configuration for App Service file system logs including disk quota and retention period.                                                                                                | <pre>object({<br/> disk_quota_mb = number<br/> retention_period_days = number<br/> })</pre>                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | <pre>{<br/> "disk_quota_mb": 35,<br/> "retention_period_days": 3<br/>}</pre>                                                                                    |                          no                           |
| <a name="input_application_insights_name"></a> [application_insights_name](#input_application_insights_name) | The ID of the Application Insights to be linked to the Function App.                                                                                                                                | `string`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | n/a                                                                                                                                                             |                          yes                          |
| <a name="input_application_insights_rg"></a> [application_insights_rg](#input_application_insights_rg)       | The RG of the Application Insights to be linked to the Function App.                                                                                                                                | `string`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | n/a                                                                                                                                                             |                          yes                          |
| <a name="input_approval_runbook"></a> [approval_runbook](#input_approval_runbook)                            | (Optional) Configuration for approval runbook settings including time-to-live in minutes and secret key for approval validation. If not provided, approval functionality will use default settings. | <pre>object({<br/> ttl_min = optional(string)<br/> secret = optional(string)<br/> })</pre>                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | `{}`                                                                                                                                                            |                          no                           |
| <a name="input_custom_role_assignments"></a> [custom_role_assignments](#input_custom_role_assignments)       | List of generic role assignments. Each element: { role = <role name or role_definition_id>, scope = <full scope>, principal_id = (optional) }                                                       | <pre>list(object({<br/> role = string<br/> scope = string<br/> principal_id = optional(string)<br/> }))</pre>                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | `[]`                                                                                                                                                            |                          no                           |
| <a name="input_custom_roles_per_aks"></a> [custom_roles_per_aks](#input_custom_roles_per_aks)                | Map of AKS key => list of role names (backward compatibility)                                                                                                                                       | `map(list(string))`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | `{}`                                                                                                                                                            |                          no                           |
| <a name="input_custom_roles_subscription"></a> [custom_roles_subscription](#input_custom_roles_subscription) | List of role names at subscription level (backward compatibility)                                                                                                                                   | `list(string)`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | `[]`                                                                                                                                                            |                          no                           |
| <a name="input_env"></a> [env](#input_env)                                                                   | Environment                                                                                                                                                                                         | `string`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | n/a                                                                                                                                                             |                          yes                          |
| <a name="input_github_repo_info"></a> [github_repo_info](#input_github_repo_info)                            | A map containing GitHub repository information such as repo, branch, token.                                                                                                                         | <pre>object({<br/> repo_name = string<br/> repo_branch = optional(string, "main")<br/> repo_token = optional(string, "")<br/> runbook_path = string<br/> })</pre>                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | <pre>{<br/> "repo_branch": "main",<br/> "repo_name": "pagopa/payments-cloudo",<br/> "repo_token": "",<br/> "runbook_path": "src/runbooks"<br/>}</pre>           |                          no                           |
| <a name="input_location"></a> [location](#input_location)                                                    | (Required) Specifies the supported Azure location where the resource exists. Changing this forces a new resource to be created.                                                                     | `string`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | n/a                                                                                                                                                             |                          yes                          |
| <a name="input_jsm_api_key"></a> [jsm_api_key](#input_jsm_api_key)                                           | (Optional) The API key used for JSM integration to create and manage alerts. If not provided, JSM integration will be disabled.                                                                     | `string`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | `""`                                                                                                                                                            |                          no                           |
| <a name="input_orchestrator_image"></a> [orchestrator_image](#input_orchestrator_image)                      | n/a                                                                                                                                                                                                 | <pre>object({<br/> image_name = string<br/> image_tag = string<br/> registry_url = string<br/> registry_username = optional(string)<br/> registry_password = optional(string)<br/> })</pre>                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | n/a                                                                                                                                                             |                          yes                          |
| <a name="input_prefix"></a> [prefix](#input_prefix)                                                          | (Required) The prefix of resources. Changing this forces a new resource to be created.                                                                                                              | `string`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | n/a                                                                                                                                                             |                          yes                          |
| <a name="input_resource_group_name"></a> [resource_group_name](#input_resource_group_name)                   | (Required) The name of the Resource Group in which the resources should be exist.                                                                                                                   | `string`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | n/a                                                                                                                                                             |                          yes                          |
| <a name="input_routing_config"></a> [routing_config](#input_routing_config)                                  | Routing configuration: defaults, teams and rules (when/then)                                                                                                                                        | <pre>object({<br/> teams = optional(map(object({<br/> slack = optional(object({ channel = optional(string) }))<br/> jsm = optional(object({ team = optional(string) }))<br/> })), {})<br/> rules = list(object({<br/> when = object({<br/> any = optional(string) # "\*"<br/> finalOnly = optional(bool)<br/> statusIn = optional(list(string))<br/> resourceId = optional(string)<br/> resourceGroup = optional(string)<br/> resourceName = optional(string)<br/> subscriptionId = optional(string)<br/> namespace = optional(string)<br/> alertRule = optional(string)<br/> oncall = optional(string)<br/> resourceGroupPrefix = optional(string)<br/> severityMin = optional(string) # "Sev0..Sev4"<br/> severityMax = optional(string) # "Sev0..Sev4"<br/> })<br/> then = list(object({<br/> type = string # "slack" | "jsm"<br/> team = optional(string)<br/> channel = optional(string)<br/> token = optional(string)<br/> apiKey = optional(string)<br/> }))<br/> }))<br/> })</pre> | <pre>{<br/> "rules": [],<br/> "teams": {}<br/>}</pre> | no  |
| <a name="input_schemas"></a> [schemas](#input_schemas)                                                       | The name of the Storage Table for runbook schemas.                                                                                                                                                  | `string`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | n/a                                                                                                                                                             |                          yes                          |
| <a name="input_service_plan_sku"></a> [service_plan_sku](#input_service_plan_sku)                            | (Required) The SKU for the plan. (Default: B1)                                                                                                                                                      | `string`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | `"B1"`                                                                                                                                                          |                          no                           |
| <a name="input_slack_integration"></a> [slack_integration](#input_slack_integration)                         | (Optional) Configuration for Slack integration including the authentication token and target channel. If not provided, Slack integration will be disabled.                                          | <pre>object({<br/> token = string<br/> channel = optional(string)<br/> })</pre>                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | <pre>{<br/> "channel": "#cloudo-test",<br/> "token": ""<br/>}</pre>                                                                                             |                          no                           |
| <a name="input_subscription_id"></a> [subscription_id](#input_subscription_id)                               | (Optional) The Azure subscription ID for resource permission scope.                                                                                                                                 | `string`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | `""`                                                                                                                                                            |                          no                           |
| <a name="input_tags"></a> [tags](#input_tags)                                                                | A mapping of tags to assign to the Function App.                                                                                                                                                    | `map(string)`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | `{}`                                                                                                                                                            |                          no                           |
| <a name="input_team_jsm_api_keys"></a> [team_jsm_api_keys](#input_team_jsm_api_keys)                         | Team maps -> JSM API key (JSM_API_KEY\_<TEAM>)                                                                                                                                                      | `map(string)`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | `{}`                                                                                                                                                            |                          no                           |
| <a name="input_team_slack_channels"></a> [team_slack_channels](#input_team_slack_channels)                   | Team maps -> Slack channel (SLACK_CHANNEL\_<TEAM>)                                                                                                                                                  | `map(string)`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | `{}`                                                                                                                                                            |                          no                           |
| <a name="input_team_slack_tokens"></a> [team_slack_tokens](#input_team_slack_tokens)                         | Team maps -> Slack token (SLACK_TOKEN\_<TEAM>)                                                                                                                                                      | `map(string)`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | `{}`                                                                                                                                                            |                          no                           |
| <a name="input_vnet_name"></a> [vnet_name](#input_vnet_name)                                                 | The name of the VNet in which the Subnet exists.                                                                                                                                                    | `string`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | `null`                                                                                                                                                          |                          no                           |
| <a name="input_vnet_rg"></a> [vnet_rg](#input_vnet_rg)                                                       | The name of the Resource Group in which the VNet exists.                                                                                                                                            | `string`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | `null`                                                                                                                                                          |                          no                           |
| <a name="input_workers_config"></a> [workers_config](#input_workers_config)                                  | n/a                                                                                                                                                                                                 | <pre>object({<br/> workers = optional(map(<br/> string<br/> ), {})<br/> image_name = string<br/> image_tag = string<br/> registry_url = string<br/> registry_username = optional(string)<br/> registry_password = optional(string)<br/> })</pre>                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | n/a                                                                                                                                                             |                          yes                          |

## Outputs

No outputs.
