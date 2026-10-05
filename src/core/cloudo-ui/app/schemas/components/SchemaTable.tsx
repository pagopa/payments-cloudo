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
  userTeam?: string;
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
  userTeam,
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

  const headerClass =
    "px-3 py-3 text-[10px] font-black uppercase tracking-[0.2em] text-cloudo-muted";
  const sortableHeaderClass = `${headerClass} cursor-pointer hover:text-cloudo-text transition-colors`;

  return (
    <div className="border border-cloudo-border bg-cloudo-panel overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-left border-collapse text-xs">
          <thead>
            <tr className="border-b border-cloudo-border bg-cloudo-accent/5">
              <th className={headerClass}>Status</th>
              <th
                className={sortableHeaderClass}
                onClick={() => requestSort("name")}
              >
                <div className="flex items-center gap-1.5">
                  Name / ID {getSortIcon("name")}
                </div>
              </th>
              <th
                className={`hidden md:table-cell ${sortableHeaderClass}`}
                onClick={() => requestSort("runbook")}
              >
                <div className="flex items-center gap-1.5">
                  Runbook / Worker {getSortIcon("runbook")}
                </div>
              </th>
              <th className={`hidden lg:table-cell ${headerClass}`}>
                Args / Tags
              </th>
              <th className={`${headerClass} text-right`}>Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-cloudo-border/50">
            {sortedSchemas.map((schema) => {
              const isTf = schema.tags
                ?.split(",")
                .map((t) => t.trim().toLowerCase())
                .includes("terraform");
              const ownsSchema =
                userRole === "ADMIN" ||
                (schema.team || "default") === (userTeam || "default");
              const canEdit =
                !isViewer &&
                ownsSchema &&
                (userRole === "ADMIN" || userRole === "OPERATOR") &&
                !isTf;
              const tags = (schema.tags || "")
                .split(",")
                .map((t) => t.trim())
                .filter((t) => t !== "");

              return (
                <tr
                  key={schema.RowKey}
                  className="hover:bg-cloudo-accent/[0.02] transition-colors group"
                >
                  <td className="px-3 py-2 whitespace-nowrap">
                    <div className="flex gap-1 items-center">
                      <button
                        onClick={() => onToggle && onToggle(schema)}
                        disabled={togglingId === schema.id || !ownsSchema}
                        className={`p-1 border transition-all ${
                          schema.enabled !== false
                            ? "bg-cloudo-ok/5 border-cloudo-ok/30 text-cloudo-ok hover:border-cloudo-ok/50"
                            : "bg-cloudo-accent/10 border-cloudo-border text-cloudo-muted hover:border-white/20"
                        } ${
                          togglingId === schema.id
                            ? "opacity-50 cursor-wait"
                            : ""
                        } ${
                          !ownsSchema ? "cursor-not-allowed opacity-60" : ""
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
                  <td className="px-3 py-2 max-w-[260px]">
                    <div className="flex flex-col gap-0.5">
                      <span
                        className="text-xs font-bold text-cloudo-text uppercase truncate group-hover:text-cloudo-accent transition-colors"
                        title={schema.name}
                      >
                        {schema.name}
                      </span>
                      <div className="flex items-center gap-1.5 min-w-0">
                        <button
                          onClick={() => onCopyId(schema.id)}
                          className="text-[10px] font-mono text-cloudo-muted/60 flex items-center gap-1 hover:text-cloudo-accent min-w-0 transition-colors group/id"
                          title={schema.id}
                        >
                          <span className="truncate">{schema.id}</span>
                          {copiedId === schema.id ? (
                            <HiOutlineCheck className="text-cloudo-ok w-2.5 h-2.5 shrink-0" />
                          ) : (
                            <HiOutlineClipboardCopy className="w-2.5 h-2.5 shrink-0 opacity-0 group-hover/id:opacity-100" />
                          )}
                        </button>
                        <span className="text-[9px] px-1 border border-cloudo-border text-cloudo-accent uppercase tracking-widest shrink-0">
                          {schema.team || "default"}
                        </span>
                        {schema.group && schema.group !== "-" && (
                          <span
                            className="text-[9px] px-1 border border-cloudo-border text-cloudo-muted uppercase tracking-widest shrink-0"
                            title={`Group: ${schema.group}`}
                          >
                            {schema.group}
                          </span>
                        )}
                      </div>
                    </div>
                  </td>
                  <td className="hidden md:table-cell px-3 py-2 max-w-[220px]">
                    <div className="flex flex-col gap-0.5">
                      <button
                        onClick={() => onViewSource(schema.runbook)}
                        className="flex items-center gap-1.5 text-[11px] font-mono hover:text-cloudo-accent transition-colors min-w-0 cursor-pointer"
                        title={`View source: ${schema.runbook}`}
                      >
                        <HiOutlineTerminal className="text-cloudo-accent/60 w-3.5 h-3.5 shrink-0" />
                        <span className="truncate">{schema.runbook}</span>
                      </button>
                      <div className="flex items-center gap-1.5 text-[10px] font-mono text-cloudo-text/60 uppercase">
                        <HiOutlineChip className="text-cloudo-accent/60 w-3 h-3 shrink-0" />
                        <span className="truncate">{schema.worker}</span>
                      </div>
                    </div>
                  </td>
                  <td className="hidden lg:table-cell px-3 py-2 max-w-[220px]">
                    <div className="flex flex-col gap-1">
                      <span
                        className="text-[10px] font-mono text-cloudo-text/60 truncate italic"
                        title={schema.run_args || ""}
                      >
                        {schema.run_args || "-"}
                      </span>
                      {tags.length > 0 && (
                        <div className="flex flex-wrap gap-1">
                          {tags.map((tag, idx) => {
                            const isTagTf = tag.toLowerCase() === "terraform";
                            return (
                              <span
                                key={idx}
                                className={`px-1 border text-[8px] font-black uppercase tracking-tighter flex items-center gap-1 ${
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
                      )}
                    </div>
                  </td>
                  <td className="px-3 py-2 text-right">
                    <div className="flex items-center justify-end gap-1">
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
                          className={`h-7 px-2 border transition-all flex items-center gap-1.5 font-black text-[9px] uppercase tracking-widest ${
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
                        ownsSchema &&
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
