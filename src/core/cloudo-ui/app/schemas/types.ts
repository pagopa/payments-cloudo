export interface Schema {
  PartitionKey: string;
  RowKey: string;
  id: string;
  name: string;
  group?: string;
  description: string;
  runbook: string;
  run_args: string;
  worker: string;
  oncall: boolean;
  require_approval: boolean;
  aks_login_required: boolean;
  aks_cluster_name?: string;
  aks_resource_group?: string;
  aks_namespace?: string;
  enabled?: boolean;
  severity?: string;
  monitor_condition?: string;
  tags?: string;
  team?: string;
}

export interface Notification {
  id: string;
  type: "success" | "error";
  message: string;
}
