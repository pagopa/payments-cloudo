import React from "react";
import {
  HiOutlineShieldCheck,
  HiOutlineUserGroup,
  HiOutlineCheck,
  HiOutlineClipboardCopy,
  HiOutlineTerminal,
  HiOutlineChip,
  HiOutlineRefresh,
  HiOutlinePlay,
  HiOutlinePencil,
  HiOutlineEye,
  HiOutlineTrash,
  HiOutlineBan,
} from "react-icons/hi";
import { SiTerraform } from "react-icons/si";
import { Schema } from "../types";

interface SchemaCardProps {
  schema: Schema;
  isViewer: boolean;
  userRole?: string;
  copiedId: string | null;
  confirmRunId: string | null;
  executingId: string | null;
  togglingId?: string | null;
  onCopyId: (id: string) => void;
  onRun: (id: string) => void;
  onToggle?: (schema: Schema) => void;
  onConfirmRun: (id: string | null) => void;
  onViewSource: (runbook: string) => void;
  onEdit: (schema: Schema) => void;
  onDelete: (schema: Schema) => void;
}

export function SchemaCard({
  schema,
  isViewer,
  userRole,
  copiedId,
  confirmRunId,
  executingId,
  togglingId,
  onCopyId,
  onRun,
  onToggle,
  onConfirmRun,
  onViewSource,
  onEdit,
  onDelete,
}: SchemaCardProps) {
  const isTf = schema.tags
    ?.split(",")
    .map((t) => t.trim().toLowerCase())
    .includes("terraform");

  const canEdit =
    !isViewer && (userRole === "ADMIN" || userRole === "OPERATOR") && !isTf;

  return (
    <div className="group relative flex flex-col bg-cloudo-panel border border-cloudo-border hover:border-cloudo-accent/40 transition-all duration-300 overflow-hidden">
      {/* Card Header */}
      <div className="p-6 border-b border-cloudo-border flex flex-col gap-2 relative">
        <div className="flex justify-between items-start gap-4">
          <h3 className="text-base font-black text-cloudo-text tracking-wide uppercase group-hover:text-cloudo-accent transition-colors leading-tight truncate">
            {schema.name}
          </h3>
          <div className="flex flex-wrap items-center gap-1.5 shrink-0">
            <div
              title={
                schema.enabled === false
                  ? "Runbook Disabled"
                  : "Runbook Enabled"
              }
              className={`px-1.5 py-0.5 text-[9px] font-black uppercase border flex items-center gap-1 ${
                schema.enabled === false
                  ? "bg-cloudo-err/5 border-cloudo-err/30 text-cloudo-err"
                  : "bg-cloudo-ok/5 border-cloudo-ok/30 text-cloudo-ok"
              }`}
            >
              {schema.enabled === false ? (
                <HiOutlineBan className="w-3 h-3" />
              ) : (
                <HiOutlineCheck className="w-3 h-3" />
              )}
              {schema.enabled === false ? "Disabled" : "Enabled"}
            </div>
            <div
              title={
                String(schema.require_approval) === "true"
                  ? "Approval Gate Active"
                  : "Auto-Execute"
              }
              className={`px-1.5 py-0.5 text-[9px] font-black uppercase border flex items-center gap-1 ${
                String(schema.require_approval) === "true"
                  ? "bg-cloudo-warn/5 border-cloudo-warn/30 text-cloudo-warn"
                  : "bg-cloudo-ok/5 border-cloudo-ok/30 text-cloudo-ok"
              }`}
            >
              <HiOutlineShieldCheck className="w-3 h-3" />
              {String(schema.require_approval) === "true" ? "Gate" : "Auto"}
            </div>
            {schema.oncall === "true" && (
              <div className="px-1.5 py-0.5 text-[9px] font-black uppercase border bg-cloudo-accent/10 border-cloudo-accent/40 text-cloudo-accent flex items-center gap-1">
                <HiOutlineUserGroup className="w-3 h-3" />
                OnCall
              </div>
            )}
          </div>
        </div>
        <button
          onClick={() => onCopyId(schema.id)}
          className="text-[10px] font-mono text-cloudo-muted/60 flex items-center gap-2 hover:text-cloudo-accent w-fit transition-colors group/id"
        >
          <span className="opacity-50">ID:</span>
          <span className="font-bold">{schema.id}</span>
          {copiedId === schema.id ? (
            <HiOutlineCheck className="text-cloudo-ok" />
          ) : (
            <HiOutlineClipboardCopy className="opacity-0 group-hover/id:opacity-100" />
          )}
        </button>
      </div>

      {/* Card Content */}
      <div className="p-6 space-y-6 flex-1">
        <div className="flex flex-col gap-3">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-cloudo-accent/5 border border-cloudo-border group-hover:border-cloudo-accent/20 transition-all shrink-0">
              <HiOutlineTerminal className="text-cloudo-accent w-4 h-4" />
            </div>
            <div className="flex flex-col min-w-0">
              <span className="text-[10px] text-cloudo-muted/50 uppercase font-black tracking-widest">
                Execution Path
              </span>
              <span
                className="text-xs text-cloudo-text/80 font-bold truncate cursor-pointer hover:text-cloudo-accent transition-colors"
                onClick={() => onViewSource(schema.runbook)}
                title="View Source"
              >
                {schema.runbook}
              </span>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <div className="p-2 bg-cloudo-accent/5 border border-cloudo-border group-hover:border-cloudo-accent/20 transition-all shrink-0">
              <HiOutlineChip className="text-cloudo-accent w-4 h-4" />
            </div>
            <div className="flex flex-col min-w-0">
              <span className="text-[10px] text-cloudo-muted/50 uppercase font-black tracking-widest">
                Worker Capability
              </span>
              <span className="text-xs text-cloudo-text/80 font-bold uppercase truncate">
                {schema.worker}
              </span>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <div className="p-2 bg-cloudo-accent/5 border border-cloudo-border group-hover:border-cloudo-accent/20 transition-all shrink-0">
              <HiOutlineTerminal className="text-cloudo-accent/70 w-4 h-4" />
            </div>
            <div className="flex flex-col min-w-0">
              <span className="text-[10px] text-cloudo-muted/50 uppercase font-black tracking-widest">
                Default Arguments
              </span>
              <span className="text-[11px] font-mono text-cloudo-text/70 truncate italic">
                {schema.run_args || "None"}
              </span>
            </div>
          </div>
        </div>

        <div className="space-y-2">
          <span className="text-[10px] text-cloudo-muted/50 uppercase font-black tracking-widest block">
            Tags & Metadata
          </span>
          <div className="flex flex-wrap gap-1.5">
            {schema.tags
              ?.split(",")
              .map((t) => t.trim())
              .filter((t) => t !== "")
              .map((tag, idx) => {
                const isTagTf = tag.toLowerCase() === "terraform";
                return (
                  <span
                    key={idx}
                    className={`px-2 py-0.5 border text-[9px] font-black uppercase tracking-tighter flex items-center gap-1 ${
                      isTagTf
                        ? "bg-[#7B42BC]/20 border-[#7B42BC]/40 text-[#7B42BC]"
                        : "bg-cloudo-accent/5 border-cloudo-accent/20 text-cloudo-accent"
                    }`}
                  >
                    {isTagTf && <SiTerraform className="w-3 h-3" />}
                    {tag}
                  </span>
                );
              })}
            {!schema.tags && (
              <span className="text-[10px] text-cloudo-muted/30 italic">
                No tags
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Card Actions */}
      <div className="p-4 border-t border-cloudo-border bg-cloudo-accent/[0.02] flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          {!isViewer && (
            <div className="relative group/run">
              <button
                onClick={() => onToggle && onToggle(schema)}
                disabled={togglingId === schema.id}
                className={`p-2.5 border transition-all ${
                  schema.enabled !== false
                    ? "bg-cloudo-accent/10 border-cloudo-border text-cloudo-ok hover:border-cloudo-ok/40"
                    : "bg-cloudo-accent/10 border-cloudo-border text-cloudo-muted hover:border-white/20"
                } ${togglingId === schema.id ? "opacity-50 cursor-wait" : ""} ${
                  userRole !== "ADMIN" && userRole !== "OPERATOR"
                    ? "hidden"
                    : ""
                }`}
                title={
                  schema.enabled !== false
                    ? "Disable Runbook"
                    : "Enable Runbook"
                }
              >
                {togglingId === schema.id ? (
                  <HiOutlineRefresh className="w-4 h-4 animate-spin" />
                ) : schema.enabled !== false ? (
                  <HiOutlineBan className="w-4 h-4" />
                ) : (
                  <HiOutlineCheck className="w-4 h-4" />
                )}
              </button>
            </div>
          )}
          {!isViewer && (
            <div className="relative group/run">
              <button
                onClick={() => {
                  if (confirmRunId === schema.id) {
                    onRun(schema.id);
                  } else {
                    onConfirmRun(schema.id);
                  }
                }}
                disabled={executingId === schema.id}
                className={`h-9 px-4 border transition-all flex items-center gap-2 font-black text-[10px] uppercase tracking-widest ${
                  confirmRunId === schema.id
                    ? "bg-cloudo-accent border-cloudo-accent text-cloudo-dark"
                    : "bg-cloudo-accent/10 border-cloudo-border text-cloudo-accent hover:border-cloudo-accent/40"
                } ${executingId === schema.id ? "opacity-50 cursor-wait" : ""}`}
              >
                {executingId === schema.id ? (
                  <HiOutlineRefresh className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <HiOutlinePlay className="w-3.5 h-3.5" />
                )}
                {confirmRunId === schema.id ? "Confirm?" : "Run"}
              </button>
            </div>
          )}
        </div>

        <div className="flex items-center gap-1.5">
          <button
            onClick={() => onEdit(schema)}
            className="p-2 border border-cloudo-border text-cloudo-muted hover:text-cloudo-text hover:border-cloudo-muted/50 transition-all bg-cloudo-panel"
            title={canEdit ? "Edit Configuration" : "View Schema"}
          >
            {canEdit ? (
              <HiOutlinePencil className="w-4 h-4" />
            ) : (
              <HiOutlineEye className="w-4 h-4" />
            )}
          </button>

          {!isViewer && (userRole === "ADMIN" || userRole === "OPERATOR") && (
            <button
              onClick={() => onDelete(schema)}
              disabled={isTf}
              className={`p-2 border transition-all ${
                isTf
                  ? "opacity-20 cursor-not-allowed bg-cloudo-panel-2 border-cloudo-border"
                  : "border-cloudo-border text-cloudo-err hover:bg-cloudo-err hover:text-white"
              }`}
              title={isTf ? "Protected Asset" : "Delete Schema"}
            >
              <HiOutlineTrash className="w-4 h-4" />
            </button>
          )}
        </div>
      </div>

      {/* Decorative Elements */}
      <div className="absolute top-0 right-0 w-8 h-8 border-t border-r border-cloudo-accent/0 group-hover:border-cloudo-accent/20 transition-all pointer-events-none" />
      <div className="absolute bottom-0 left-0 w-8 h-8 border-b border-l border-cloudo-accent/0 group-hover:border-cloudo-accent/20 transition-all pointer-events-none" />
    </div>
  );
}
