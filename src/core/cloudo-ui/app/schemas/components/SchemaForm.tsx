import React, { useState } from "react";
import {
  HiOutlineTerminal,
  HiOutlineChip,
  HiOutlineCheck,
} from "react-icons/hi";
import { cloudoFetch } from "@/lib/api";
import { Schema } from "../types";
import { LabelWithTooltip } from "./LabelWithTooltip";

interface SchemaFormProps {
  initialData?: Schema | null;
  mode: "create" | "edit" | "view";
  availableRunbooks: string[];
  availableWorkers: string[];
  onSuccess: (message: string) => void;
  onCancel: () => void;
  onError: (message: string) => void;
}

export function SchemaForm({
  initialData,
  mode,
  availableRunbooks,
  availableWorkers,
  onSuccess,
  onCancel,
  onError,
}: SchemaFormProps) {
  const [formData, setFormData] = useState({
    id: initialData?.id || "",
    name: initialData?.name || "",
    description: initialData?.description || "",
    runbook: initialData?.runbook || "",
    run_args: initialData?.run_args || "",
    worker: initialData?.worker || "",
    oncall: initialData?.oncall || "",
    require_approval: initialData?.require_approval || false,
    severity: initialData?.severity || "",
    monitor_condition: initialData?.monitor_condition || "",
    tags: (initialData?.tags || (mode === "create" ? "ui" : ""))
      .split(",")
      .map((t) => t.trim())
      .filter((t) => t !== "ui")
      .join(", "),
  });
  const [submitting, setSubmitting] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();

    const originalTags = initialData?.tags || "";
    const isTf = originalTags
      .split(",")
      .map((t) => t.trim().toLowerCase())
      .includes("terraform");
    if (mode === "view" && isTf) {
      onError("Cannot modify Terraform-managed schema");
      return;
    }

    setSubmitting(true);

    const userTags = formData.tags
      .split(",")
      .map((t) => t.trim())
      .filter((t) => t !== "");
    const finalTags = ["ui", ...userTags].join(", ");

    try {
      const response = await cloudoFetch(`/schemas`, {
        method: mode === "create" ? "POST" : "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          PartitionKey: "RunbookSchema",
          RowKey: formData.id,
          ...formData,
          tags: finalTags,
        }),
      });

      const data = await response.json();

      if (!response.ok) {
        onError(data.error || "Operation failed");
        setSubmitting(false);
        return;
      }

      onSuccess(
        mode === "create" ? "Schema registered" : "Configuration updated",
      );
    } catch (e) {
      onError("Network error // uplink failed");
      console.error(e);
      setSubmitting(false);
    }
  };

  const isDisabled = mode === "view";

  return (
    <form onSubmit={submit} className="p-8 grid grid-cols-2 gap-x-8 gap-y-6">
      <div className="space-y-2">
        <LabelWithTooltip tooltip="Unique identifier for the schema. Cannot be changed after creation.">
          SCHEMA_ID // ALERT_ID *
        </LabelWithTooltip>
        <input
          type="text"
          required
          disabled={mode !== "create"}
          className="input font-mono text-cloudo-accent w-full"
          value={formData.id}
          onChange={(e) => setFormData({ ...formData, id: e.target.value })}
          placeholder="e.g. aks-pod-restart"
        />
      </div>
      <div className="space-y-2">
        <LabelWithTooltip tooltip="Human-readable name for this schema.">
          Schema Name *
        </LabelWithTooltip>
        <input
          type="text"
          required
          disabled={isDisabled}
          className="input w-full"
          value={formData.name}
          onChange={(e) => setFormData({ ...formData, name: e.target.value })}
          placeholder="e.g. AKS Cleanup Task"
        />
      </div>

      <div className="space-y-2 col-span-2">
        <LabelWithTooltip tooltip="Detailed explanation of what this automation does.">
          Purpose Description
        </LabelWithTooltip>
        <textarea
          disabled={isDisabled}
          className="input min-h-[100px] py-4 resize-none w-full"
          value={formData.description}
          onChange={(e) =>
            setFormData({ ...formData, description: e.target.value })
          }
          placeholder="Objective of this automation..."
        />
      </div>

      <div className="space-y-2">
        <LabelWithTooltip tooltip="Path to the script or executable in the runbook repository.">
          Runbook Path *
        </LabelWithTooltip>
        <div className="relative group">
          <div className="absolute inset-y-0 left-0 w-10 flex items-center justify-center border-r border-cloudo-border/30 group-focus-within:border-cloudo-accent/50 bg-cloudo-accent/5">
            <HiOutlineTerminal className="text-cloudo-muted/70 w-4 h-4" />
          </div>
          <input
            type="text"
            required
            disabled={isDisabled}
            className="input input-icon font-mono w-full"
            value={formData.runbook}
            onChange={(e) =>
              setFormData({ ...formData, runbook: e.target.value })
            }
            placeholder="script.sh"
            list="runbooks-list"
          />
          <datalist id="runbooks-list">
            {availableRunbooks.map((rb) => (
              <option key={rb} value={rb} />
            ))}
          </datalist>
        </div>
      </div>
      <div className="space-y-2">
        <LabelWithTooltip tooltip="The required worker capability to execute this schema.">
          Worker Capability *
        </LabelWithTooltip>
        <div className="relative group">
          <div className="absolute inset-y-0 left-0 w-10 flex items-center justify-center border-r border-cloudo-border/30 group-focus-within:border-cloudo-accent/50 bg-cloudo-accent/5">
            <HiOutlineChip className="text-cloudo-muted/70 w-4 h-4" />
          </div>
          <select
            required
            disabled={isDisabled}
            className="input input-icon font-mono w-full appearance-none cursor-pointer"
            value={formData.worker}
            onChange={(e) =>
              setFormData({ ...formData, worker: e.target.value })
            }
          >
            <option
              value=""
              disabled
              className="bg-cloudo-panel text-cloudo-muted italic"
            >
              Select Worker Capability...
            </option>
            {availableWorkers.map((worker) => (
              <option
                key={worker}
                value={worker}
                className="bg-cloudo-panel text-cloudo-text py-2"
              >
                {worker}
              </option>
            ))}
          </select>
          <div className="absolute inset-y-0 right-0 flex items-center px-4 pointer-events-none text-cloudo-muted">
            <svg
              className="w-4 h-4"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth="2"
                d="M19 9l-7 7-7-7"
              />
            </svg>
          </div>
        </div>
      </div>

      <div className="space-y-2 col-span-2">
        <LabelWithTooltip tooltip="Optional arguments passed to the script during execution.">
          Run Arguments
        </LabelWithTooltip>
        <div className="relative group">
          <div className="absolute inset-y-0 left-0 w-10 flex items-center justify-center border-r border-cloudo-border/30 group-focus-within:border-cloudo-accent/50 bg-cloudo-accent/5">
            <HiOutlineTerminal className="text-cloudo-muted/70 w-4 h-4 opacity-50" />
          </div>
          <input
            type="text"
            disabled={isDisabled}
            className="input input-icon font-mono text-cloudo-warn/80 w-full"
            value={formData.run_args}
            onChange={(e) =>
              setFormData({ ...formData, run_args: e.target.value })
            }
            placeholder="--force --silent"
          />
        </div>
      </div>

      <div className="space-y-2 col-span-2">
        <LabelWithTooltip tooltip="Metadata tags for categorization.">
          Tags (comma separated)
        </LabelWithTooltip>
        <div className="flex gap-2">
          <div className="h-10 px-4 bg-cloudo-accent/10 border border-cloudo-accent/30 text-cloudo-accent text-[11px] font-black flex items-center uppercase tracking-widest">
            ui
          </div>
          <input
            type="text"
            disabled={isDisabled}
            className="input flex-1"
            value={formData.tags}
            onChange={(e) => setFormData({ ...formData, tags: e.target.value })}
            placeholder="e.g. production, urgent"
          />
        </div>
      </div>

      <div
        className={`flex items-center justify-between p-4 bg-cloudo-accent/10 border border-cloudo-border group hover:border-cloudo-accent/40 transition-all ${
          isDisabled ? "cursor-default" : "cursor-pointer"
        }`}
        onClick={() =>
          !isDisabled &&
          setFormData({
            ...formData,
            require_approval: !formData.require_approval,
          })
        }
      >
        <div className="space-y-1">
          <p className="text-[11px] font-black text-cloudo-text uppercase tracking-widest">
            Approval Gate
          </p>
          <p className="text-[10px] text-cloudo-muted uppercase font-bold opacity-70">
            Manual Auth
          </p>
        </div>
        <div
          className={`w-5 h-5 border flex items-center justify-center transition-all ${
            formData.require_approval == true
              ? "bg-cloudo-accent border-cloudo-accent text-cloudo-dark"
              : "border-cloudo-border"
          }`}
        >
          {formData.require_approval && <HiOutlineCheck className="w-4 h-4" />}
        </div>
      </div>

      <div
        className={`flex items-center justify-between p-4 bg-cloudo-accent/10 border border-cloudo-border group hover:border-cloudo-accent/40 transition-all ${
          isDisabled ? "cursor-default" : "cursor-pointer"
        }`}
        onClick={() =>
          !isDisabled &&
          setFormData({
            ...formData,
            oncall: formData.oncall === "true" ? "false" : "true",
          })
        }
      >
        <div className="space-y-1">
          <p className="text-[11px] font-black text-cloudo-text uppercase tracking-widest">
            On-Call Flow
          </p>
          <p className="text-[10px] text-cloudo-muted uppercase font-bold opacity-70">
            Notify Team
          </p>
        </div>
        <div
          className={`w-5 h-5 border flex items-center justify-center transition-all ${
            formData.oncall === "true"
              ? "bg-cloudo-accent border-cloudo-accent text-cloudo-dark"
              : "border-cloudo-border"
          }`}
        >
          {formData.oncall === "true" && <HiOutlineCheck className="w-4 h-4" />}
        </div>
      </div>

      <div className="flex gap-4 pt-6 border-t border-cloudo-border col-span-2">
        <button
          type="button"
          onClick={onCancel}
          className="btn btn-ghost px-8 h-12"
        >
          {isDisabled ? "Close" : "Cancel"}
        </button>
        {!isDisabled && (
          <button
            type="submit"
            disabled={submitting}
            className="btn btn-primary flex-1 h-12"
          >
            {submitting ? "Saving..." : "Save Schema"}
          </button>
        )}
      </div>
    </form>
  );
}
