import React, { useEffect, useState } from "react";
import {
  HiOutlineTerminal,
  HiOutlineChip,
  HiOutlineCheck,
  HiOutlineIdentification,
  HiOutlineCollection,
  HiOutlineAdjustments,
} from "react-icons/hi";
import { cloudoFetch } from "@/lib/api";
import { Schema } from "../types";
import { LabelWithTooltip } from "./LabelWithTooltip";

export const SCHEMA_FORM_ID = "schema-config-form";

interface SchemaFormProps {
  initialData?: Schema | null;
  mode: "create" | "edit" | "view";
  availableRunbooks: string[];
  availableWorkers: string[];
  onSuccess: (message: string) => void;
  onError: (message: string) => void;
  onSubmittingChange?: (submitting: boolean) => void;
}

export function SchemaForm({
  initialData,
  mode,
  availableRunbooks,
  availableWorkers,
  onSuccess,
  onError,
  onSubmittingChange,
}: SchemaFormProps) {
  const currentUserTeam = (() => {
    if (typeof window === "undefined") return "default";
    try {
      return (
        JSON.parse(localStorage.getItem("cloudo_user") || "null")?.team ||
        "default"
      );
    } catch {
      return "default";
    }
  })();
  const isAdmin = (() => {
    if (typeof window === "undefined") return false;
    try {
      return (
        JSON.parse(localStorage.getItem("cloudo_user") || "null")?.role ===
        "ADMIN"
      );
    } catch {
      return false;
    }
  })();
  const [formData, setFormData] = useState({
    id: initialData?.id || "",
    name: initialData?.name || "",
    description: initialData?.description || "",
    group: initialData?.group || "-",
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
    team: initialData?.team || (isAdmin ? "default" : currentUserTeam),
  });
  const [submitting, setSubmitting] = useState(false);
  const [teams, setTeams] = useState<
    { id: string; name: string; enabled: boolean }[]
  >([]);

  useEffect(() => {
    cloudoFetch("/teams")
      .then((response) => (response.ok ? response.json() : []))
      .then((data) => setTeams(Array.isArray(data) ? data : []))
      .catch(() => setTeams([]));
  }, []);

  const setSubmittingState = (value: boolean) => {
    setSubmitting(value);
    onSubmittingChange?.(value);
  };

  useEffect(() => {
    onSubmittingChange?.(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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

    setSubmittingState(true);

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
        setSubmittingState(false);
        return;
      }

      onSuccess(
        mode === "create" ? "Schema registered" : "Configuration updated",
      );
    } catch (e) {
      onError("Network error // uplink failed");
      console.error(e);
      setSubmittingState(false);
    }
  };

  const isDisabled = mode === "view" || submitting;

  return (
    <form id={SCHEMA_FORM_ID} onSubmit={submit}>
      <div className="grid grid-cols-[repeat(auto-fit,minmax(320px,1fr))] gap-8 items-start w-full">
        {/* Identification Section */}
        <div className="space-y-6">
          <div className="flex items-center gap-3">
            <div className="w-1.5 h-4 bg-cloudo-accent" />
            <h2 className="text-sm font-black uppercase tracking-[0.4em] text-cloudo-text flex items-center gap-2">
              <HiOutlineIdentification className="w-4 h-4 text-cloudo-accent" />
              Identification
            </h2>
          </div>

          <div className="bg-cloudo-panel border border-cloudo-border p-6 space-y-5">
            <div className="space-y-1.5">
              <LabelWithTooltip tooltip="Unique identifier for the schema. Cannot be changed after creation.">
                SCHEMA_ID // ALERT_ID *
              </LabelWithTooltip>
              <input
                type="text"
                required
                disabled={mode !== "create" || submitting}
                className="input font-mono text-cloudo-accent w-full h-11 disabled:opacity-50 disabled:cursor-not-allowed"
                value={formData.id}
                onChange={(e) =>
                  setFormData({ ...formData, id: e.target.value })
                }
                placeholder="e.g. aks-pod-restart"
              />
            </div>

            <div className="space-y-1.5">
              <LabelWithTooltip tooltip="Human-readable name for this schema.">
                Schema Name *
              </LabelWithTooltip>
              <input
                type="text"
                required
                disabled={isDisabled}
                className="input w-full h-11 disabled:opacity-50 disabled:cursor-not-allowed"
                value={formData.name}
                onChange={(e) =>
                  setFormData({ ...formData, name: e.target.value })
                }
                placeholder="e.g. AKS Cleanup Task"
              />
            </div>

            <div className="space-y-1.5">
              <LabelWithTooltip tooltip="Detailed explanation of what this automation does.">
                Purpose Description
              </LabelWithTooltip>
              <textarea
                disabled={isDisabled}
                className="input min-h-[110px] py-2.5 resize-none w-full disabled:opacity-50 disabled:cursor-not-allowed"
                value={formData.description}
                onChange={(e) =>
                  setFormData({ ...formData, description: e.target.value })
                }
                placeholder="Objective of this automation..."
              />
            </div>

            <div className="space-y-1.5">
              <LabelWithTooltip tooltip="Schema grouping bucket used avoid runbook that can not run in parallel.">
                Group
              </LabelWithTooltip>
              <input
                type="text"
                disabled={isDisabled}
                className="input w-full h-11 disabled:opacity-50 disabled:cursor-not-allowed"
                value={formData.group}
                onChange={(e) =>
                  setFormData({ ...formData, group: e.target.value })
                }
                placeholder="-"
              />
            </div>

            <div className="space-y-1.5">
              <LabelWithTooltip tooltip="The default team is shared with every team.">
                Team
              </LabelWithTooltip>
              <select
                disabled={isDisabled || !isAdmin}
                className="input w-full h-11 disabled:opacity-50"
                value={formData.team}
                onChange={(e) =>
                  setFormData({ ...formData, team: e.target.value })
                }
              >
                {teams
                  .filter(
                    (team) =>
                      (team.enabled || team.id === formData.team) &&
                      (isAdmin ||
                        team.id === currentUserTeam ||
                        team.id === formData.team),
                  )
                  .map((team) => (
                    <option key={team.id} value={team.id}>
                      {team.name}
                    </option>
                  ))}
              </select>
            </div>
          </div>
        </div>

        {/* Execution Target Section */}
        <div className="space-y-6">
          <div className="flex items-center gap-3">
            <div className="w-1.5 h-4 bg-cloudo-warn" />
            <h2 className="text-sm font-black uppercase tracking-[0.4em] text-cloudo-text flex items-center gap-2">
              <HiOutlineTerminal className="w-4 h-4 text-cloudo-warn" />
              Execution Target
            </h2>
          </div>

          <div className="bg-cloudo-panel border border-cloudo-border p-6 space-y-5">
            <div className="space-y-1.5">
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
                  className="input input-icon font-mono w-full h-11 disabled:opacity-50 disabled:cursor-not-allowed"
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

            <div className="space-y-1.5">
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
                  className="input input-icon font-mono w-full h-11 appearance-none cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
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

            <div className="space-y-1.5">
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
                  className="input input-icon font-mono text-cloudo-warn/80 w-full h-11 disabled:opacity-50 disabled:cursor-not-allowed"
                  value={formData.run_args}
                  onChange={(e) =>
                    setFormData({ ...formData, run_args: e.target.value })
                  }
                  placeholder="--force --silent"
                />
              </div>
            </div>
          </div>
        </div>

        {/* Behavior & Metadata Section */}
        <div className="space-y-6">
          <div className="flex items-center gap-3">
            <div className="w-1.5 h-4 bg-cloudo-ok" />
            <h2 className="text-sm font-black uppercase tracking-[0.4em] text-cloudo-text flex items-center gap-2">
              <HiOutlineAdjustments className="w-4 h-4 text-cloudo-ok" />
              Behavior & Metadata
            </h2>
          </div>

          <div className="bg-cloudo-panel border border-cloudo-border p-6 space-y-6">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
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
                  <p className="text-sm font-black text-cloudo-text uppercase tracking-widest">
                    Approval Gate
                  </p>
                  <p className="text-[11px] text-cloudo-muted uppercase font-bold opacity-70">
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
                  {formData.require_approval && (
                    <HiOutlineCheck className="w-4 h-4" />
                  )}
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
                  <p className="text-sm font-black text-cloudo-text uppercase tracking-widest">
                    On-Call Flow
                  </p>
                  <p className="text-[11px] text-cloudo-muted uppercase font-bold opacity-70">
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
                  {formData.oncall === "true" && (
                    <HiOutlineCheck className="w-4 h-4" />
                  )}
                </div>
              </div>
            </div>

            <div className="space-y-1.5">
              <LabelWithTooltip tooltip="Metadata tags for categorization.">
                <span className="flex items-center gap-2">
                  <HiOutlineCollection className="w-3.5 h-3.5" />
                  Tags (comma separated)
                </span>
              </LabelWithTooltip>
              <div className="flex gap-2">
                <div className="h-11 px-3 bg-cloudo-accent/10 border border-cloudo-accent/30 text-cloudo-accent text-[11px] font-black flex items-center uppercase tracking-widest">
                  ui
                </div>
                <input
                  type="text"
                  disabled={isDisabled}
                  className="input flex-1 h-11 disabled:opacity-50 disabled:cursor-not-allowed"
                  value={formData.tags}
                  onChange={(e) =>
                    setFormData({ ...formData, tags: e.target.value })
                  }
                  placeholder="e.g. production, urgent"
                />
              </div>
            </div>
          </div>
        </div>
      </div>
    </form>
  );
}
