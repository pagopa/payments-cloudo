"use client";

import { useState, useEffect, useCallback, useRef, Suspense } from "react";
import { cloudoFetch } from "@/lib/api";
import { useExecutionLogStream, useExecutionsStream } from "@/lib/logStream";
import { useSearchParams } from "next/navigation";
import {
  HiOutlineSearch,
  HiOutlineDatabase,
  HiOutlineTerminal,
  HiOutlineX,
  HiOutlineRefresh,
  HiOutlineFilter,
  HiOutlineClipboardCheck,
  HiOutlineClipboard,
  HiOutlineTag,
  HiOutlineFingerPrint,
  HiOutlineCalendar,
  HiOutlineShare,
  HiOutlineArrowsExpand,
  HiOutlineChevronLeft,
  HiOutlineChevronRight,
  HiOutlineChevronDown,
  HiCheckCircle,
  HiXCircle,
  HiClock,
  HiExclamationCircle,
  HiPlay,
  HiStop,
  HiOutlineChevronDoubleRight,
  HiOutlineInbox,
  HiOutlineCheckCircle,
  HiOutlineInformationCircle,
  HiOutlineSparkles,
  HiOutlineLightBulb,
  HiOutlineUsers,
} from "react-icons/hi";
import {
  parseDate,
  today,
  getLocalTimeZone,
  CalendarDate,
} from "@internationalized/date";
import { HiArrowPath, HiOutlineExclamationCircle } from "react-icons/hi2";

interface LogEntry {
  PartitionKey: string;
  RowKey: string;
  ExecId: string;
  Status: string;
  RequestedAt: string;
  Name: string;
  Id: string;
  Runbook: string;
  Run_Args: string;
  Log: string;
  MonitorCondition: string;
  Severity: string;
  OnCall?: boolean | string;
  Initiator?: string;
  Worker?: string;
  Group?: string;
  team?: string;
  ResourceInfo?: string;
  Notifications?: string;
}

const statusPriority: Record<string, number> = {
  succeeded: 5,
  completed: 5,
  success: 5,
  failed: 4,
  error: 4,
  running: 3,
  skipped: 3,
  rejected: 3,
  stopped: 3,
  accepted: 2,
  pending: 1,
  scheduled: 1,
};

const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: "pending", label: "PENDING" },
  { value: "scheduled", label: "SCHEDULED" },
  { value: "accepted", label: "ACCEPTED" },
  { value: "running", label: "RUNNING" },
  { value: "succeeded", label: "SUCCEEDED" },
  { value: "completed", label: "COMPLETED" },
  { value: "failed", label: "FAILED" },
  { value: "rejected", label: "REJECTED" },
  { value: "error", label: "ERROR" },
  { value: "stopped", label: "STOPPED" },
  { value: "skipped", label: "SKIPPED" },
];

export function LogsPanel() {
  return (
    <Suspense
      fallback={
        <div className="flex h-full items-center justify-center">
          <HiOutlineRefresh className="animate-spin w-8 h-8 text-cloudo-accent" />
        </div>
      }
    >
      <LogsPanelContent />
    </Suspense>
  );
}

