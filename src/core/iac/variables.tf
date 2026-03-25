variable "prefix" {
  type        = string
  description = "(Required) The prefix of resources. Changing this forces a new resource to be created."
}

variable "env" {
  type        = string
  description = "Environment"
}

variable "location" {
  type        = string
  description = "(Required) Specifies the supported Azure location where the resource exists. Changing this forces a new resource to be created."
}

variable "resource_group_name" {
  type        = string
  description = "(Required) The name of the Resource Group in which the resources should be exist."
}

variable "product_name" {
  type        = string
  default     = null
  description = "(Optional) The name of the product for IDH deployment."
}

variable "application_insights_name" {
  description = "The ID of the Application Insights to be linked to the Function App."
  type        = string
}

variable "application_insights_rg" {
  description = "The RG of the Application Insights to be linked to the Function App."
  type        = string
}

variable "schemas" {
  description = "The name of the Storage Table for runbook schemas."
  type        = string
  validation {
    condition = alltrue([
      for k, v in jsondecode(var.schemas) : (
        length(setsubtract(keys(v), ["partition_key", "entity"])) == 0 &&

        alltrue([
          for item in v.entity : (
            length(setsubtract(keys(item), [
              "id", "name", "description", "runbook", "run_args", "worker", "oncall", "tags", "require_approval"
            ])) == 0 &&
            item.id != "" && item.name != "" && item.runbook != "" && item.worker != "" &&
            contains([true, false], lookup(item, "oncall", "")) &&
            contains([true, false], lookup(item, "require_approval", ""))
          )
        ])
      )
    ])
    error_message = "The schema contains invalid keys or empty required fields (id, name, runbook, worker)."
  }
}

variable "subscription_id" {
  type        = string
  description = "(Optional) The Azure subscription ID for resource permission scope."
  default     = ""
}

// Variables for custom role assignments
variable "custom_role_assignments" {
  description = "List of generic role assignments. Each element: { role = <role name or role_definition_id>, scope = <full scope>, principal_id = (optional) }"
  type = list(object({
    role         = string
    scope        = string
    principal_id = optional(string)
  }))
  default = []
}

variable "custom_roles_per_aks" {
  description = "Map of AKS key => list of role names (backward compatibility)"
  type        = map(list(string))
  default     = {}
}

variable "custom_roles_subscription" {
  description = "List of role names at subscription level (backward compatibility)"
  type        = list(string)
  default     = []
}

variable "github_repo_info" {
  type = object({
    repo_name    = string
    repo_branch  = optional(string, "main")
    repo_token   = optional(string, "")
    runbook_path = string
  })
  description = "A map containing GitHub repository information such as repo, branch, token."
  default = {
    repo_name    = "pagopa/payments-cloudo"
    repo_branch  = "main"
    repo_token   = ""
    runbook_path = "src/runbooks"
  }
}

variable "vnet_name" {
  description = "The name of the VNet in which the Subnet exists."
  type        = string
  default     = null
}

variable "vnet_rg" {
  description = "The name of the Resource Group in which the VNet exists."
  type        = string
  default     = null
}

variable "private_endpoint_dns_zone_name" {
  type = string
}

# variable "private_endpoint_subnet_id" {
#   type    = string
#   default = null
# }

variable "vpn_subnet_id" {
  type = string
}

variable "aks_integration" {
  type = map(object({
    cluster_id = string
  }))
  description = "Map of AKS cluster configurations including cluster_id for each cluster."
  default     = {}
}

variable "orchestrator_image" {
  description = ""
  type = object({
    image_name        = string
    image_tag         = string
    registry_url      = string
    registry_username = optional(string)
    registry_password = optional(string)
  })
}

variable "ui_image" {
  description = ""
  type = object({
    image_name        = string
    image_tag         = string
    registry_url      = string
    registry_username = optional(string)
    registry_password = optional(string)
  })
}

variable "cloudo_ui_tier" {
  type    = string
  default = "basic"
}

variable "cloudo_google_sso_integration_client_id" {
  type        = string
  description = "Cloudo SSO google client id."
}

variable "cloudo_function_tier" {
  type    = string
  default = "basic_private"
}

variable "enable_ui" {
  type        = bool
  description = "Enable UI App Service"
  default     = true
}

variable "workers_config" {
  description = ""
  type = object({
    workers = optional(map(
      string
    ), {})
    image_name        = string
    image_tag         = string
    registry_url      = string
    registry_username = optional(string)
    registry_password = optional(string)
  })
}

# variable "service_plan_sku" {
#   type        = string
#   default     = "B1"
#   description = "(Required) The SKU for the plan. (Default: B1)"
# }

variable "key_vaults_integration" {
  type = map(object({
    name           = optional(string)
    resource_group = optional(string)
  }))
  default     = {}
  description = "List of key vaults to integrate on data reader RBAC Role."
}

variable "slack_integration" {
  description = "(Optional) Configuration for Slack integration including the authentication token and target channel. If not provided, Slack integration will be disabled."
  type = object({
    token   = string
    channel = optional(string)
  })
  default = {
    token   = ""
    channel = "#cloudo-test"
  }
}

variable "opsgenie_api_key" {
  description = "(Optional) The API key used for OpsGenie integration to create and manage alerts. If not provided, OpsGenie integration will be disabled."
  type        = string
  default     = ""
}

variable "team_opsgenie_api_keys" {
  description = "Team maps -> Opsgenie API key (OPSGENIE_API_KEY_<TEAM>)"
  type        = map(string)
  default     = {}
  sensitive   = true
}

variable "team_slack_tokens" {
  description = "Team maps -> Slack token (SLACK_TOKEN_<TEAM>)"
  type        = map(string)
  default     = {}
  sensitive   = true
}

variable "team_slack_channels" {
  description = "Team maps -> Slack channel (SLACK_CHANNEL_<TEAM>)"
  type        = map(string)
  default     = {}
}

variable "routing_config" {
  description = "Routing configuration: defaults, teams and rules (when/then)"
  type = object({
    teams = optional(map(object({
      slack    = optional(object({ channel = optional(string) }))
      opsgenie = optional(object({ team = optional(string) }))
    })), {})
    rules = list(object({
      when = object({
        any                 = optional(string) # "*"
        finalOnly           = optional(bool)
        statusIn            = optional(list(string))
        resourceId          = optional(string)
        resourceGroup       = optional(string)
        resourceName        = optional(string)
        subscriptionId      = optional(string)
        namespace           = optional(string)
        alertRule           = optional(string)
        oncall              = optional(string)
        resourceGroupPrefix = optional(string)
        severityMin         = optional(string) # "Sev0..Sev4"
        severityMax         = optional(string) # "Sev0..Sev4"
      })
      then = list(object({
        type    = string # "slack" | "opsgenie"
        team    = optional(string)
        channel = optional(string)
        token   = optional(string)
        apiKey  = optional(string)
      }))
    }))
  })
  default = {
    teams = {}
    rules = []
  }
}


variable "approval_runbook" {
  description = "(Optional) Configuration for approval runbook settings including time-to-live in minutes and secret key for approval validation. If not provided, approval functionality will use default settings."
  type = object({
    ttl_min = optional(string)
    secret  = optional(string)
  })
  default = {}
}

variable "tags" {
  description = "A mapping of tags to assign to the Function App."
  type        = map(string)
  default     = {}
}
