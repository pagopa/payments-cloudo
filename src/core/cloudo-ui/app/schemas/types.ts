import { ReactNode } from "react";

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
  oncall: string;
  require_approval: boolean;
  enabled?: boolean;
  severity?: string;
  monitor_condition?: string;
  tags?: string;
}

export interface Notification {
  id: string;
  type: "success" | "error";
  message: string;
}
