locals {
  entity_executor = flatten([
    for partition, k in jsondecode(var.schemas) :
    [
      for item in k.entity :
      {
        entity        = item
        partition_key = k.partition_key
      }
    ]
    ]
  )

  orchestrator_smart_routing_app_settings = merge(
    { for team, key in var.team_opsgenie_api_keys :
      "OPSGENIE_API_KEY_${upper(replace(team, "-", "_"))}" => key
    },
    { for team, tok in var.team_slack_tokens :
      "SLACK_TOKEN_${upper(replace(team, "-", "_"))}" => tok
    },
    { for team, ch in var.team_slack_channels :
      "SLACK_CHANNEL_${upper(replace(team, "-", "_"))}" => ch
    },
    {
      ROUTING_RULES = local.routing_rules_json_from_object
    },
  )

}
