import React, { useState, useMemo } from "react";
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
  HiOutlineChevronDown,
  HiOutlineChevronUp,
} from "react-icons/hi";
import { SiTerraform } from "react-icons/si";
import { Schema } from "../types";

interface SchemaTableProps {
  schemas: Schema[];
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

export function SchemaTable({
  schemas,
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
}: SchemaTableProps) {
  const [sortConfig, setSortConfig] = useState<{
    key: keyof Schema;
    direction: "asc" | "desc";
  } | null>(null);

  const sortedSchemas = useMemo(() => {
    const sortableItems = [...schemas];
    if (sortConfig !== null) {
      sortableItems.sort((a, b) => {
        const aValue = a[sortConfig.key] || "";
        const bValue = b[sortConfig.key] || "";
        if (aValue < bValue) {
          return sortConfig.direction === "asc" ? -1 : 1;
        }
        if (aValue > bValue) {
          return sortConfig.direction === "asc" ? 1 : -1;
        }
        return 0;
      });
    }
    return sortableItems;
  }, [schemas, sortConfig]);

  const requestSort = (key: keyof Schema) => {
    let direction: "asc" | "desc" = "asc";
    if (
      sortConfig &&
      sortConfig.key === key &&
      sortConfig.direction === "asc"
    ) {
      direction = "desc";
    }
    setSortConfig({ key, direction });
  };

  const getSortIcon = (key: keyof Schema) => {
    if (!sortConfig || sortConfig.key !== key) {
      return <HiOutlineChevronDown className="w-3 h-3 opacity-20" />;
    }
    return sortConfig.direction === "asc" ? (
      <HiOutlineChevronUp className="w-3 h-3 text-cloudo-accent" />
    ) : (
      <HiOutlineChevronDown className="w-3 h-3 text-cloudo-accent" />
    );
  };

  return (
    <div className="border border-cloudo-border bg-cloudo-panel overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-left border-collapse">
          <thead>
            <tr className="border-b border-cloudo-border bg-cloudo-accent/5">
              <th className="px-6 py-4 text-[10px] font-black uppercase tracking-[0.2em] text-cloudo-muted">
                Status
              </th>
              <th
                className="px-6 py-4 text-[10px] font-black uppercase tracking-[0.2em] text-cloudo-muted cursor-pointer hover:text-cloudo-text transition-colors"
                onClick={() => requestSort("name")}
              >
                <div className="flex items-center gap-2">
                  Name / ID {getSortIcon("name")}
                </div>
              </th>
              <th
                className="px-6 py-4 text-[10px] font-black uppercase tracking-[0.2em] text-cloudo-muted cursor-pointer hover:text-cloudo-text transition-colors"
                onClick={() => requestSort("runbook")}
              >
                <div className="flex items-center gap-2">
                  Runbook {getSortIcon("runbook")}
                </div>
              </th>
              <th
                className="px-6 py-4 text-[10px] font-black uppercase tracking-[0.2em] text-cloudo-muted cursor-pointer hover:text-cloudo-text transition-colors"
                onClick={() => requestSort("worker")}
              >
                <div className="flex items-center gap-2">
                  Worker {getSortIcon("worker")}
                </div>
              </th>
              <th
                className="px-6 py-4 text-[10px] font-black uppercase tracking-[0.2em] text-cloudo-muted cursor-pointer hover:text-cloudo-text transition-colors"
                onClick={() => requestSort("group")}
              >
                <div className="flex items-center gap-2">
                  Group {getSortIcon("group")}
                </div>
              </th>
              <th className="px-6 py-4 text-[10px] font-black uppercase tracking-[0.2em] text-cloudo-muted">
                Run_Args
              </th>
              <th className="px-6 py-4 text-[10px] font-black uppercase tracking-[0.2em] text-cloudo-muted">
                Tags
              </th>
              <th className="px-6 py-4 text-[10px] font-black uppercase tracking-[0.2em] text-cloudo-muted text-right">
                Actions
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-cloudo-border/50">
            {sortedSchemas.map((schema) => {
              const isTf = schema.tags
                ?.split(",")
                .map((t) => t.trim().toLowerCase())
                .includes("terraform");
              const canEdit =
                !isViewer &&
                (userRole === "ADMIN" || userRole === "OPERATOR") &&
                !isTf;

              return (
                <tr
                  key={schema.RowKey}
                  className="hover:bg-cloudo-accent/[0.02] transition-colors group"
                >
                  <td className="px-6 py-4 whitespace-nowrap">
                    <div className="flex gap-1.5 items-center">
                      <button
                        onClick={() => onToggle && onToggle(schema)}
                        disabled={togglingId === schema.id}
                        className={`p-1 border transition-all ${
                          schema.enabled !== false
                            ? "bg-cloudo-ok/5 border-cloudo-ok/30 text-cloudo-ok hover:border-cloudo-ok/50"
                            : "bg-cloudo-accent/10 border-cloudo-border text-cloudo-muted hover:border-white/20"
                        } ${
                          togglingId === schema.id
                            ? "opacity-50 cursor-wait"
                            : ""
                        }`}
                        title={
                          schema.enabled !== false
                            ? "Disable Runbook"
                            : "Enable Runbook"
                        }
                      >
                        {togglingId === schema.id ? (
                          <HiOutlineRefresh className="w-3.5 h-3.5 animate-spin" />
                        ) : schema.enabled !== false ? (
                          <HiOutlineCheck className="w-3.5 h-3.5" />
                        ) : (
                          <HiOutlineCheck className="w-3.5 h-3.5 opacity-20" />
                        )}
                      </button>
                      <div
                        title={
                          String(schema.require_approval) === "true"
                            ? "Approval Gate Active"
                            : "Auto-Execute"
                        }
                        className={`p-1 border ${
                          String(schema.require_approval) === "true"
                            ? "bg-cloudo-warn/5 border-cloudo-warn/30 text-cloudo-warn"
                            : "bg-cloudo-ok/5 border-cloudo-ok/30 text-cloudo-ok"
                        }`}
                      >
                        <HiOutlineShieldCheck className="w-3.5 h-3.5" />
                      </div>
                      {schema.oncall === "true" && (
                        <div
                          className="p-1 border bg-cloudo-accent/10 border-cloudo-accent/40 text-cloudo-accent"
                          title="On-Call Flow Active"
                        >
                          <HiOutlineUserGroup className="w-3.5 h-3.5" />
                        </div>
                      )}
                    </div>
                  </td>
                  <td className="px-6 py-4">
                    <div className="flex flex-col gap-0.5 max-w-[250px]">
                      <span className="text-sm font-bold text-cloudo-text uppercase truncate group-hover:text-cloudo-accent transition-colors">
                        {schema.name}
                      </span>
                      <button
                        onClick={() => onCopyId(schema.id)}
                        className="text-[9px] font-mono text-cloudo-muted/60 flex items-center gap-1.5 hover:text-cloudo-accent w-fit transition-colors group/id"
                      >
                        <span>{schema.id}</span>
                        {copiedId === schema.id ? (
                          <HiOutlineCheck className="text-cloudo-ok w-2.5 h-2.5" />
                        ) : (
                          <HiOutlineClipboardCopy className="w-2.5 h-2.5 opacity-0 group-hover/id:opacity-100" />
                        )}
                      </button>
                    </div>
                  </td>
                  <td className="px-6 py-4">
                    <div className="flex items-center gap-2 max-w-[200px]">
                      <HiOutlineTerminal className="text-cloudo-accent/60 w-3.5 h-3.5 shrink-0" />
                      <span
                        className="text-[11px] font-mono hover:text-cloudo-accent truncate cursor-pointer hover:text-cloudo-accent transition-colors"
                        onClick={() => onViewSource(schema.runbook)}
                        title="View Source"
                      >
                        {schema.runbook}
                      </span>
                    </div>
                  </td>
                  <td className="px-6 py-4">
                    <div className="flex items-center gap-2">
                      <HiOutlineChip className="text-cloudo-accent/60 w-3.5 h-3.5" />
                      <span className="text-[11px] font-mono text-cloudo-text/70 uppercase">
                        {schema.worker}
                      </span>
                    </div>
                  </td>
                  <td className="px-6 py-4">
                    <div className="flex items-center gap-2">
                      <HiOutlineUserGroup className="text-cloudo-accent/60 w-3.5 h-3.5" />
                      <span className="text-[11px] font-mono text-cloudo-text/70 uppercase">
                        {schema.group || "default"}
                      </span>
                    </div>
                  </td>
                  <td className="px-6 py-4">
                    <div className="flex flex-col gap-0.5 max-w-[150px]">
                      <span className="text-[10px] font-mono text-cloudo-text/60 truncate italic">
                        {schema.run_args || "-"}
                      </span>
                    </div>
                  </td>
                  <td className="px-6 py-4">
                    <div className="flex flex-wrap gap-1.5 max-w-[200px]">
                      {schema.tags
                        ?.split(",")
                        .map((t) => t.trim())
                        .filter((t) => t !== "")
                        .map((tag, idx) => {
                          const isTagTf = tag.toLowerCase() === "terraform";
                          return (
                            <span
                              key={idx}
                              className={`px-1.5 py-0.5 border text-[8px] font-black uppercase tracking-tighter flex items-center gap-1 ${
                                isTagTf
                                  ? "bg-[#7B42BC]/10 border-[#7B42BC]/30 text-[#7B42BC]"
                                  : "bg-cloudo-accent/5 border-cloudo-accent/20 text-cloudo-accent"
                              }`}
                            >
                              {isTagTf && (
                                <SiTerraform className="w-2.5 h-2.5" />
                              )}
                              {tag}
                            </span>
                          );
                        })}
                    </div>
                  </td>
                  <td className="px-6 py-4 text-right">
                    <div className="flex items-center justify-end gap-2">
                      {!isViewer && (
                        <button
                          onClick={() => {
                            if (confirmRunId === schema.id) {
                              onRun(schema.id);
                            } else {
                              onConfirmRun(schema.id);
                            }
                          }}
                          disabled={executingId === schema.id}
                          className={`h-8 px-3 border transition-all flex items-center gap-2 font-black text-[9px] uppercase tracking-widest ${
                            confirmRunId === schema.id
                              ? "bg-cloudo-accent border-cloudo-accent text-cloudo-dark"
                              : "bg-cloudo-accent/5 border-cloudo-border text-cloudo-accent hover:border-cloudo-accent/40"
                          } ${
                            executingId === schema.id
                              ? "opacity-50 cursor-wait"
                              : ""
                          }`}
                        >
                          {executingId === schema.id ? (
                            <HiOutlineRefresh className="w-3 h-3 animate-spin" />
                          ) : (
                            <HiOutlinePlay className="w-3 h-3" />
                          )}
                          {confirmRunId === schema.id ? "Confirm?" : "Run"}
                        </button>
                      )}
                      <button
                        onClick={() => onEdit(schema)}
                        className="p-1.5 border border-cloudo-border text-cloudo-muted hover:text-cloudo-text hover:border-cloudo-muted/50 transition-all bg-cloudo-panel"
                        title={canEdit ? "Edit Configuration" : "View Schema"}
                      >
                        {canEdit ? (
                          <HiOutlinePencil className="w-3.5 h-3.5" />
                        ) : (
                          <HiOutlineEye className="w-3.5 h-3.5" />
                        )}
                      </button>
                      {!isViewer &&
                        (userRole === "ADMIN" || userRole === "OPERATOR") && (
                          <button
                            onClick={() => onDelete(schema)}
                            disabled={isTf}
                            className={`p-1.5 border transition-all ${
                              isTf
                                ? "opacity-20 cursor-not-allowed bg-cloudo-panel-2 border-cloudo-border"
                                : "border-cloudo-border text-cloudo-err hover:bg-cloudo-err hover:text-white"
                            }`}
                            title={isTf ? "Protected Asset" : "Delete Schema"}
                          >
                            <HiOutlineTrash className="w-3.5 h-3.5" />
                          </button>
                        )}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
