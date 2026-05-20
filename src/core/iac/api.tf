# API Management API for Cloudo
# This creates a REST API endpoint exposed through Azure API Manager

module "apim_api_cloudo_api_v1" {
  source = "git::https://github.com/pagopa/terraform-azurerm-v4.git//api_management_api?ref=77e0c671b8f4c11c6568e4b0cc87e30332b62090" #v8.5.1"

  # Basic API Configuration
  name                = format("%s-cloudo-api", var.prefix)
  api_management_name = var.api_management_name
  resource_group_name = var.api_management_rg
  api_version         = "v1"

  # Product Association
  product_ids = var.api_product_ids

  # API Display and Description
  display_name = "Cloudo API"
  description  = "REST API for Cloudo Orchestrator - Payments cloud runbook engine"

  # API Path on APIM (es. https://{host}/cloudo/...)
  path      = var.api_path
  protocols = ["https"]

  # Backend base URL (es. https://...azurewebsites.net/api)
  service_url = var.service_url

  # API Definition Format
  content_format = "openapi"
  content_value = templatefile("${path.module}/api/cloudo/v1/_openapi.json.tpl", {
    host     = var.api_manager_hostname
    api_path = var.api_path
  })

  # API Policies
  xml_content = templatefile("${path.module}/api/cloudo/v1/_base_policy.xml", {
    backend_base_url     = var.service_url
    backend_function_key = var.api_backend_function_key
  })

  # Subscription Configuration
  subscription_required = var.api_subscription_required
}
