"use client";

import { useState, useEffect, useCallback, Suspense } from "react";
import { cloudoFetch } from "@/lib/api";
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
  ResourceInfo?: string;
}

const statusPriority: Record<string, number> = {
  succeeded: 5,
  completed: 5,
  failed: 4,
  error: 4,
  running: 3,
  skipped: 3,
  rejected: 3,
  stopped: 3,
  accepted: 2,
  pending: 1,
};

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
  const [status, setStatus] = useState("");
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState("200");
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedLog, setSelectedLog] = useState<LogEntry | null>(null);
  const [isExpanded, setIsExpanded] = useState(false);
  const [detailWidth, setDetailWidth] = useState(
    typeof window !== "undefined" ? Math.floor(window.innerWidth / 2) : 600,
  ); // Start at half-screen width
  const [isResizing, setIsResizing] = useState(false);
  const [copied, setCopied] = useState(false);
  const [linkCopied, setLinkCopied] = useState(false);
  const [isRawExpanded, setIsRawExpanded] = useState(false);

  const setTodayDate = () => {
    const t = today(getLocalTimeZone());
    setDateValue(t);
    setPartitionKey(t.toString().replace(/-/g, ""));
  };

  const runQuery = useCallback(
    async (overrideParams?: { partitionKey?: string; execId?: string }) => {
      setLoading(true);
      try {
        const params = new URLSearchParams();
        const pKey =
          overrideParams?.partitionKey !== undefined
            ? overrideParams.partitionKey
            : partitionKey;
        const eId =
          overrideParams?.execId !== undefined ? overrideParams.execId : execId;

        if (pKey) params.set("partitionKey", pKey);
        if (eId) params.set("execId", eId);
        if (status) params.set("status", status);
        if (query) params.set("q", query);
        if (limit) params.set("limit", limit);

        const res = await cloudoFetch(`/logs/query?${params}`);
        const data = await res.json();
        const rawLogs = data.items || [];

        // Group by ExecId and keep the final status (highest priority wins)
        const groupedByExecId = new Map<string, LogEntry>();

        rawLogs.forEach((log: LogEntry) => {
          const logExecId = log.ExecId;
          const existing = groupedByExecId.get(logExecId);

          if (!existing) {
            groupedByExecId.set(logExecId, log);
          } else {
            // Keep the entry with higher priority status
            const currentPriority =
              statusPriority[log.Status?.toLowerCase()] || 0;
            const existingPriority =
              statusPriority[existing.Status?.toLowerCase()] || 0;

            if (currentPriority > existingPriority) {
              groupedByExecId.set(logExecId, log);
            } else if (currentPriority === existingPriority) {
              // If same priority, keep the most recent
              if (log.RequestedAt > existing.RequestedAt) {
                groupedByExecId.set(logExecId, log);
              }
            }
          }
        });

        const finalLogs = Array.from(groupedByExecId.values()).sort((a, b) =>
          b.RequestedAt.localeCompare(a.RequestedAt),
        );
        setLogs(finalLogs);

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
    [partitionKey, execId, status, query, limit],
  );

  useEffect(() => {
    const initialExecId = searchParams.get("execId");
    const initialPK = searchParams.get("partitionKey");
    if (initialExecId || initialPK) {
      runQuery({
        execId: initialExecId || execId,
        partitionKey: initialPK || partitionKey,
      });
    } else {
      runQuery();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // Run only on mount

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      runQuery();
    }, 10_000);

    return () => {
      window.clearInterval(intervalId);
    };
  }, [runQuery]);

  const handleReset = () => {
    setExecId("");
    setStatus("");
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
      runQuery({ partitionKey: pk });
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
    if (s === "succeeded" || s === "completed")
      return <HiCheckCircle className="w-4 h-4 text-cloudo-ok" />;
    if (s === "accepted")
      return <HiPlay className="w-4 h-4 text-cloudo-accent" />;
    if (s === "running")
      return <HiArrowPath className="w-4 h-4 text-cloudo-accent" />;
    if (s === "failed" || s === "error")
      return <HiXCircle className="w-4 h-4 text-cloudo-err" />;
    if (s === "rejected")
      return <HiExclamationCircle className="w-4 h-4 text-cloudo-err" />;
    if (s === "pending")
      return <HiClock className="w-4 h-4 text-cloudo-warn" />;
    if (s === "stopped") return <HiStop className="w-4 h-4 text-cloudo-warn" />;
    if (s === "routed")
      return (
        <HiOutlineChevronDoubleRight className="w-4 h-4 text-cloudo-accent" />
      );
    return <HiOutlineTerminal className="w-4 h-4 text-cloudo-muted" />;
  };

  const getStatusBadgeClass = (status: string) => {
    const s = status.toLowerCase();
    if (s === "succeeded" || s === "completed")
      return "border-cloudo-ok/30 text-cloudo-ok bg-cloudo-ok/5";
    if (s === "running" || s === "accepted")
      return "border-cloudo-accent/30 text-cloudo-accent bg-cloudo-accent/5";
    if (s === "failed" || s === "error")
      return "border-cloudo-err/30 text-cloudo-err bg-cloudo-err/5";
    if (s === "rejected")
      return "border-cloudo-err/30 text-cloudo-err bg-cloudo-err/5";
    if (s === "pending")
      return "border-cloudo-warn/30 text-cloudo-warn bg-cloudo-warn/5";
    if (s === "stopped")
      return "border-cloudo-warn/30 text-cloudo-warn bg-cloudo-warn/5";
    if (s === "routed")
      return "border-cloudo-accent/30 text-cloudo-accent bg-cloudo-accent/5";
    return "border-cloudo-muted/60 text-cloudo-muted bg-cloudo-muted/5";
  };

  const formatLogContent = (content: string) => {
    if (!content)
      return (
        <span className="italic text-cloudo-muted opacity-20">
          No log data available
        </span>
      );
    return content.split("\n").map((line, i) => {
      let color = "text-cloudo-text/80";
      if (
        line.toUpperCase().includes("ERROR") ||
        line.toUpperCase().includes("EXCEPTION")
      )
        color = "text-red-600";
      if (line.toUpperCase().includes("WARN")) color = "text-yellow-600";
      if (line.toUpperCase().includes("INFO")) color = "text-blue-600";
      return (
        <div
          key={i}
          className={`${color} font-mono text-xs leading-relaxed py-1 border-b border-white/2 break-all`}
        >
          {line}
        </div>
      );
    });
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
        if (newWidth > 300 && newWidth < window.innerWidth * 0.8) {
          setDetailWidth(newWidth);
        }
      }
    },
    [isResizing],
  );

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
    <div className="flex flex-col lg:flex-row gap-4 h-full bg-cloudo-dark font-mono">
      {/* Search & List Section */}
      <div
        className="flex flex-col gap-4 overflow-hidden h-full"
        style={{
          flex: selectedLog ? "1" : "none",
          width: selectedLog ? "auto" : "100%",
        }}
      >
        {/* Filters Card */}
        <div className="bg-cloudo-panel border border-cloudo-border shadow-none">
          <div className="px-6 py-4 border-b border-cloudo-border flex justify-between items-center bg-cloudo-panel-2">
            <div className="flex items-center gap-3 shrink-0">
              <HiOutlineDatabase className="text-cloudo-accent w-5 h-5 shrink-0" />
              <h2 className="text-sm font-black uppercase tracking-[0.2em] text-cloudo-text truncate">
                Executions Explorer
              </h2>
            </div>
            <button
              onClick={handleReset}
              className="text-[11px] font-black uppercase tracking-widest text-cloudo-muted hover:text-cloudo-text transition-colors border border-cloudo-border px-2 py-1"
            >
              Reset
            </button>
          </div>

          <div className="p-6">
            <div className="flex flex-wrap gap-x-6 gap-y-6 items-end">
              <div className="space-y-2 flex-1 min-w-70">
                <div className="flex items-center justify-between ml-1">
                  <label className="text-[11px] font-black uppercase tracking-widest text-cloudo-muted block">
                    Telemetry_Date
                  </label>
                  <button
                    onClick={setTodayDate}
                    className="text-[9px] font-black uppercase tracking-tighter text-cloudo-accent hover:text-white transition-colors"
                  >
                    [ GO_TODAY ]
                  </button>
                </div>
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => {
                      if (dateValue) {
                        handleDateChange(dateValue.subtract({ days: 1 }));
                      }
                    }}
                    className="p-2 border border-cloudo-border text-cloudo-muted hover:text-cloudo-accent hover:border-cloudo-accent/40 transition-all bg-cloudo-dark/30 shrink-0"
                    title="Previous Day"
                  >
                    <HiOutlineChevronLeft className="w-3 h-3" />
                  </button>
                  <div className="relative group flex-1 min-w-0">
                    <HiOutlineCalendar className="absolute left-3 top-1/2 -translate-y-1/2 text-cloudo-muted/70 w-4 h-4 group-focus-within:text-cloudo-accent transition-colors pointer-events-none z-10" />
                    <input
                      type="date"
                      className="input input-icon pl-10 relative bg-transparent border border-cloudo-border text-cloudo-text w-full py-2 px-3 leading-tight focus:outline-none focus:border-cloudo-accent transition-colors block text-xs font-bold"
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
                    className="p-2 border border-cloudo-border text-cloudo-muted hover:text-cloudo-accent hover:border-cloudo-accent/40 transition-all bg-cloudo-dark/30 shrink-0"
                    title="Next Day"
                  >
                    <HiOutlineChevronRight className="w-3 h-3" />
                  </button>
                </div>
              </div>

              <div className="space-y-2 flex-1 min-w-40">
                <label className="text-[11px] font-black uppercase tracking-widest text-cloudo-muted ml-1 block">
                  State
                </label>
                <div className="relative group">
                  <HiOutlineTag className="absolute left-3 top-1/2 -translate-y-1/2 text-cloudo-muted/70 w-4 h-4 group-focus-within:text-cloudo-accent transition-colors pointer-events-none z-10" />
                  <select
                    className="input input-icon pl-10 appearance-none relative w-full"
                    value={status}
                    onChange={(e) => setStatus(e.target.value)}
                    onKeyDown={handleKeyDown}
                  >
                    <option value="">ALL_EVENTS</option>
                    <option value="pending">PENDING</option>
                    <option value="accepted">ACCEPTED</option>
                    <option value="running">RUNNING</option>
                    <option value="succeeded">SUCCEEDED</option>
                    <option value="failed">FAILED</option>
                    <option value="rejected">REJECTED</option>
                    <option value="error">ERROR</option>
                  </select>
                </div>
              </div>

              <div className="space-y-2 flex-1 min-w-50">
                <label className="text-[11px] font-black uppercase tracking-widest text-cloudo-muted ml-1 block">
                  Exec_ID
                </label>
                <div className="relative group">
                  <HiOutlineFingerPrint className="absolute left-3 top-1/2 -translate-y-1/2 text-cloudo-muted/70 w-4 h-4 group-focus-within:text-cloudo-accent transition-colors pointer-events-none z-10" />
                  <input
                    type="text"
                    className="input input-icon pl-10 pr-10 relative w-full"
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

              <div className="space-y-2 flex-2 min-w-70">
                <label className="text-[11px] font-black uppercase tracking-widest text-cloudo-muted ml-1 block">
                  Search_Term
                </label>
                <div className="relative group">
                  <HiOutlineSearch className="absolute left-3 top-1/2 -translate-y-1/2 text-cloudo-muted/70 w-4 h-4 group-focus-within:text-cloudo-accent transition-colors pointer-events-none z-10" />
                  <input
                    type="text"
                    className="input input-icon pl-10 pr-10 relative w-full"
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

              <div className="space-y-2 flex-1 min-w-30">
                <label className="text-[11px] font-black uppercase tracking-widest text-cloudo-muted ml-1 block">
                  Limit
                </label>
                <div className="relative group">
                  <HiOutlineDatabase className="absolute left-3 top-1/2 -translate-y-1/2 text-cloudo-muted/70 w-4 h-4 group-focus-within:text-cloudo-accent transition-colors pointer-events-none z-10" />
                  <input
                    type="number"
                    className="input input-icon pl-10 relative w-full"
                    placeholder="200"
                    value={limit}
                    onChange={(e) => setLimit(e.target.value)}
                    onKeyDown={handleKeyDown}
                  />
                </div>
              </div>

              <div className="flex-2 min-w-45 h-12 flex flex-col justify-end">
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
        <div className="bg-cloudo-panel border border-cloudo-border flex-1 overflow-hidden flex flex-col">
          {logs.length > 0 && (
            <div className="px-6 py-2 border-b border-cloudo-border bg-cloudo-panel-2 flex justify-between items-center">
              <span className="text-[10px] font-black uppercase tracking-widest text-cloudo-muted">
                Displaying {logs.length} unique execution
                {logs.length !== 1 ? "s" : ""}
                {limit && ` (limited to ${limit} raw logs)`}
              </span>
            </div>
          )}
          <div className="overflow-x-auto overflow-y-auto custom-scrollbar">
            {/* Desktop Table View */}
            <table className="hidden md:table w-full text-xs border-collapse min-w-200">
              <thead className="bg-cloudo-panel-2 sticky top-0 z-10 border-b border-cloudo-border">
                <tr className="text-[10px] font-black text-cloudo-muted uppercase tracking-[0.3em]">
                  <th className="px-4 py-4 text-left min-w-30">Timestamp</th>
                  <th className="px-4 py-4 text-center w-15">State</th>
                  <th className="px-4 py-4 text-left min-w-45">
                    Process_Context
                  </th>
                  <th className="px-4 py-4 text-left min-w-35">Asset_ID</th>
                  <th className="px-4 py-4 text-left min-w-50">
                    Execution_Details
                  </th>
                  <th className="px-4 py-4 text-left min-w-25">Worker</th>
                  <th className="px-4 py-4 text-center w-10">On Call</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-cloudo-border/50">
                {logs.map((log) => (
                  <tr
                    key={log.ExecId}
                    onClick={() => {
                      setSelectedLog(log);
                      setIsRawExpanded(false);
                    }}
                    className={`group cursor-pointer transition-all duration-200 border-l-2 hover:z-20 hover:shadow-xl ${
                      selectedLog?.ExecId === log.ExecId
                        ? "bg-cloudo-accent/10 border-cloudo-accent"
                        : "border-transparent hover:bg-white/3 odd:bg-white/1"
                    } ${
                      log.Status?.toLowerCase() === "failed" ||
                      log.Status?.toLowerCase() === "error"
                        ? "hover:border-cloudo-err/50"
                        : log.Status?.toLowerCase() === "running"
                          ? "hover:border-cloudo-accent/50"
                          : "hover:border-cloudo-muted/30"
                    }`}
                  >
                    <td className="px-4 py-4 whitespace-nowrap">
                      <div className="text-cloudo-text font-bold text-[11px]">
                        {log.RequestedAt?.split("T")[1]?.slice(0, 8)}
                      </div>
                      <div className="text-[10px] text-cloudo-accent/50 font-medium">
                        {log.RequestedAt?.split("T")[0]}
                      </div>
                    </td>
                    <td className="px-4 py-4">
                      <div className="flex justify-center" title={log.Status}>
                        {getStatusIcon(log.Status)}
                      </div>
                    </td>
                    <td className="px-4 py-4">
                      <div className="flex flex-col gap-1">
                        <div className="flex items-center gap-2">
                          <div className="text-cloudo-text font-bold uppercase tracking-widest">
                            {log.Name || "SYS_TASK"}
                          </div>
                        </div>
                        <div className="text-[10px] text-cloudo-muted/60 opacity-50 font-mono break-all">
                          {log.ExecId}
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-4">
                      <div className="flex flex-col gap-0.5 group/cell">
                        <div className="text-[11px] font-black text-cloudo-accent/80 truncate max-w-60 font-mono transition-all">
                          {log.Id || "SYSTEM"}
                        </div>
                        {log.Severity && (
                          <div
                            className={`text-[9px] font-black uppercase tracking-tighter px-1.5 py-0.5 rounded-sm border inline-flex items-center gap-1.5 w-fit ${
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
                    <td className="px-4 py-4">
                      <div className="flex flex-col gap-0.5">
                        <div className="text-[11px] font-mono text-cloudo-accent/70 uppercase tracking-widest">
                          {log.Runbook}
                        </div>
                        {log.Run_Args && (
                          <div className="text-[10px] text-cloudo-muted/60 font-mono mt-0.5 break-all">
                            {log.Run_Args}
                          </div>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-4">
                      <div>
                        <div className="text-[10px] font-black text-cloudo-muted uppercase tracking-widest">
                          {log.Worker || "N/A"}
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-4 text-center">
                      {(log.OnCall === true || log.OnCall === "true") && (
                        <div className="flex justify-center">
                          <div className="w-1.5 h-1.5 bg-cloudo-err animate-pulse" />
                        </div>
                      )}
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
                  }}
                  className={`p-4 flex flex-col gap-3 transition-all duration-200 border-l-4 ${
                    selectedLog?.ExecId === log.ExecId
                      ? "bg-cloudo-accent/10 border-cloudo-accent"
                      : "border-transparent hover:bg-white/3"
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
      {selectedLog && (
        <>
          {/* Resize Handle */}
          <div
            className={`hidden lg:flex w-1 bg-cloudo-border hover:bg-cloudo-accent/50 cursor-col-resize transition-colors items-center justify-center group relative ${
              isResizing ? "bg-cloudo-accent" : ""
            }`}
            onMouseDown={startResizing}
          >
            <div className="absolute inset-y-0 -left-2 -right-2 z-10" />
            <div className="w-px h-8 bg-cloudo-muted/30 group-hover:bg-cloudo-accent/50" />
          </div>

          <div
            className={`bg-cloudo-panel border border-cloudo-border flex flex-col transition-all duration-500 ease-in-out overflow-hidden shadow-2xl ${
              isExpanded
                ? "fixed inset-4 z-60 animate-in zoom-in-95 duration-500 overflow-y-auto custom-scrollbar ring-1 ring-cloudo-accent/20"
                : "animate-in slide-in-from-right-full duration-500 relative"
            }`}
            style={isExpanded ? {} : { width: `${detailWidth}px` }}
          >
            <div className="p-6 border-b border-cloudo-border bg-cloudo-panel-2 flex justify-between items-center">
              <div className="flex items-center gap-4">
                <div>
                  <h3 className="text-xs font-black text-cloudo-text uppercase tracking-[0.2em]">
                    {selectedLog.Name || "Runtime Process"}
                  </h3>
                  <div className="flex items-center gap-2 mt-1">
                    <code className="text-[10px] text-cloudo-muted font-mono">
                      {selectedLog.ExecId}
                    </code>
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

            <div className="flex-1 overflow-auto p-8 space-y-8 custom-scrollbar bg-cloudo-dark/30">
              {/* Section: Execution Timeline */}
              <ExecutionTimeline
                execId={selectedLog.ExecId}
                partitionKey={selectedLog.PartitionKey}
              />

              {/* Section: Identity & Deployment */}
              <div className="space-y-4">
                <div className="flex items-center gap-2">
                  <div className="w-1 h-3 bg-cloudo-accent" />
                  <h3 className="text-[10px] font-black uppercase tracking-[0.2em] text-cloudo-muted">
                    Process Identity & Deployment
                  </h3>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-3">
                  <DetailItem
                    label="Asset_Path"
                    value={selectedLog.Runbook}
                    icon={<HiOutlineTerminal className="text-cloudo-accent" />}
                  />
                  <DetailItem
                    label="Initiator"
                    value={selectedLog.Initiator || "SYSTEM"}
                    icon={<HiOutlineTag />}
                  />
                  <div className="bg-cloudo-accent/5 border border-cloudo-border p-3 space-y-2 overflow-hidden">
                    <div className="flex items-center gap-2 text-cloudo-muted/60">
                      <HiExclamationCircle className="text-sm" />
                      <span className="text-[10px] font-black uppercase tracking-widest whitespace-nowrap">
                        Severity
                      </span>
                    </div>
                    <div
                      className={`text-[9px] font-black uppercase tracking-tighter px-1.5 py-0.5 rounded-sm border inline-flex items-center gap-1.5 w-fit ${
                        getSeverityStyles(selectedLog.Severity || "INFO").bg
                      } ${
                        getSeverityStyles(selectedLog.Severity || "INFO").border
                      } ${
                        getSeverityStyles(selectedLog.Severity || "INFO").text
                      }`}
                    >
                      <span
                        className={`w-1 h-1 rounded-full animate-pulse ${
                          getSeverityStyles(selectedLog.Severity || "INFO").dot
                        }`}
                      />
                      {selectedLog.Severity || "MANUAL"}
                    </div>
                  </div>
                  <div className="bg-cloudo-accent/5 border border-cloudo-border p-3 space-y-2 overflow-hidden">
                    <div className="flex items-center gap-2 text-cloudo-muted/60">
                      <HiClock className="text-sm" />
                      <span className="text-[10px] font-black uppercase tracking-widest whitespace-nowrap">
                        On Call
                      </span>
                    </div>
                    <div className="flex items-center gap-2 h-4">
                      {selectedLog.OnCall === true ||
                      selectedLog.OnCall === "true" ? (
                        <div className="flex items-center gap-2">
                          <div className="w-1.5 h-1.5 bg-cloudo-err animate-pulse" />
                          <span className="text-[10px] font-black text-cloudo-err uppercase tracking-widest">
                            Active
                          </span>
                        </div>
                      ) : (
                        <span className="text-[10px] font-black text-cloudo-muted uppercase tracking-widest opacity-30">
                          Inactive
                        </span>
                      )}
                    </div>
                  </div>
                  <DetailItem
                    label="Node"
                    value={selectedLog.Worker || "DYNAMIC"}
                    icon={<HiOutlineDatabase />}
                  />
                  <DetailItem
                    label="Group"
                    value={selectedLog.Group || "default"}
                    icon={<HiOutlineTag />}
                  />
                  <DetailItem
                    label="Requested At"
                    value={new Date(selectedLog.RequestedAt).toLocaleString(
                      [],
                      {
                        day: "2-digit",
                        month: "2-digit",
                        year: "numeric",
                        hour: "2-digit",
                        minute: "2-digit",
                        second: "2-digit",
                        hour12: false,
                      },
                    )}
                    icon={<HiOutlineCalendar />}
                    className="md:col-span-4"
                  />
                </div>
              </div>

              {/* Section: Execution Status */}
              <div className="space-y-4">
                <div className="flex items-center gap-2">
                  <div className="w-1 h-3 bg-cloudo-accent" />
                  <h3 className="text-[10px] font-black uppercase tracking-[0.2em] text-cloudo-muted">
                    Execution Status
                  </h3>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3">
                  <DetailItem
                    label="Status"
                    value={selectedLog.Status}
                    icon={<HiOutlineTag />}
                    className={`flex-col items-start space-y-1! ${getStatusBadgeClass(
                      selectedLog.Status,
                    )}`}
                  />
                  <div
                    className="bg-cloudo-accent/5 border border-cloudo-border p-3 flex flex-col justify-center items-center gap-1 cursor-pointer hover:bg-cloudo-accent/10 transition-colors"
                    onClick={() => copyToClipboard(selectedLog.ExecId)}
                  >
                    <span className="text-[10px] font-black text-cloudo-muted uppercase tracking-widest block">
                      Copy_ID
                    </span>
                    {copied ? (
                      <HiOutlineClipboardCheck className="text-cloudo-ok w-3 h-3" />
                    ) : (
                      <HiOutlineClipboard className="w-3 h-3" />
                    )}
                  </div>
                </div>
              </div>

              {/* Section: Runtime Arguments */}
              <div className="space-y-4">
                <div className="flex items-center gap-2">
                  <div className="w-1 h-3 bg-cloudo-accent" />
                  <h3 className="text-[10px] font-black uppercase tracking-[0.2em] text-cloudo-muted">
                    Runtime Arguments
                  </h3>
                </div>
                <div className="bg-cloudo-dark/60 border border-cloudo-border p-4 font-mono text-[11px] text-cloudo-accent whitespace-pre-wrap break-all leading-relaxed">
                  {selectedLog.Run_Args || "EMPTY_ARGS"}
                </div>
              </div>

              {/* Section: Resource Info */}
              {(() => {
                let info: Record<string, unknown> = {};
                if (selectedLog.ResourceInfo) {
                  try {
                    const parsed = JSON.parse(selectedLog.ResourceInfo);
                    if (parsed && typeof parsed === "object") {
                      info = parsed as Record<string, unknown>;
                    } else {
                      // Fallback: show raw string if JSON is not an object
                      info = { _raw: selectedLog.ResourceInfo };
                    }
                  } catch (e) {
                    console.warn("Failed to parse ResourceInfo:", e);
                    // Ensure we still display the raw content if parsing fails
                    info = { _raw: selectedLog.ResourceInfo };
                  }
                }

                const validEntries = Object.entries(info).filter(
                  // eslint-disable-next-line @typescript-eslint/no-unused-vars
                  ([__unused, v]) =>
                    v !== null && v !== undefined && String(v).trim() !== "",
                );

                if (validEntries.length > 0) {
                  return (
                    <div className="space-y-4">
                      <div className="flex items-center gap-2">
                        <div className="w-1 h-3 bg-cloudo-accent" />
                        <h3 className="text-[10px] font-black uppercase tracking-[0.2em] text-cloudo-muted">
                          Resource Info
                        </h3>
                      </div>
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
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
                              className={`bg-cloudo-accent/5 border border-cloudo-border p-3 flex flex-col group gap-2 ${
                                isRaw ? "md:col-span-2" : ""
                              }`}
                            >
                              <div className="flex items-center justify-between gap-2">
                                <span className="text-[10px] font-black text-cloudo-muted uppercase tracking-widest shrink-0">
                                  {k}
                                </span>
                                {isRaw && (
                                  <button
                                    onClick={() =>
                                      setIsRawExpanded(!isRawExpanded)
                                    }
                                    className="text-[9px] font-black uppercase tracking-widest text-cloudo-accent hover:text-white transition-colors flex items-center gap-1"
                                  >
                                    <span>
                                      {isRawExpanded
                                        ? "[ COLLAPSE ]"
                                        : "[ EXPAND ]"}
                                    </span>
                                  </button>
                                )}
                              </div>
                              {(!isRaw || isRawExpanded) && (
                                <span
                                  className={`text-xs font-mono text-cloudo-text group-hover:text-cloudo-accent transition-colors break-all ${
                                    isRaw
                                      ? "whitespace-pre-wrap text-left"
                                      : "text-right"
                                  }`}
                                >
                                  {displayValue}
                                </span>
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

              {/* Section: Telemetry Logs */}
              <div className="space-y-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <div className="w-1 h-2 bg-cloudo-accent" />
                    <span className="text-[11px] font-black uppercase tracking-[0.2em] text-cloudo-text">
                      Standard Output Stream
                    </span>
                  </div>
                  <button
                    onClick={() => copyToClipboard(selectedLog.Log)}
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
                <div className="bg-cloudo-dark p-6 border border-cloudo-border font-mono text-xs min-h-100 overflow-x-auto">
                  {formatLogContent(selectedLog.Log)}
                </div>
              </div>
            </div>
          </div>
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

        const res = await cloudoFetch(`/logs/query?${params}`);
        const data = await res.json();

        // Sort all logs chronologically for this execution by time and priority
        const allLogs = (data.items || []).sort((a: LogEntry, b: LogEntry) => {
          const priorityDiff =
            (statusPriority[a.Status] ?? 0) - (statusPriority[b.Status] ?? 0);
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

  const getStatusColor = (status: string) => {
    const s = status.toLowerCase();
    if (s === "accepted")
      return "bg-cloudo-accent/20 border-cloudo-accent text-cloudo-accent";
    if (s === "running")
      return "bg-yellow-500/20 border-yellow-500/50 text-yellow-400";
    if (s === "succeeded" || s === "completed")
      return "bg-cloudo-ok/20 border-cloudo-ok text-cloudo-ok";
    if (s === "failed" || s === "error")
      return "bg-cloudo-err/20 border-cloudo-err text-cloudo-err";
    return "bg-cloudo-muted/10 border-cloudo-muted/30 text-cloudo-muted";
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
      <div className="space-y-4">
        <div className="flex items-center gap-2">
          <div className="w-1 h-3 bg-cloudo-accent" />
          <h3 className="text-[10px] font-black uppercase tracking-[0.2em] text-cloudo-muted">
            Execution Timeline
          </h3>
        </div>
        <div className="text-[11px] text-cloudo-muted">Loading timeline...</div>
      </div>
    );
  }

  if (timelineLogs.length === 0) {
    return null;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <div className="w-1 h-3 bg-cloudo-accent" />
        <h3 className="text-[10px] font-black uppercase tracking-[0.2em] text-cloudo-muted">
          Execution Timeline
        </h3>
      </div>

      {/* Timeline Schema */}
      <div className="flex flex-wrap items-center justify-center gap-2 md:gap-3">
        {timelineLogs.map((log, idx) => {
          return (
            <div
              key={`${log.ExecId}-${log.RowKey}`}
              className="flex items-center gap-2 md:gap-3"
            >
              {/* Phase Box */}
              <div
                className={`flex flex-col items-center gap-1 p-3 md:p-4 rounded-lg border-2 transition-all hover:shadow-lg ${getStatusColor(
                  log.Status || "",
                )}`}
              >
                <div className="text-2xl md:text-3xl">
                  {getStatusIcon(log.Status || "")}
                </div>
                <div className="text-xs md:text-sm font-bold uppercase tracking-wide">
                  {log.Status || "UNKNOWN"}
                </div>
                <div className="text-xs md:text-xs font-bold">
                  {log.RequestedAt || "UNKNOWN"}
                </div>
              </div>

              {/* Connector with Duration */}
              {idx < timelineLogs.length - 1 && (
                <div className="flex flex-col items-center gap-1">
                  <div
                    className="h-1 bg-linear-to-r from-cloudo-accent to-cloudo-accent/30 rounded-full"
                    style={{ width: `24px` }}
                  />
                </div>
              )}
            </div>
          );
        })}
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
      className={`bg-cloudo-accent/5 border border-cloudo-border p-3 space-y-2 overflow-hidden ${className}`}
    >
      <div className="flex items-center gap-2 text-cloudo-muted/60">
        <span className="text-sm">{icon}</span>
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
