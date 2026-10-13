[
  {
    "partition_key": "generic",
    "entity": [
    {
      "id": "000-0000-0e0e00ww-wqesd",
      "name": "pippo",
      "description": "Hello Pippo V1!",
      "runbook": "check_sys.sh",
      "worker": "Runbook",
      "oncall": "true"
    },
    {
      "id": "000-0000-0e0e00wa-wqesd",
      "name": "pippo",
      "description": "Hello Pippo V2!",
      "runbook": "check_sys.sh",
      "worker": "Runbook",
      "oncall": "true",
      "run_args": ""
    }]
  },
  {
    "partition_key": "infra",
    "entity": [
    {
      "id": "000-0000-0e0e00ww-wasdsa",
      "name": "infra-pippo",
      "description": "Hello Pippo INFRA!",
      "runbook": "test.py",
      "run_args": "-n 1000 --repeats 1000",
      "worker": "Runbook",
      "oncall": "true",
      "aks_login_required": true,
      "aks_cluster_name": "aks-demo",
      "aks_resource_group": "rg-demo",
      "aks_namespace": "payments"
    }]
  },
  {
    "partition_key": "alert",
    "entity": [
    {
      "id": "12345678-1234-1234-1234-1234567890ab",
      "name": "smart-alert",
      "description": "SMART!",
      "runbook": "check_sys.sh",
      "worker": "Runbook",
      "oncall": "true"
    }]
  }
]