function LogsPanelContent() {
  const searchParams = useSearchParams();
  const [partitionKey, setPartitionKey] = useState(
    searchParams.get("partitionKey") ||
      today(getLocalTimeZone()).toString().replace(/-/g, ""),
  );
  const [dateValue, setDateValue] = useState<CalendarDate | null>(() => {
    const pk = searchParams.get("partitionKey");
    if (pk && pk.length === 8) {
      try {
        const formatted = `${pk.slice(0, 4)}-${pk.slice(4, 6)}-${pk.slice(
          6,
          8,
        )}`;
        return parseDate(formatted);
      } catch {
        return today(getLocalTimeZone());
      }
    }
    return today(getLocalTimeZone());
  });
  const [execId, setExecId] = useState(searchParams.get("execId") || "");
  const [selectedStatuses, setSelectedStatuses] = useState<string[]>(() => {
    const raw =
      searchParams.get("status") || searchParams.get("statuses") || "";
    if (raw) {
      return raw
        .split(",")
        .map((s) => s.trim().toLowerCase())
        .filter(Boolean);
    }
    if (typeof window !== "undefined") {
      try {
        const saved = localStorage.getItem("cloudo_executions_statuses");
        if (saved) {
          const parsed = JSON.parse(saved);
          if (Array.isArray(parsed)) return parsed;
        }
      } catch (e) {
        console.error("Failed to parse saved execution statuses", e);
      }
    }
    return [];
  });
  const [isStatusDropdownOpen, setIsStatusDropdownOpen] = useState(false);
  const statusDropdownRef = useRef<HTMLDivElement>(null);
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState("200");
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedLog, setSelectedLog] = useState<LogEntry | null>(null);
  const [isExpanded, setIsExpanded] = useState(false);
  const [detailWidth, setDetailWidth] = useState(650);
  const [isResizing, setIsResizing] = useState(false);
  const [copied, setCopied] = useState(false);
  const [linkCopied, setLinkCopied] = useState(false);
  const [isRawExpanded, setIsRawExpanded] = useState(false);
  const [showAiModal, setShowAiModal] = useState(false);

  useEffect(() => {
    if (typeof window !== "undefined") {
      const initial = Math.max(400, Math.floor(window.innerWidth * 0.48));
      // eslint-disable-next-line react-hooks/set-state-in-effect -- window is only readable after hydration
      setDetailWidth(initial);
    }
  }, []);

  const toggleStatus = (val: string) => {
    setSelectedStatuses((prev) => {
      const exists = prev.includes(val);
      if (exists) {
        return prev.filter((s) => s !== val);
      } else {
        return [...prev, val];
      }
    });
  };

  const selectAllStatuses = () => {
    setSelectedStatuses(STATUS_OPTIONS.map((o) => o.value));
  };

  const clearAllStatuses = () => {
    setSelectedStatuses([]);
  };

  useEffect(() => {
    try {
      if (selectedStatuses.length > 0) {
        localStorage.setItem(
          "cloudo_executions_statuses",
          JSON.stringify(selectedStatuses),
        );
      } else {
        localStorage.removeItem("cloudo_executions_statuses");
      }
    } catch (e) {
      console.error("Failed to save execution statuses", e);
    }
  }, [selectedStatuses]);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (
        statusDropdownRef.current &&
        !statusDropdownRef.current.contains(e.target as Node)
      ) {
        setIsStatusDropdownOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, []);

  const setTodayDate = () => {
    const t = today(getLocalTimeZone());
    setDateValue(t);
    setPartitionKey(t.toString().replace(/-/g, ""));
  };

  const runQuery = useCallback(
    async (overrideParams?: {
      partitionKey?: string;
      execId?: string;
      statuses?: string[];
    }) => {
      setLoading(true);
      try {
        const params = new URLSearchParams();
        const pKey =
          overrideParams?.partitionKey !== undefined
            ? overrideParams.partitionKey
            : partitionKey;
        const eId =
          overrideParams?.execId !== undefined ? overrideParams.execId : execId;
        const currentStatuses =
          overrideParams?.statuses !== undefined
            ? overrideParams.statuses
            : selectedStatuses;

        if (pKey) params.set("partitionKey", pKey);
        if (eId) params.set("execId", eId);
        if (currentStatuses.length > 0)
          params.set("status", currentStatuses.join(","));
        if (query) params.set("q", query);
        if (limit) params.set("limit", limit);
        params.set("latestOnly", "true");

        const res = await cloudoFetch(`/logs/query?${params}`);
        const data = await res.json();
        const finalLogs: LogEntry[] = ((data.items || []) as LogEntry[]).sort(
          (a: LogEntry, b: LogEntry) =>
            b.RequestedAt.localeCompare(a.RequestedAt),
        );
        setLogs(finalLogs);

        // Keep detail panel aligned with the newest row for the selected execution.
        setSelectedLog((prev) => {
          if (!prev) return prev;
          const updated = finalLogs.find((l) => l.ExecId === prev.ExecId);
          return updated || prev;
        });

        // If we are looking for a specific execId via deep link, select it
        if (eId && finalLogs.length > 0) {
          const target = finalLogs.find((l) => l.ExecId === eId);
          if (target) {
            setSelectedLog(target);
            setIsRawExpanded(false);
          }
        }
      } catch (error) {
        console.error("Error fetching logs:", error);
        setLogs([]);
      } finally {
        setLoading(false);
      }
    },
    [partitionKey, execId, selectedStatuses, query, limit],
  );

  useEffect(() => {
    const initialExecId = searchParams.get("execId");
    const initialPK = searchParams.get("partitionKey");
    if (initialExecId || initialPK) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- initial query on mount
      runQuery({
        execId: initialExecId || execId,
        partitionKey: initialPK || partitionKey,
      });
    } else {
      runQuery();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // Run only on mount

  // Re-run the query shortly after any filter changes (runQuery is rebuilt).
  const filtersInitialized = useRef(false);
  useEffect(() => {
    if (!filtersInitialized.current) {
      filtersInitialized.current = true;
      return;
    }
    const timeoutId = window.setTimeout(() => runQuery(), 400);
    return () => window.clearTimeout(timeoutId);
  }, [runQuery]);

  // Merge rows pushed by the executions stream into the table
  const mergeStreamItems = useCallback(
    (items: LogEntry[]) => {
      // Free-text search is evaluated server-side
      if (query) {
        runQuery();
        return;
      }
      const matches = (l: LogEntry) =>
        (!execId || l.ExecId === execId) &&
        (selectedStatuses.length === 0 ||
          selectedStatuses.includes((l.Status || "").toLowerCase()));

      setLogs((prev) => {
        const byExecId = new Map(prev.map((l) => [l.ExecId, l]));
        for (const item of items) {
          if (matches(item)) {
            byExecId.set(item.ExecId, {
              ...byExecId.get(item.ExecId),
              ...item,
            });
          } else {
            byExecId.delete(item.ExecId);
          }
        }
        return [...byExecId.values()]
          .sort((a, b) => b.RequestedAt.localeCompare(a.RequestedAt))
          .slice(0, Number(limit) || 200);
      });
      setSelectedLog((prev) => {
        const updated = prev && items.find((i) => i.ExecId === prev.ExecId);
        return updated ? { ...prev, ...updated } : prev;
      });
    },
    [query, execId, selectedStatuses, limit, runQuery],
  );

  const executionsStream = useExecutionsStream<LogEntry>(
    partitionKey,
    true,
    mergeStreamItems,
  );

  // Fallback when the executions stream cannot be established
  useEffect(() => {
    if (executionsStream !== "unavailable") return;

    const intervalId = window.setInterval(() => {
      runQuery();
    }, 10_000);

    return () => {
      window.clearInterval(intervalId);
    };
  }, [executionsStream, runQuery]);

  const selectedStatus = (selectedLog?.Status || "").toLowerCase();
  const isSelectedLive =
    selectedStatus === "running" || selectedStatus === "accepted";
  const logStream = useExecutionLogStream<LogEntry>(
    selectedLog?.PartitionKey,
    selectedLog?.ExecId,
    isSelectedLive,
    executionsStream === "live" ? undefined : runQuery,
  );
  const detailLog =
    selectedLog && logStream.entry
      ? {
          ...selectedLog,
          ...logStream.entry,
          Log: logStream.log ?? selectedLog.Log,
        }
      : selectedLog;

  useEffect(() => {
    if (!isSelectedLive || logStream.status !== "unavailable") return;

    const intervalId = window.setInterval(() => {
      runQuery();
    }, 2_000);

    return () => {
      window.clearInterval(intervalId);
    };
  }, [isSelectedLive, logStream.status, runQuery]);

  const handleReset = () => {
    setExecId("");
    setSelectedStatuses([]);
    try {
      localStorage.removeItem("cloudo_executions_statuses");
    } catch (e) {
      console.error("Failed to clear saved execution statuses", e);
    }
    setQuery("");
    setLimit("200");
    setLogs([]);
    setSelectedLog(null);
    setTodayDate();
  };

  const handleDateChange = (val: CalendarDate | null) => {
    setDateValue(val);
    if (val) {
      const pk = val.toString().replace(/-/g, "");
      setPartitionKey(pk);
    } else {
      setPartitionKey("");
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      e.preventDefault();
      runQuery();
    }
  };

  const getStatusIcon = (status: string) => {
    const s = status.toLowerCase();
    if (s === "succeeded" || s === "completed" || s === "success")
      return <HiCheckCircle className="w-5 h-5 text-cloudo-ok" />;
    if (s === "accepted")
      return <HiPlay className="w-5 h-5 text-cloudo-accent" />;
    if (s === "running")
      return <HiArrowPath className="w-5 h-5 text-cloudo-accent" />;
    if (s === "failed" || s === "error")
      return <HiXCircle className="w-5 h-5 text-cloudo-err" />;
    if (s === "rejected")
      return <HiExclamationCircle className="w-5 h-5 text-cloudo-err" />;
    if (s === "pending" || s === "scheduled")
      return <HiClock className="w-5 h-5 text-cloudo-warn" />;
    if (s === "stopped") return <HiStop className="w-5 h-5 text-cloudo-warn" />;
    if (s === "skipped")
      return (
        <HiOutlineExclamationCircle className="w-5 h-5 text-cloudo-muted" />
      );
    if (s === "routed")
      return (
        <HiOutlineChevronDoubleRight className="w-5 h-5 text-cloudo-accent" />
      );
    return <HiOutlineTerminal className="w-5 h-5 text-cloudo-muted" />;
  };

  const getStatusBadgeClass = (status: string) => {
    const s = status.toLowerCase();
    if (s === "succeeded" || s === "completed" || s === "success")
      return "border-cloudo-ok/30 text-cloudo-ok bg-cloudo-ok/5";
    if (s === "running" || s === "accepted")
      return "border-cloudo-accent/30 text-cloudo-accent bg-cloudo-accent/5";
    if (s === "failed" || s === "error")
      return "border-cloudo-err/30 text-cloudo-err bg-cloudo-err/5";
    if (s === "rejected")
      return "border-cloudo-err/30 text-cloudo-err bg-cloudo-err/5";
    if (s === "pending" || s === "scheduled")
      return "border-cloudo-warn/30 text-cloudo-warn bg-cloudo-warn/5";
    if (s === "stopped")
      return "border-cloudo-warn/30 text-cloudo-warn bg-cloudo-warn/5";
    if (s === "skipped")
      return "border-cloudo-muted/60 text-cloudo-muted bg-cloudo-muted/5";
    if (s === "routed")
      return "border-cloudo-accent/30 text-cloudo-accent bg-cloudo-accent/5";
    return "border-cloudo-muted/60 text-cloudo-muted bg-cloudo-muted/5";
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const copyShareLink = () => {
    if (!selectedLog) return;
    const url = new URL(window.location.href);
    url.searchParams.set("execId", selectedLog.ExecId);
    url.searchParams.set("partitionKey", selectedLog.PartitionKey);
    navigator.clipboard.writeText(url.toString());
    setLinkCopied(true);
    setTimeout(() => setLinkCopied(false), 2000);
  };

  const startResizing = useCallback((e: React.MouseEvent) => {
    setIsResizing(true);
    e.preventDefault();
  }, []);

  const stopResizing = useCallback(() => {
    setIsResizing(false);
  }, []);

  const resize = useCallback(
    (e: MouseEvent) => {
      if (isResizing) {
        const newWidth = window.innerWidth - e.clientX;
        const maxWidth = Math.max(350, window.innerWidth - 300);
        if (newWidth >= 320 && newWidth <= maxWidth) {
          setDetailWidth(newWidth);
        }
      }
    },
    [isResizing],
  );

  useEffect(() => {
    if (isResizing) {
      document.body.style.userSelect = "none";
      document.body.style.cursor = "col-resize";
    } else {
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
    }
  }, [isResizing]);

  useEffect(() => {
    window.addEventListener("mousemove", resize);
    window.addEventListener("mouseup", stopResizing);
    return () => {
      window.removeEventListener("mousemove", resize);
      window.removeEventListener("mouseup", stopResizing);
    };
  }, [resize, stopResizing]);

  const getSeverityStyles = (severity: string) => {
    const s = severity?.toUpperCase();
    if (s === "SEV0" || s === "CRITICAL")
      return {
        bg: "bg-red-500/10",
        border: "border-red-500/30",
        text: "text-red-500",
        dot: "bg-red-500",
        label: s === "CRITICAL" ? "CRITICAL" : "SEV0_CRITICAL",
      };
    if (s === "SEV1")
      return {
        bg: "bg-orange-500/10",
        border: "border-orange-500/30",
        text: "text-orange-500",
        dot: "bg-orange-500",
        label: "SEV1_ERROR",
      };
    if (s === "SEV2" || s === "WARNING")
      return {
        bg: "bg-yellow-500/10",
        border: "border-yellow-500/30",
        text: "text-yellow-500",
        dot: "bg-yellow-500",
        label: s === "WARNING" ? "WARNING" : "SEV2_WARNING",
      };
    if (s === "SEV3")
      return {
        bg: "bg-cyan-500/10",
        border: "border-cyan-500/30",
        text: "text-cyan-500",
        dot: "bg-cyan-500",
        label: "SEV3_NOTICE",
      };
    if (s === "SEV4" || s === "INFO")
      return {
        bg: "bg-blue-500/10",
        border: "border-blue-500/30",
        text: "text-blue-500",
        dot: "bg-blue-500",
        label: s === "INFO" ? "INFO" : "SEV4_INFO",
      };
    return {
      bg: "bg-cloudo-ok/10",
      border: "border-cloudo-ok/30",
      text: "text-cloudo-ok",
      dot: "bg-cloudo-ok",
      label: s || "UNKNOWN",
    };
  };

  return (
    <div className="flex flex-col lg:flex-row gap-4 h-full min-w-0 bg-cloudo-dark font-mono">
      {/* Search & List Section */}
      <div
        className={`flex flex-col gap-4 h-full min-h-0 min-w-0 ${
          selectedLog ? "flex-1" : "w-full"
        }`}
      >
        {/* Filters Card */}
        <div className="bg-cloudo-panel/40 border border-cloudo-border/80 overflow-visible relative z-30">
          <div className="px-4 sm:px-6 py-4 border-b border-cloudo-border/80 flex flex-col sm:flex-row sm:items-center gap-3 sm:justify-between">
            <div className="flex items-start sm:items-center gap-3 min-w-0">
              <HiOutlineDatabase className="text-cloudo-accent w-5 h-5 shrink-0 mt-0.5 sm:mt-0" />
              <div className="min-w-0">
                <h2 className="text-xs sm:text-sm font-black uppercase tracking-[0.2em] text-cloudo-text truncate">
                  Executions Explorer
                </h2>
                <p className="text-[10px] font-black uppercase tracking-[0.22em] text-cloudo-muted/70 mt-1 truncate">
                  Search & filtering console
                </p>
              </div>
            </div>
            <button
              onClick={handleReset}
              className="self-start sm:self-auto text-[10px] sm:text-[11px] font-black uppercase tracking-widest text-cloudo-muted hover:text-cloudo-text transition-colors border border-cloudo-border/80 px-3 py-1.5 bg-cloudo-dark/20 hover:border-cloudo-accent/30"
            >
              Reset filters
            </button>
          </div>

          <div className="p-4 sm:p-6 bg-cloudo-dark/10">
            <div className="flex flex-col xl:flex-row xl:flex-wrap gap-3.5 sm:gap-4 items-stretch xl:items-end">
              <div className="space-y-2 w-full xl:flex-[2_1_340px] min-w-0 px-0.5 py-1">
                <div className="flex items-center justify-between">
                  <label className="text-[10px] font-black uppercase tracking-[0.22em] text-cloudo-muted block">
                    Telemetry_Date
                  </label>
                  <button
                    onClick={setTodayDate}
                    className="text-[9px] font-black uppercase tracking-widest text-cloudo-accent hover:text-white transition-colors"
                  >
                    [ GO_TODAY ]
                  </button>
                </div>
                <div className="flex items-center gap-1.5">
                  <button
                    onClick={() => {
                      if (dateValue) {
                        handleDateChange(dateValue.subtract({ days: 1 }));
                      }
                    }}
                    className="h-10 w-10 sm:h-11 sm:w-11 flex items-center justify-center border border-cloudo-border/70 text-cloudo-muted hover:text-cloudo-accent hover:border-cloudo-accent/40 transition-all bg-cloudo-dark/20 shrink-0"
                    title="Previous Day"
                  >
                    <HiOutlineChevronLeft className="w-3 h-3" />
                  </button>
                  <div className="relative group flex-1 min-w-0">
                    <HiOutlineCalendar className="absolute left-3 top-1/2 -translate-y-1/2 text-cloudo-muted/70 w-4 h-4 group-focus-within:text-cloudo-accent transition-colors pointer-events-none z-10" />
                    <input
                      type="date"
                      className="input input-icon pl-10 relative bg-cloudo-dark/20 border border-cloudo-border/70 text-cloudo-text w-full py-2 px-3 leading-tight focus:outline-none focus:border-cloudo-accent transition-colors block text-xs font-bold"
                      value={dateValue ? dateValue.toString() : ""}
                      onChange={(e) =>
                        handleDateChange(
                          e.target.value ? parseDate(e.target.value) : null,
                        )
                      }
                      onKeyDown={handleKeyDown}
                      onClick={(e) => e.currentTarget.showPicker?.()}
                    />
                  </div>
                  <button
                    onClick={() => {
                      if (dateValue) {
                        handleDateChange(dateValue.add({ days: 1 }));
                      }
                    }}
                    className="h-10 w-10 sm:h-11 sm:w-11 flex items-center justify-center border border-cloudo-border/70 text-cloudo-muted hover:text-cloudo-accent hover:border-cloudo-accent/40 transition-all bg-cloudo-dark/20 shrink-0"
                    title="Next Day"
                  >
                    <HiOutlineChevronRight className="w-3 h-3" />
                  </button>
                </div>
              </div>

              <div
                className="space-y-2 w-full sm:w-[calc(50%-0.625rem)] xl:flex-[1_1_210px] min-w-0 px-0.5 py-1 relative"
                ref={statusDropdownRef}
              >
                <div className="flex items-center justify-between">
                  <label className="text-[10px] font-black uppercase tracking-[0.22em] text-cloudo-muted block">
                    State
                  </label>
                  {selectedStatuses.length > 0 && (
                    <button
                      type="button"
                      onClick={clearAllStatuses}
                      className="text-[9px] font-black uppercase tracking-wider text-cloudo-accent hover:underline cursor-pointer"
                    >
                      Clear ({selectedStatuses.length})
                    </button>
                  )}
                </div>
                <div className="relative">
                  <button
                    type="button"
                    onClick={() => setIsStatusDropdownOpen((prev) => !prev)}
                    className="input input-icon pl-10 pr-8 relative w-full bg-cloudo-dark/20 border border-cloudo-border/70 focus:border-cloudo-accent text-left flex items-center justify-between cursor-pointer select-none"
                  >
                    <HiOutlineTag className="absolute left-3 top-1/2 -translate-y-1/2 text-cloudo-muted/70 w-4 h-4 group-focus-within:text-cloudo-accent transition-colors pointer-events-none z-10" />
                    <span className="truncate text-xs font-bold text-cloudo-text">
                      {selectedStatuses.length === 0
                        ? "ALL_STATES"
                        : selectedStatuses.length === 1
                          ? selectedStatuses[0].toUpperCase()
                          : `${
                              selectedStatuses.length
                            } STATES (${selectedStatuses
                              .map((s) => s.toUpperCase())
                              .join(", ")})`}
                    </span>
                    <HiOutlineChevronDown
                      className={`w-4 h-4 text-cloudo-muted transition-transform shrink-0 ${
                        isStatusDropdownOpen ? "rotate-180" : ""
                      }`}
                    />
                  </button>

                  {isStatusDropdownOpen && (
                    <div className="absolute left-0 top-full mt-1.5 w-full min-w-[260px] bg-cloudo-panel border border-cloudo-border shadow-2xl rounded-sm p-2 z-50">
                      <div className="flex items-center justify-between px-2 py-1.5 border-b border-cloudo-border/60 mb-1.5">
                        <span className="text-[9px] font-black uppercase tracking-[0.2em] text-cloudo-muted">
                          Filter by state
                        </span>
                        <div className="flex items-center gap-2">
                          <button
                            type="button"
                            onClick={selectAllStatuses}
                            className="text-[9px] font-black uppercase tracking-wider text-cloudo-muted hover:text-cloudo-accent"
                          >
                            All
                          </button>
                          <span className="text-cloudo-border text-[10px]">
                            |
                          </span>
                          <button
                            type="button"
                            onClick={clearAllStatuses}
                            className="text-[9px] font-black uppercase tracking-wider text-cloudo-muted hover:text-cloudo-accent"
                          >
                            None
                          </button>
                        </div>
                      </div>
                      <div className="space-y-1 max-h-60 overflow-y-auto custom-scrollbar pr-0.5">
                        {STATUS_OPTIONS.map((opt) => {
                          const isChecked = selectedStatuses.includes(
                            opt.value,
                          );
                          return (
                            <label
                              key={opt.value}
                              className={`flex items-center gap-2.5 px-2 py-1.5 rounded-sm cursor-pointer transition-colors text-xs font-bold ${
                                isChecked
                                  ? "bg-cloudo-accent/10 text-cloudo-text"
                                  : "hover:bg-cloudo-panel-2/70 text-cloudo-muted hover:text-cloudo-text"
                              }`}
                            >
                              <input
                                type="checkbox"
                                checked={isChecked}
                                onChange={() => toggleStatus(opt.value)}
                                className="w-3.5 h-3.5 rounded border border-cloudo-border bg-cloudo-dark/60 text-cloudo-accent focus:ring-cloudo-accent/30 focus:ring-offset-0 cursor-pointer accent-cloudo-accent"
                              />
                              <div className="flex items-center gap-1.5 min-w-0 flex-1">
                                <span className="[&_svg]:w-3.5 [&_svg]:h-3.5">
                                  {getStatusIcon(opt.value)}
                                </span>
                                <span className="text-[11px] font-black uppercase tracking-wider truncate">
                                  {opt.label}
                                </span>
                              </div>
                            </label>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>
              </div>

              <div className="space-y-2 w-full sm:w-[calc(50%-0.625rem)] xl:flex-[1_1_230px] min-w-0 px-0.5 py-1">
                <label className="text-[10px] font-black uppercase tracking-[0.22em] text-cloudo-muted block">
                  Exec_ID
                </label>
                <div className="relative group">
                  <HiOutlineFingerPrint className="absolute left-3 top-1/2 -translate-y-1/2 text-cloudo-muted/70 w-4 h-4 group-focus-within:text-cloudo-accent transition-colors pointer-events-none z-10" />
                  <input
                    type="text"
                    className="input input-icon pl-10 pr-10 relative w-full bg-cloudo-dark/20 border border-cloudo-border/70 focus:border-cloudo-accent"
                    placeholder="Execution ID..."
                    value={execId}
                    onChange={(e) => setExecId(e.target.value)}
                    onKeyDown={handleKeyDown}
                  />
                  {execId && (
                    <button
                      onClick={() => setExecId("")}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-cloudo-muted hover:text-cloudo-accent transition-colors z-20"
                    >
                      <HiOutlineX className="w-4 h-4" />
                    </button>
                  )}
                </div>
              </div>

              <div className="space-y-2 w-full xl:flex-[2_1_340px] min-w-0 px-0.5 py-1">
                <label className="text-[10px] font-black uppercase tracking-[0.22em] text-cloudo-muted block">
                  Search_Term
                </label>
                <div className="relative group">
                  <HiOutlineSearch className="absolute left-3 top-1/2 -translate-y-1/2 text-cloudo-muted/70 w-4 h-4 group-focus-within:text-cloudo-accent transition-colors pointer-events-none z-10" />
                  <input
                    type="text"
                    className="input input-icon pl-10 pr-10 relative w-full bg-cloudo-dark/20 border border-cloudo-border/70 focus:border-cloudo-accent"
                    placeholder="Keywords in logs..."
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    onKeyDown={handleKeyDown}
                  />
                  {query && (
                    <button
                      onClick={() => setQuery("")}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-cloudo-muted hover:text-cloudo-accent transition-colors z-20"
                    >
                      <HiOutlineX className="w-4 h-4" />
                    </button>
                  )}
                </div>
              </div>

              <div className="space-y-2 w-full sm:w-[calc(50%-0.625rem)] xl:flex-[1_1_130px] min-w-0 px-0.5 py-1">
                <label className="text-[10px] font-black uppercase tracking-[0.22em] text-cloudo-muted block">
                  Limit
                </label>
                <div className="relative group">
                  <HiOutlineDatabase className="absolute left-3 top-1/2 -translate-y-1/2 text-cloudo-muted/70 w-4 h-4 group-focus-within:text-cloudo-accent transition-colors pointer-events-none z-10" />
                  <input
                    type="number"
                    className="input input-icon pl-10 relative w-full bg-cloudo-dark/20 border border-cloudo-border/70 focus:border-cloudo-accent"
                    placeholder="200"
                    value={limit}
                    onChange={(e) => setLimit(e.target.value)}
                    onKeyDown={handleKeyDown}
                  />
                </div>
              </div>

              <div className="w-full sm:w-[calc(50%-0.625rem)] xl:flex-[1_1_220px] h-10 sm:h-11 xl:h-12 flex flex-col justify-end px-0.5 py-1">
                <button
                  onClick={() => runQuery()}
                  disabled={loading}
                  className="w-full btn btn-primary flex items-center justify-center gap-2 h-full"
                  onKeyDown={handleKeyDown}
                >
                  {loading ? (
                    <HiOutlineRefresh className="animate-spin w-4.5 h-4.5" />
                  ) : (
                    <HiOutlineFilter className="w-4.5 h-4.5" />
                  )}
                  {loading ? "Executing..." : "search // reload"}
                </button>
              </div>
            </div>
          </div>
        </div>

        {/* Results List Card */}
        <div className="bg-cloudo-panel border border-cloudo-border flex-1 min-h-0 min-w-0 overflow-hidden flex flex-col">
          {logs.length > 0 && (
            <div className="px-4 sm:px-6 py-2.5 border-b border-cloudo-border bg-cloudo-panel-2 flex justify-between items-center">
              <span className="text-[10px] font-black uppercase tracking-widest text-cloudo-muted/90">
                Displaying {logs.length} unique execution
                {logs.length !== 1 ? "s" : ""}
                {limit && ` (limited to ${limit} raw logs)`}
              </span>
            </div>
          )}
          <div className="overflow-x-auto overflow-y-auto flex-1 min-h-0 custom-scrollbar">
            {/* Desktop Table View */}
            <table className="hidden md:table w-full text-xs border-separate border-spacing-0">
              <thead className="bg-cloudo-panel-2/95 sticky top-0 z-10 border-b border-cloudo-border backdrop-blur-sm">
                <tr className="text-[10px] font-black text-cloudo-muted uppercase tracking-[0.2em]">
                  <th className="px-3 py-2.5 text-left">Time</th>
                  <th className="px-3 py-2.5 text-left">State</th>
                  <th className="px-3 py-2.5 text-left">Process</th>
                  <th className="px-3 py-2.5 text-left">Asset / Team</th>
                  <th className="hidden lg:table-cell px-3 py-2.5 text-left">
                    Runbook / Worker
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-cloudo-border/40">
                {logs.map((log) => (
                  <tr
                    key={log.ExecId}
                    onClick={() => {
                      setSelectedLog(log);
                      setIsRawExpanded(false);
                      setShowAiModal(false);
                    }}
                    className={`group cursor-pointer transition-all duration-200 border-l-2 hover:z-20 hover:shadow-xl ${
                      selectedLog?.ExecId === log.ExecId
                        ? "bg-cloudo-accent/12 border-cloudo-accent shadow-[inset_0_0_0_1px_rgba(66,153,225,0.25)]"
                        : "border-transparent hover:bg-cloudo-panel-2/40 odd:bg-white/[0.015]"
                    } ${
                      log.Status?.toLowerCase() === "failed" ||
                      log.Status?.toLowerCase() === "error"
                        ? "hover:border-cloudo-err/50"
                        : log.Status?.toLowerCase() === "running"
                          ? "hover:border-cloudo-accent/50"
                          : "hover:border-cloudo-muted/30"
                    }`}
                  >
                    <td className="px-3 py-2 whitespace-nowrap">
                      <div className="text-cloudo-text font-bold text-[11px]">
                        {log.RequestedAt?.split("T")[1]?.slice(0, 8)}
                      </div>
                      <div className="text-[10px] text-cloudo-accent/50 font-medium">
                        {log.RequestedAt?.split("T")[0]}
                      </div>
                    </td>
                    <td className="px-3 py-2">
                      <div
                        className={`inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 text-[9px] font-black uppercase tracking-wider [&_svg]:w-3.5 [&_svg]:h-3.5 ${getStatusBadgeClass(
                          log.Status,
                        )}`}
                        title={log.Status}
                      >
                        {getStatusIcon(log.Status)}
                        <span>{log.Status || "unknown"}</span>
                      </div>
                    </td>
                    <td className="px-3 py-2 max-w-[260px]">
                      <div className="flex flex-col gap-0.5">
                        <div className="flex items-center gap-1.5 min-w-0">
                          <span
                            className="text-cloudo-text font-bold uppercase tracking-wider truncate"
                            title={log.Name}
                          >
                            {log.Name || "SYS_TASK"}
                          </span>
                          {(log.OnCall === true || log.OnCall === "true") && (
                            <span
                              className="w-1.5 h-1.5 bg-cloudo-err animate-pulse shrink-0"
                              title="On Call"
                            />
                          )}
                        </div>
                        <div className="text-[10px] text-cloudo-muted/60 opacity-60 font-mono truncate">
                          {log.ExecId}
                        </div>
                      </div>
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex flex-col gap-0.5">
                        <div className="flex items-center gap-1.5 min-w-0">
                          <span
                            className="text-[11px] font-black text-cloudo-accent/80 truncate max-w-48 font-mono"
                            title={log.Id}
                          >
                            {log.Id || "SYSTEM"}
                          </span>
                          <span className="text-[9px] px-1 border border-cloudo-border text-cloudo-accent uppercase tracking-widest shrink-0">
                            {log.team || "default"}
                          </span>
                        </div>
                        {log.Severity && (
                          <div
                            className={`text-[9px] font-black uppercase tracking-tighter px-1 rounded-sm border inline-flex items-center gap-1 w-fit ${
                              getSeverityStyles(log.Severity).bg
                            } ${getSeverityStyles(log.Severity).border} ${
                              getSeverityStyles(log.Severity).text
                            }`}
                          >
                            <span
                              className={`w-1 h-1 rounded-full animate-pulse ${
                                getSeverityStyles(log.Severity).dot
                              }`}
                            />
                            {log.Severity}
                          </div>
                        )}
                      </div>
                    </td>
                    <td className="hidden lg:table-cell px-3 py-2 max-w-[260px]">
                      <div className="flex flex-col gap-0.5">
                        <div
                          className="text-[11px] font-mono text-cloudo-accent/70 truncate"
                          title={log.Runbook}
                        >
                          {log.Runbook}
                        </div>
                        <div className="flex items-center gap-1.5 text-[10px] font-mono text-cloudo-muted/60 min-w-0">
                          <span className="font-black uppercase tracking-widest shrink-0">
                            {log.Worker || "N/A"}
                          </span>
                          {log.Run_Args && (
                            <span className="truncate" title={log.Run_Args}>
                              {log.Run_Args}
                            </span>
                          )}
                        </div>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            {/* Mobile Card View */}
            <div className="md:hidden flex flex-col divide-y divide-cloudo-border/50">
              {logs.map((log) => (
                <div
                  key={log.ExecId}
                  onClick={() => {
                    setSelectedLog(log);
                    setIsRawExpanded(false);
                    setShowAiModal(false);
                  }}
                  className={`p-4 sm:p-5 flex flex-col gap-3 transition-all duration-200 border-l-4 ${
                    selectedLog?.ExecId === log.ExecId
                      ? "bg-cloudo-accent/12 border-cloudo-accent"
                      : "border-transparent hover:bg-cloudo-panel-2/35"
                  }`}
                >
                  <div className="flex justify-between items-start">
                    <div className="flex flex-col gap-1">
                      <div className="text-cloudo-text font-bold uppercase tracking-widest text-xs">
                        {log.Name || "SYS_TASK"}
                      </div>
                      <div className="text-[10px] text-cloudo-muted font-mono truncate max-w-50">
                        {log.ExecId}
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      {(log.OnCall === true || log.OnCall === "true") && (
                        <div
                          className="w-2 h-2 bg-cloudo-err animate-pulse rounded-full"
                          title="On Call"
                        />
                      )}
                      {getStatusIcon(log.Status)}
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-y-3 gap-x-2">
                    <div className="space-y-0.5">
                      <div className="text-[9px] font-black text-cloudo-muted uppercase tracking-widest">
                        Timestamp
                      </div>
                      <div className="text-cloudo-text font-bold text-[10px]">
                        {log.RequestedAt?.split("T")[1]?.slice(0, 8)}{" "}
                        <span className="text-cloudo-accent/50 ml-1">
                          {log.RequestedAt?.split("T")[0]}
                        </span>
                      </div>
                    </div>
                    <div className="space-y-0.5">
                      <div className="text-[9px] font-black text-cloudo-muted uppercase tracking-widest">
                        Team
                      </div>
                      <div className="text-[10px] font-black text-cloudo-accent uppercase tracking-widest truncate">
                        {log.team || "default"}
                      </div>
                    </div>
                    <div className="space-y-0.5">
                      <div className="text-[9px] font-black text-cloudo-muted uppercase tracking-widest">
                        Asset_ID
                      </div>
                      <div className="text-[10px] font-black text-cloudo-accent/80 font-mono truncate">
                        {log.Id || "SYSTEM"}
                      </div>
                    </div>
                    <div className="col-span-2 space-y-0.5">
                      <div className="text-[9px] font-black text-cloudo-muted uppercase tracking-widest">
                        Runbook
                      </div>
                      <div className="text-[10px] font-mono text-cloudo-accent/70 uppercase tracking-widest truncate">
                        {log.Runbook}
                      </div>
                    </div>
                    <div className="space-y-0.5">
                      <div className="text-[9px] font-black text-cloudo-muted uppercase tracking-widest">
                        Initiator
                      </div>
                      <div className="text-[10px] font-black text-cloudo-text uppercase tracking-widest truncate">
                        {log.Initiator || "SYSTEM"}
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center justify-between mt-1">
                    {log.Severity ? (
                      <div
                        className={`text-[9px] font-black uppercase tracking-tighter px-1.5 py-0.5 rounded-sm border inline-flex items-center gap-1.5 ${
                          getSeverityStyles(log.Severity).bg
                        } ${getSeverityStyles(log.Severity).border} ${
                          getSeverityStyles(log.Severity).text
                        }`}
                      >
                        <span
                          className={`w-1 h-1 rounded-full animate-pulse ${
                            getSeverityStyles(log.Severity).dot
                          }`}
                        />
                        {log.Severity}
                      </div>
                    ) : (
                      <div />
                    )}
                    <div className="text-[9px] font-black text-cloudo-muted uppercase tracking-widest">
                      Node:{" "}
                      <span className="text-cloudo-text">
                        {log.Worker || "N/A"}
                      </span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
            {logs.length === 0 && !loading && (
              <div className="py-20 text-center flex flex-col items-center gap-3 opacity-50">
                <HiOutlineTerminal className="w-8 h-8" />
                <span className="text-[10px] font-black uppercase tracking-widest">
                  interrogation_idle // no_data
                </span>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Detail Panel Section */}
      {detailLog && (
        <>
          {/* Resize Handle */}
          <div
            className={`hidden lg:flex w-1.5 shrink-0 bg-cloudo-border hover:bg-cloudo-accent/60 cursor-col-resize transition-colors items-center justify-center group relative select-none ${
              isResizing ? "bg-cloudo-accent" : ""
            }`}
            onMouseDown={startResizing}
          >
            <div className="absolute inset-y-0 -left-2 -right-2 z-10" />
            <div className="w-0.5 h-8 bg-cloudo-muted/40 group-hover:bg-cloudo-accent/60" />
          </div>

          <div
            className={`bg-cloudo-panel border border-cloudo-border flex flex-col shrink-0 overflow-hidden shadow-2xl ${
              isResizing ? "" : "transition-all duration-300 ease-in-out"
            } ${
              isExpanded
                ? "fixed inset-4 z-50 animate-in zoom-in-95 duration-300 overflow-y-auto custom-scrollbar ring-1 ring-cloudo-accent/20"
                : "relative rounded-l-md w-full lg:w-auto"
            }`}
            style={
              isExpanded
                ? {}
                : {
                    width: `${detailWidth}px`,
                    maxWidth: "100%",
                    minWidth: "320px",
                  }
            }
          >
            <div className="p-5 lg:p-6 border-b border-cloudo-border bg-linear-to-r from-cloudo-panel-2 to-cloudo-panel flex justify-between items-center gap-4">
              <div className="flex items-center gap-4">
                <div>
                  <h3 className="text-xs font-black text-cloudo-text uppercase tracking-[0.2em]">
                    {detailLog.Name || "Runtime Process"}
                  </h3>
                  <div className="flex items-center gap-2 mt-1 flex-wrap">
                    <code className="text-[10px] text-cloudo-muted font-mono border border-cloudo-border px-2 py-0.5 bg-cloudo-dark/40">
                      {detailLog.ExecId}
                    </code>
                    <span
                      className={`text-[9px] font-black uppercase tracking-widest px-2 py-0.5 border ${getStatusBadgeClass(
                        detailLog.Status,
                      )}`}
                    >
                      {detailLog.Status || "unknown"}
                    </span>
                  </div>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setIsExpanded(!isExpanded)}
                  className="p-2 text-cloudo-muted hover:text-cloudo-accent border border-cloudo-border transition-colors group/expand"
                  title={isExpanded ? "Collapse" : "Expand"}
                >
                  <HiOutlineArrowsExpand
                    className={`w-4 h-4 transition-transform duration-300 ${
                      isExpanded ? "rotate-180" : ""
                    }`}
                  />
                </button>
                <button
                  onClick={copyShareLink}
                  className={`flex items-center gap-2 px-3 py-1.5 border text-[10px] font-black uppercase tracking-widest transition-all ${
                    linkCopied
                      ? "bg-cloudo-ok border-cloudo-ok text-cloudo-dark"
                      : "bg-cloudo-accent/10 border-cloudo-accent/20 text-cloudo-accent hover:bg-cloudo-accent hover:text-cloudo-dark"
                  }`}
                >
                  {linkCopied ? (
                    <>
                      <HiOutlineClipboardCheck className="w-3.5 h-3.5" />
                      <span>Link_Copied</span>
                    </>
                  ) : (
                    <>
                      <HiOutlineShare className="w-3.5 h-3.5" />
                      <span>Share_Execution</span>
                    </>
                  )}
                </button>
                <button
                  onClick={() => {
                    setSelectedLog(null);
                    setIsExpanded(false);
                  }}
                  className="p-2 text-cloudo-muted hover:text-cloudo-text border border-cloudo-border transition-colors group/expand"
                >
                  <HiOutlineX className="w-4 h-4 transition-transform duration-300 group-hover/expand:rotate-90" />
                </button>
              </div>
            </div>

            <div className="flex-1 overflow-auto p-5 lg:p-6 space-y-5 custom-scrollbar bg-cloudo-dark/35">
              <div className="border border-cloudo-border bg-cloudo-dark/40 p-4 space-y-4">
                <div className="text-[10px] font-black uppercase tracking-[0.2em] text-cloudo-muted">
                  Execution Snapshot
                </div>
                <div className="grid grid-cols-2 xl:grid-cols-4 gap-2">
                  <div
                    className={`px-3 py-2 border text-[10px] font-black uppercase tracking-widest ${getStatusBadgeClass(
                      detailLog.Status,
                    )}`}
                  >
                    {detailLog.Status || "unknown"}
                  </div>
                  <div className="px-3 py-2 border border-cloudo-border text-[10px] font-black uppercase tracking-widest text-cloudo-muted">
                    Worker:{" "}
                    <span className="text-cloudo-text">
                      {detailLog.Worker || "N/A"}
                    </span>
                  </div>
                  <div className="px-3 py-2 border border-cloudo-border text-[10px] font-black uppercase tracking-widest text-cloudo-muted">
                    On Call:{" "}
                    <span
                      className={
                        detailLog.OnCall === true || detailLog.OnCall === "true"
                          ? "text-cloudo-err"
                          : "text-cloudo-text"
                      }
                    >
                      {detailLog.OnCall === true || detailLog.OnCall === "true"
                        ? "ACTIVE"
                        : "INACTIVE"}
                    </span>
                  </div>
                  <button
                    className="px-3 py-2 border border-cloudo-border text-[10px] font-black uppercase tracking-widest text-cloudo-accent hover:border-cloudo-accent/40 transition-colors text-left"
                    onClick={() => copyToClipboard(detailLog.ExecId)}
                  >
                    {copied ? "ID COPIED" : "COPY EXEC ID"}
                  </button>
                </div>

                {(detailLog.Status?.toLowerCase() === "failed" ||
                  detailLog.Status?.toLowerCase() === "error") && (
                  <button
                    onClick={() => setShowAiModal(true)}
                    className="w-full flex items-center justify-center gap-2 px-3 py-2.5 border border-cloudo-accent/30 bg-cloudo-accent/10 text-cloudo-accent text-[10px] font-black uppercase tracking-widest hover:bg-cloudo-accent hover:text-cloudo-dark transition-colors"
                  >
                    <HiOutlineSparkles className="w-3.5 h-3.5" />
                    AI Triage Analysis
                  </button>
                )}
              </div>

              <div className="border border-cloudo-border bg-cloudo-dark/40 p-4">
                <ExecutionTimeline
                  execId={detailLog.ExecId}
                  partitionKey={detailLog.PartitionKey}
                />
              </div>

              <ExecutionNotifications
                execId={detailLog.ExecId}
                partitionKey={detailLog.PartitionKey}
                status={detailLog.Status}
              />

              <div className="border border-cloudo-border bg-cloudo-dark/40 p-4 space-y-3">
                <div className="text-[10px] font-black uppercase tracking-[0.2em] text-cloudo-muted">
                  Process Identity
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                  <DetailItem
                    label="Asset_Path"
                    value={detailLog.Runbook}
                    icon={<HiOutlineTerminal className="text-cloudo-accent" />}
                  />
                  <DetailItem
                    label="Initiator"
                    value={detailLog.Initiator || "SYSTEM"}
                    icon={<HiOutlineTag />}
                  />
                  <DetailItem
                    label="Node"
                    value={detailLog.Worker || "DYNAMIC"}
                    icon={<HiOutlineDatabase />}
                  />
                  <DetailItem
                    label="Group"
                    value={detailLog.Group || "default"}
                    icon={<HiOutlineTag />}
                  />
                  <DetailItem
                    label="Team"
                    value={detailLog.team || "default"}
                    icon={<HiOutlineUsers />}
                  />
                </div>
              </div>

              <div className="border border-cloudo-border bg-cloudo-dark/40 p-4 space-y-3">
                <div className="text-[10px] font-black uppercase tracking-[0.2em] text-cloudo-muted">
                  Runtime Arguments
                </div>
                <div className="bg-cloudo-dark/70 border border-cloudo-border px-4 py-3 font-mono text-[11px] text-cloudo-accent whitespace-pre-wrap break-all leading-relaxed">
                  {detailLog.Run_Args || "EMPTY_ARGS"}
                </div>
              </div>

              {(() => {
                let info: Record<string, unknown> = {};
                if (detailLog.ResourceInfo) {
                  try {
                    const parsed = JSON.parse(detailLog.ResourceInfo);
                    if (parsed && typeof parsed === "object") {
                      info = parsed as Record<string, unknown>;
                    } else {
                      info = { _raw: detailLog.ResourceInfo };
                    }
                  } catch (e) {
                    console.warn("Failed to parse ResourceInfo:", e);
                    info = { _raw: detailLog.ResourceInfo };
                  }
                }

                const validEntries = Object.entries(info).filter(
                  // eslint-disable-next-line @typescript-eslint/no-unused-vars
                  ([__unused, v]) =>
                    v !== null && v !== undefined && String(v).trim() !== "",
                );

                if (validEntries.length > 0) {
                  return (
                    <div className="border border-cloudo-border bg-cloudo-dark/40 p-4 space-y-3">
                      <div className="text-[10px] font-black uppercase tracking-[0.2em] text-cloudo-muted">
                        Resource Info
                      </div>
                      <div className="divide-y divide-cloudo-border">
                        {validEntries.map(([k, v]) => {
                          const isRaw = k === "_raw";
                          let displayValue = String(v);

                          if (isRaw) {
                            try {
                              const parsed =
                                typeof v === "string" ? JSON.parse(v) : v;
                              displayValue = JSON.stringify(parsed, null, 2);
                            } catch {
                              // fallback
                            }
                          }

                          return (
                            <div
                              key={k}
                              className={`py-2.5 ${
                                isRaw
                                  ? "space-y-2"
                                  : "grid grid-cols-[8rem_1fr] gap-3 items-start"
                              }`}
                            >
                              <div className="text-[10px] font-black text-cloudo-muted uppercase tracking-widest">
                                {k}
                              </div>
                              {isRaw ? (
                                <>
                                  <button
                                    onClick={() =>
                                      setIsRawExpanded(!isRawExpanded)
                                    }
                                    className="text-[9px] font-black uppercase tracking-widest text-cloudo-accent hover:text-white transition-colors"
                                  >
                                    {isRawExpanded
                                      ? "[ COLLAPSE ]"
                                      : "[ EXPAND ]"}
                                  </button>
                                  {isRawExpanded && (
                                    <pre className="text-xs font-mono text-cloudo-text whitespace-pre-wrap break-all">
                                      {displayValue}
                                    </pre>
                                  )}
                                </>
                              ) : (
                                <div className="text-xs font-mono text-cloudo-text break-all text-right">
                                  {displayValue}
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  );
                }
                return null;
              })()}

              <div className="border border-cloudo-border bg-cloudo-dark/40 p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="text-[10px] font-black uppercase tracking-[0.2em] text-cloudo-muted">
                    Standard Output Stream
                  </div>
                  <button
                    onClick={() => copyToClipboard(detailLog.Log)}
                    className="flex items-center gap-2 text-[9px] font-black uppercase tracking-widest text-cloudo-accent hover:text-white transition-colors"
                    title="Copy all logs"
                  >
                    {copied ? (
                      <>
                        <HiOutlineClipboardCheck className="w-3.5 h-3.5 text-cloudo-ok" />
                        <span className="text-cloudo-ok">Copied</span>
                      </>
                    ) : (
                      <>
                        <HiOutlineClipboard className="w-3.5 h-3.5" />
                        <span>Copy Logs</span>
                      </>
                    )}
                  </button>
                </div>
                <div className="bg-cloudo-dark/80 border border-cloudo-border overflow-hidden">
                  <div className="px-4 py-2 border-b border-cloudo-border bg-cloudo-panel-2/70 flex items-center justify-between">
                    <span className="text-[9px] font-black uppercase tracking-widest text-cloudo-muted/90">
                      {detailLog.Log
                        ? `${
                            detailLog.Log.split("\n").filter(Boolean).length
                          } linee`
                        : "0 linee"}
                    </span>
                    {logStream.status === "live" ? (
                      <span className="flex items-center gap-1.5 text-[9px] font-black uppercase tracking-widest text-cloudo-accent">
                        <span className="w-1.5 h-1.5 rounded-full bg-cloudo-accent animate-pulse" />
                        live stream
                      </span>
                    ) : (
                      <span className="text-[9px] font-black uppercase tracking-widest text-cloudo-muted/60">
                        terminal stream
                      </span>
                    )}
                  </div>
                  <ExecutionLogOutput content={detailLog.Log} />
                </div>
              </div>
            </div>
          </div>

          {showAiModal && (
            <AiAnalysisModal
              execId={detailLog.ExecId}
              onClose={() => setShowAiModal(false)}
            />
          )}
        </>
      )}
    </div>
  );
}

function ExecutionTimeline({
  execId,
  partitionKey,
}: {
  execId: string;
  partitionKey: string;
}) {
  const [timelineLogs, setTimelineLogs] = useState<LogEntry[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const fetchTimeline = async () => {
      setLoading(true);
      try {
        const params = new URLSearchParams();
        params.set("partitionKey", partitionKey);
        params.set("execId", execId);
        params.set("limit", "100");
        params.set("latestOnly", "false");

        const res = await cloudoFetch(`/logs/query?${params}`);
        const data = await res.json();

        // Sort all logs chronologically for this execution by time and priority
        const allLogs = (data.items || []).sort((a: LogEntry, b: LogEntry) => {
          const priorityDiff =
            (statusPriority[a.Status?.toLowerCase()] ?? 0) -
            (statusPriority[b.Status?.toLowerCase()] ?? 0);
          if (priorityDiff !== 0) return priorityDiff;
          return a.RequestedAt.localeCompare(b.RequestedAt);
        });

        setTimelineLogs(allLogs);
      } catch (error) {
        console.error("Failed to fetch timeline logs:", error);
      } finally {
        setLoading(false);
      }
    };

    if (execId && partitionKey) {
      fetchTimeline();
    }
  }, [execId, partitionKey]);

  const getStatusTone = (status: string) => {
    const s = status.toLowerCase();
    if (s === "accepted")
      return "border-cloudo-accent/40 bg-cloudo-accent/8 text-cloudo-accent";
    if (s === "running")
      return "border-yellow-500/40 bg-yellow-500/8 text-yellow-400";
    if (s === "succeeded" || s === "completed")
      return "border-cloudo-ok/40 bg-cloudo-ok/8 text-cloudo-ok";
    if (s === "failed" || s === "error")
      return "border-cloudo-err/40 bg-cloudo-err/8 text-cloudo-err";
    return "border-cloudo-border bg-cloudo-dark/40 text-cloudo-muted";
  };

  const getStatusIcon = (status: string) => {
    const s = status.toLowerCase();
    if (s === "accepted") return <HiOutlineInbox className="w-3 h-3" />;
    if (s === "running") return <HiPlay className="w-3 h-3" />;
    if (s === "succeeded" || s === "completed")
      return <HiOutlineCheckCircle className="w-3 h-3" />;
    if (s === "failed" || s === "error")
      return <HiOutlineExclamationCircle className="w-3 h-3" />;
    return <HiOutlineInformationCircle className="w-3 h-3" />;
  };

  if (loading) {
    return (
      <div className="space-y-3">
        <div className="text-[10px] font-black uppercase tracking-[0.2em] text-cloudo-muted">
          Execution Timeline
        </div>
        <div className="border border-cloudo-border bg-cloudo-dark/55 px-3 py-2 text-[11px] text-cloudo-muted">
          Loading timeline...
        </div>
      </div>
    );
  }

  if (timelineLogs.length === 0) {
    return null;
  }

  return (
    <div className="space-y-3">
      <div className="text-[10px] font-black uppercase tracking-[0.2em] text-cloudo-muted">
        Execution Timeline
      </div>

      <div className="space-y-0">
        {timelineLogs.map((log, idx) => {
          const timestamp = log.RequestedAt || "UNKNOWN";
          const [datePart, timePartRaw] = timestamp.split("T");
          const timePart = timePartRaw?.slice(0, 8) || timestamp;

          return (
            <div key={`${log.ExecId}-${log.RowKey}`} className="relative pl-12">
              {idx < timelineLogs.length - 1 && (
                <div className="absolute left-[1.15rem] top-8 bottom-[-0.35rem] w-px bg-cloudo-border" />
              )}

              <div className="absolute left-0 top-1 flex items-center justify-center w-9 h-9 border border-cloudo-border bg-cloudo-dark/70 text-[10px] font-black text-cloudo-muted">
                {String(idx + 1).padStart(2, "0")}
              </div>

              <div
                className={`mb-2 border px-3 py-2 ${getStatusTone(
                  log.Status || "",
                )}`}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="shrink-0">
                      {getStatusIcon(log.Status || "")}
                    </span>
                    <span className="text-[10px] font-black uppercase tracking-widest truncate">
                      {log.Status || "UNKNOWN"}
                    </span>
                  </div>
                  <span className="text-[10px] font-bold text-cloudo-muted whitespace-nowrap">
                    {timePart}
                  </span>
                </div>
                <div className="mt-1 text-[10px] text-cloudo-muted/80 font-mono">
                  {datePart || timestamp}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

interface NotificationTarget {
  type: string;
  team?: string;
  channel?: string;
  status: string;
  fallback?: boolean;
  error?: string;
}

interface NotificationBatch {
  key: string;
  status: string;
  requestedAt: string;
  reason?: string | null;
  targets: NotificationTarget[];
}

const notificationReasonLabel: Record<string, string> = {
  matched: "Matched routing rule",
  fallback_jsm: "No rule matched // JSM fallback",
  approval_request: "Approval request",
};

function ExecutionNotifications({
  execId,
  partitionKey,
  status,
}: {
  execId: string;
  partitionKey: string;
  status: string;
}) {
  const [batches, setBatches] = useState<NotificationBatch[]>([]);

  useEffect(() => {
    let cancelled = false;
    const fetchNotifications = async () => {
      try {
        const params = new URLSearchParams();
        params.set("partitionKey", partitionKey);
        params.set("execId", execId);
        params.set("limit", "100");
        params.set("latestOnly", "false");
        params.set("includeLogContent", "false");
        const res = await cloudoFetch(`/logs/query?${params}`);
        const data = await res.json();
        const next: NotificationBatch[] = [];
        ((data.items || []) as LogEntry[])
          .sort((a, b) => a.RequestedAt.localeCompare(b.RequestedAt))
          .forEach((row) => {
            if (!row.Notifications) return;
            try {
              const parsed = JSON.parse(row.Notifications);
              if (Array.isArray(parsed?.targets) && parsed.targets.length > 0) {
                next.push({
                  key: row.RowKey,
                  status: row.Status,
                  requestedAt: row.RequestedAt,
                  reason: parsed.reason,
                  targets: parsed.targets,
                });
              }
            } catch {
              // ignore malformed notification metadata
            }
          });
        if (!cancelled) setBatches(next);
      } catch (error) {
        console.error("Failed to fetch notifications:", error);
      }
    };
    fetchNotifications();
    return () => {
      cancelled = true;
    };
  }, [execId, partitionKey, status]);

  return (
    <div className="border border-cloudo-border bg-cloudo-dark/40 p-4 space-y-3">
      <div className="text-[10px] font-black uppercase tracking-[0.2em] text-cloudo-muted">
        Notifications
      </div>
      {batches.length === 0 ? (
        <div className="text-[11px] text-cloudo-muted/70 uppercase tracking-widest">
          No notification sent for this execution
        </div>
      ) : (
        <div className="space-y-3">
          {batches.map((batch) => (
            <div key={batch.key} className="border border-cloudo-border">
              <div className="px-3 py-2 bg-cloudo-panel-2/60 border-b border-cloudo-border flex flex-wrap items-center justify-between gap-2">
                <span className="text-[10px] font-black uppercase tracking-widest text-cloudo-text">
                  On {batch.status}
                  {batch.reason
                    ? ` // ${
                        notificationReasonLabel[batch.reason] || batch.reason
                      }`
                    : ""}
                </span>
                <span className="text-[10px] font-mono text-cloudo-muted">
                  {batch.requestedAt?.split("T")[1]?.slice(0, 8)}
                </span>
              </div>
              <div className="divide-y divide-cloudo-border/40">
                {batch.targets.map((target, index) => (
                  <div
                    key={`${batch.key}-${index}`}
                    className="px-3 py-2 grid grid-cols-[5rem_1fr_auto] gap-3 items-center text-[11px]"
                  >
                    <span className="font-black uppercase tracking-widest text-cloudo-accent">
                      {target.type === "jsm" ? "JSM" : "Slack"}
                    </span>
                    <div className="min-w-0 font-mono text-cloudo-text truncate">
                      <span className="uppercase">
                        {target.team || "default"}
                      </span>
                      {target.channel && (
                        <span className="text-cloudo-muted">
                          {" "}
                          {target.channel}
                        </span>
                      )}
                      {target.fallback && (
                        <span className="text-cloudo-warn"> fallback</span>
                      )}
                      {target.error && (
                        <div
                          className="text-[10px] text-cloudo-err truncate"
                          title={target.error}
                        >
                          {target.error}
                        </div>
                      )}
                    </div>
                    <span
                      className={`px-2 py-0.5 border text-[10px] font-black uppercase tracking-widest ${
                        target.status === "sent"
                          ? "border-cloudo-ok/30 text-cloudo-ok bg-cloudo-ok/5"
                          : "border-cloudo-err/30 text-cloudo-err bg-cloudo-err/5"
                      }`}
                    >
                      {target.status}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

interface AiAnalysisResponse {
  status: "processing" | "completed" | "error" | "not_found";
  analysis: {
    summary?: string;
    probable_root_cause?: string;
    recommended_actions?: string[];
    confidence?: string;
  } | null;
  error: string | null;
  updated_at: string | null;
}

const AI_ANALYSIS_POLL_MS = 4000;
const AI_ANALYSIS_MAX_POLLS = 30; // ~2 minutes before giving up on auto-refresh

function AiAnalysisModal({
  execId,
  onClose,
}: {
  execId: string;
  onClose: () => void;
}) {
  const [data, setData] = useState<AiAnalysisResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [failedToLoad, setFailedToLoad] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let attempts = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const fetchAnalysis = async () => {
      try {
        const res = await cloudoFetch(
          `/ai-analysis/${encodeURIComponent(execId)}`,
        );
        if (cancelled) return;
        if (!res.ok) {
          setFailedToLoad(true);
          setLoading(false);
          return;
        }
        const body: AiAnalysisResponse = await res.json();
        setData(body);
        setFailedToLoad(false);
        setLoading(false);

        attempts += 1;
        if (body.status === "processing" && attempts < AI_ANALYSIS_MAX_POLLS) {
          timer = setTimeout(fetchAnalysis, AI_ANALYSIS_POLL_MS);
        }
      } catch {
        if (!cancelled) {
          setFailedToLoad(true);
          setLoading(false);
        }
      }
    };

    fetchAnalysis();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [execId]);

  const confidenceTone = (confidence?: string) => {
    const c = (confidence || "").toLowerCase();
    if (c === "high")
      return "text-cloudo-ok border-cloudo-ok/40 bg-cloudo-ok/10";
    if (c === "medium")
      return "text-yellow-400 border-yellow-500/40 bg-yellow-500/10";
    return "text-cloudo-muted border-cloudo-border bg-cloudo-dark/40";
  };

  return (
    <div className="fixed inset-0 z-70 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="w-full max-w-xl max-h-[85vh] overflow-auto custom-scrollbar bg-cloudo-panel border border-cloudo-border shadow-2xl animate-in zoom-in-95 duration-200">
        <div className="flex items-center justify-between gap-4 px-5 py-4 border-b border-cloudo-border bg-linear-to-r from-cloudo-panel-2 to-cloudo-panel">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-cloudo-accent/10 border border-cloudo-accent/20">
              <HiOutlineSparkles className="w-4 h-4 text-cloudo-accent" />
            </div>
            <div>
              <h3 className="text-xs font-black text-cloudo-text uppercase tracking-[0.2em]">
                AI Triage Analysis
              </h3>
              <code className="text-[10px] text-cloudo-muted font-mono">
                {execId}
              </code>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-2 text-cloudo-muted hover:text-cloudo-text border border-cloudo-border transition-colors"
          >
            <HiOutlineX className="w-4 h-4" />
          </button>
        </div>

        <div className="p-5 space-y-4">
          {loading && !data && (
            <div className="flex items-center gap-3 text-[11px] font-bold uppercase tracking-widest text-cloudo-muted py-6 justify-center">
              <HiOutlineRefresh className="w-4 h-4 animate-spin" />
              Loading...
            </div>
          )}

          {!loading && failedToLoad && !data && (
            <div className="flex items-center gap-3 text-[11px] font-bold uppercase tracking-widest text-cloudo-err py-6 justify-center">
              <HiOutlineExclamationCircle className="w-4 h-4" />
              Failed to load AI analysis
            </div>
          )}

          {data && data.status === "not_found" && (
            <div className="flex flex-col items-center gap-2 text-center py-6">
              <HiOutlineInformationCircle className="w-6 h-6 text-cloudo-muted" />
              <p className="text-[11px] font-bold uppercase tracking-widest text-cloudo-muted">
                No AI analysis available for this execution
              </p>
              <p className="text-[10px] text-cloudo-muted/70 uppercase tracking-tight max-w-sm">
                AI Agent triage may be disabled, or this execution was not
                forwarded for analysis.
              </p>
            </div>
          )}

          {data && data.status === "processing" && (
            <div className="flex flex-col items-center gap-3 text-center py-6">
              <HiOutlineRefresh className="w-6 h-6 text-cloudo-accent animate-spin" />
              <p className="text-[11px] font-bold uppercase tracking-widest text-cloudo-accent">
                Processing...
              </p>
              <p className="text-[10px] text-cloudo-muted/70 uppercase tracking-tight max-w-sm">
                The AI Agent is analyzing this execution. This panel refreshes
                automatically.
              </p>
            </div>
          )}

          {data && (data.status === "completed" || data.status === "error") && (
            <div className="space-y-4">
              {data.analysis?.confidence && (
                <div className="flex items-center justify-between">
                  <span className="text-[10px] font-black uppercase tracking-[0.2em] text-cloudo-muted">
                    Confidence
                  </span>
                  <span
                    className={`text-[10px] font-black uppercase tracking-widest px-2 py-0.5 border ${confidenceTone(
                      data.analysis.confidence,
                    )}`}
                  >
                    {data.analysis.confidence}
                  </span>
                </div>
              )}

              {data.analysis?.summary && (
                <div className="space-y-1.5">
                  <div className="text-[10px] font-black uppercase tracking-[0.2em] text-cloudo-muted">
                    Summary
                  </div>
                  <p className="text-[12px] text-cloudo-text leading-relaxed whitespace-pre-wrap">
                    {data.analysis.summary}
                  </p>
                </div>
              )}

              {data.analysis?.probable_root_cause && (
                <div className="space-y-1.5">
                  <div className="text-[10px] font-black uppercase tracking-[0.2em] text-cloudo-muted">
                    Probable Root Cause
                  </div>
                  <p className="text-[12px] text-cloudo-text leading-relaxed whitespace-pre-wrap">
                    {data.analysis.probable_root_cause}
                  </p>
                </div>
              )}

              {!!data.analysis?.recommended_actions?.length && (
                <div className="space-y-1.5">
                  <div className="text-[10px] font-black uppercase tracking-[0.2em] text-cloudo-muted">
                    Recommended Actions
                  </div>
                  <ul className="space-y-1.5">
                    {data.analysis.recommended_actions.map((action, i) => (
                      <li
                        key={i}
                        className="flex items-start gap-2 text-[12px] text-cloudo-text leading-relaxed"
                      >
                        <HiOutlineLightBulb className="w-3.5 h-3.5 text-cloudo-accent shrink-0 mt-0.5" />
                        <span>{action}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {data.status === "error" && (
                <div className="border border-cloudo-err/30 bg-cloudo-err/10 px-4 py-3 text-[11px] text-cloudo-err">
                  {data.error || "AI analysis failed."}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function DetailItem({
  label,
  value,
  icon,
  className = "",
}: {
  label: string;
  value: string;
  icon: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`bg-cloudo-dark/55 border border-cloudo-border px-3 py-2 space-y-1.5 overflow-hidden hover:border-cloudo-accent/40 transition-colors ${className}`}
    >
      <div className="flex items-center gap-2 text-cloudo-muted/70">
        <span className="text-sm text-cloudo-accent/80">{icon}</span>
        <span className="text-[10px] font-black uppercase tracking-widest whitespace-nowrap">
          {label}
        </span>
      </div>
      <div
        className="text-[11px] font-bold text-cloudo-text truncate uppercase tracking-tighter hover:whitespace-normal hover:break-all transition-all"
        title={value}
      >
        {value}
      </div>
    </div>
  );
}

function ExecutionLogOutput({ content }: { content: string }) {
  if (!content) {
    return (
      <div className="px-5 py-8 text-center">
        <span className="italic text-cloudo-muted opacity-30 text-xs">
          No log data available
        </span>
      </div>
    );
  }

  const lines = content.split("\n").filter((line) => line.trim() !== "");

  return (
    <div className="max-h-[26rem] overflow-auto custom-scrollbar font-mono text-xs">
      {lines.map((line, index) => {
        const upper = line.toUpperCase();
        const tone =
          upper.includes("ERROR") || upper.includes("EXCEPTION")
            ? "text-cloudo-err"
            : upper.includes("WARN")
              ? "text-cloudo-warn"
              : upper.includes("INFO")
                ? "text-cloudo-accent"
                : "text-cloudo-text/85";

        return (
          <div
            key={`${index}-${line.slice(0, 12)}`}
            className="grid grid-cols-[3.5rem_1fr] gap-3 px-4 py-1.5 border-b border-cloudo-border/40 hover:bg-cloudo-panel-2/40 transition-colors"
          >
            <span className="text-[10px] text-cloudo-muted/70 text-right select-none">
              {String(index + 1).padStart(4, "0")}
            </span>
            <span className={`${tone} break-all leading-relaxed`}>{line}</span>
          </div>
        );
      })}
    </div>
  );
}
