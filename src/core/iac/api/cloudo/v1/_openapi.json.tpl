{
  "openapi": "3.0.0",
  "info": {
    "title": "Cloudo API",
    "description": "REST API for Cloudo Orchestrator - Payment automation and execution platform",
    "version": "1.0.0",
    "contact": {
      "name": "PagoPA"
    }
  },
  "servers": [
    {
      "url": "https://${host}/${api_path}"
    }
  ],
  "paths": {
    "/Trigger": {
      "post": {
        "summary": "Trigger to Cloudo orchestrator",
        "operationId": "triggerAlert",
        "parameters": [
          {
            "name": "x-cloudo-key",
            "in": "header",
            "required": true,
            "schema": {
              "type": "string"
            }
          },
          {
            "name": "id",
            "in": "query",
            "required": false,
            "schema": {
              "type": "string"
            },
            "description": "Optional runbook ID to override the one resolved from the alert schema"
          }
        ],
        "requestBody": {
          "required": true,
          "content": {
            "application/json": {
              "schema": {
                "type": "object",
                "properties": {
                  "schemaId": {
                    "type": "string",
                    "example": "azureMonitorCommonAlertSchema"
                  },
                  "data": {
                    "type": "object",
                    "properties": {
                      "essentials": {
                        "type": "object"
                      },
                      "alertContext": {
                        "type": "object"
                      }
                    },
                    "required": ["essentials", "alertContext"]
                  }
                },
                "required": ["schemaId", "data"]
              }
            }
          }
        },
        "responses": {
          "200": {
            "description": "Request accepted by backend"
          },
          "202": {
            "description": "Request accepted by backend"
          },
          "400": {
            "description": "Bad request"
          },
          "401": {
            "description": "Unauthorized"
          },
          "500": {
            "description": "Internal server error"
          }
        }
      }
    }
  },
  "components": {
    "securitySchemes": {
      "cloudo_key": {
        "type": "apiKey",
        "name": "x-cloudo-key",
        "in": "header"
      }
    }
  },
  "security": [
    {
      "cloudo_key": []
    }
  ]
}
