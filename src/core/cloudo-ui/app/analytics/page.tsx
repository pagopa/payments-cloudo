"use client";

import { useEffect, useState, useCallback, useMemo, useRef } from "react";
import { cloudoFetch } from "@/lib/api";
import {
  HiOutlineChartBar,
  HiOutlineExclamationCircle,
  HiOutlineCheckCircle,
  HiOutlineLightningBolt,
  HiOutlineCalendar,
  HiOutlineTrendingUp,
  HiOutlineClock,
  HiOutlineRefresh,
  HiOutlineDownload,
  HiOutlinePlay,
} from "react-icons/hi";

interface LogEntry {
  ExecId: string;
  Runbook: string;
  RequestedAt: string;
  Status: string;
}

interface ProcessedExecution {
  execId: string;
  runbook: string;
  status: string;
  requestedAt: string;
  durationMs: number | null;
}

interface AnalyticsData {
  totalRequests: number;
  successRate: number;
  peakThroughput: number;
  failedOnCalls: number;
  activeCount: number;
  avgDurationMs: number;
  p95DurationMs: number;
  requestsByStatus: Record<string, number>;
  requestsByHour: Record<string, number>;
  topRunbooks: { name: string; count: number; success: number }[];
  slowestRunbooks: { name: string; avgMs: number; count: number }[];
}

const EMPTY_DATA: AnalyticsData = {
  totalRequests: 0,
  successRate: 0,
  peakThroughput: 0,
  failedOnCalls: 0,
  activeCount: 0,
  avgDurationMs: 0,
  p95DurationMs: 0,
  requestsByStatus: {},
  requestsByHour: {},
  topRunbooks: [],
  slowestRunbooks: [],
};

const IN_PROGRESS_STATUSES = ["accepted", "pending", "routed", "scheduled"];
const TERMINAL_STATUSES = ["succeeded", "completed", "failed", "error"];
const SUCCESS_STATUSES = ["succeeded", "completed"];

// Theme-aware color (CSS var) for a given execution status.
function statusVar(status: string): string {
  if (SUCCESS_STATUSES.includes(status)) return "var(--color-cloudo-ok)";
  if (["failed", "error"].includes(status)) return "var(--color-cloudo-err)";
  if (IN_PROGRESS_STATUSES.includes(status))
    return "var(--color-cloudo-accent)";
  return "var(--color-cloudo-muted)";
}

const RANGE_TO_DAYS: Record<string, number> = { "24h": 1, "7d": 7, "30d": 30 };
const MAX_CUSTOM_DAYS = 92;

function partitionKeyFor(date: Date): string {
  return `${date.getFullYear()}${String(date.getMonth() + 1).padStart(
    2,
    "0",
  )}${String(date.getDate()).padStart(2, "0")}`;
}

function isoDateInput(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(
    2,
    "0",
  )}-${String(date.getDate()).padStart(2, "0")}`;
}

// Builds the list of calendar days to fetch for either a preset or a custom range.
function buildDateRange(
  timeRange: string,
  customStart: string,
  customEnd: string,
): Date[] {
  if (timeRange === "custom" && customStart && customEnd) {
    const start = new Date(`${customStart}T00:00:00`);
    const end = new Date(`${customEnd}T00:00:00`);
    if (isNaN(start.getTime()) || isNaN(end.getTime()) || end < start)
      return [];
    const dates: Date[] = [];
    const cur = new Date(start);
    while (cur <= end && dates.length < MAX_CUSTOM_DAYS) {
      dates.push(new Date(cur));
      cur.setDate(cur.getDate() + 1);
    }
    return dates;
  }
  const days = RANGE_TO_DAYS[timeRange] ?? 1;
  const today = new Date();
  return Array.from({ length: days }, (_, i) => {
    const d = new Date(today);
    d.setDate(today.getDate() - i);
    return d;
  });
}

function percentile(sortedAsc: number[], p: number): number {
  if (!sortedAsc.length) return 0;
  const idx = Math.min(
    sortedAsc.length - 1,
    Math.ceil((p / 100) * sortedAsc.length) - 1,
  );
  return sortedAsc[Math.max(0, idx)];
}

function formatDuration(ms: number): string {
  if (!ms || ms <= 0) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${Math.round(s % 60)}s`;
}

function downloadCsv(rows: ProcessedExecution[]) {
  const header = ["ExecId", "Runbook", "Status", "RequestedAt", "DurationMs"];
  const escape = (v: string | number) => `"${String(v).replace(/"/g, '""')}"`;
  const body = rows.map((r) =>
    [r.execId, r.runbook, r.status, r.requestedAt, r.durationMs ?? ""]
      .map(escape)
      .join(","),
  );
  const csv = [header.join(","), ...body].join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `cloudo-analytics-${isoDateInput(new Date())}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

export default function AnalyticsPage() {
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [timeRange, setTimeRange] = useState("24h");
  const [data, setData] = useState<AnalyticsData>(EMPTY_DATA);

  // Custom range + UX controls.
  const [showCustomPicker, setShowCustomPicker] = useState(false);
  const [customStart, setCustomStart] = useState(() =>
    isoDateInput(new Date(Date.now() - 6 * 86400000)),
  );
  const [customEnd, setCustomEnd] = useState(() => isoDateInput(new Date()));
  const [autoRefresh, setAutoRefresh] = useState(false);

  const abortRef = useRef<AbortController | null>(null);
  // Keep the last processed executions around for CSV export.
  const executionsRef = useRef<ProcessedExecution[]>([]);

  const fetchAnalytics = useCallback(async () => {
    // Cancel any in-flight request so stale responses can't overwrite fresh data.
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setRefreshing(true);
    setError(null);
    try {
      const rangeDates = buildDateRange(timeRange, customStart, customEnd);
      if (rangeDates.length === 0) {
        setError("Intervallo personalizzato non valido.");
        return;
      }
      // Hourly buckets only make sense for a single day, daily otherwise.
      const hourlyGranularity = rangeDates.length <= 1;

      const partitionKeys = rangeDates.map(partitionKeyFor);

      const results = await Promise.all(
        partitionKeys.map(async (partitionKey) => {
          try {
            const res = await cloudoFetch(
              `/logs/query?partitionKey=${partitionKey}&limit=1000`,
              { signal: controller.signal },
            );
            if (!res.ok) return [] as LogEntry[];
            const result = await res.json();
            return (result.items || []) as LogEntry[];
          } catch (e) {
            if ((e as Error)?.name === "AbortError") throw e;
            console.error(`Failed to fetch logs for ${partitionKey}`, e);
            return [] as LogEntry[];
          }
        }),
      );

      const allLogs = results.flat();

      // Group by ExecId to derive final status and duration.
      const execMap: Record<
        string,
        {
          start?: number;
          end?: number;
          status: string;
          runbook: string;
          requestedAt: string;
        }
      > = {};

      for (const log of allLogs) {
        const id = log.ExecId;
        if (!id) continue;

        let exec = execMap[id];
        if (!exec) {
          exec = execMap[id] = {
            status: "unknown",
            runbook: log.Runbook || "Unknown",
            requestedAt: log.RequestedAt,
          };
        }

        const currentStatus = (log.Status || "").toLowerCase();
        const ts = new Date(log.RequestedAt).getTime();

        if (IN_PROGRESS_STATUSES.includes(currentStatus)) {
          if (exec.start === undefined || ts < exec.start) exec.start = ts;
          if (exec.status === "unknown") exec.status = currentStatus;
        }

        if (TERMINAL_STATUSES.includes(currentStatus)) {
          if (exec.end === undefined || ts > exec.end) {
            exec.end = ts;
            exec.status = currentStatus;
          }
        }
      }

      const processedExecutions = Object.entries(execMap);
      const statusMap: Record<string, number> = {};
      const runbookMap: Record<
        string,
        { count: number; success: number; durSum: number; durCount: number }
      > = {};
      const hourMap: Record<string, number> = {};
      const durations: number[] = [];
      const exportRows: ProcessedExecution[] = [];
      let succeeded = 0;
      let activeCount = 0;
      let durationSum = 0;

      for (const [execId, exec] of processedExecutions) {
        const status = exec.status;
        statusMap[status] = (statusMap[status] || 0) + 1;

        const isSuccess = SUCCESS_STATUSES.includes(status);
        if (isSuccess) succeeded++;
        if (IN_PROGRESS_STATUSES.includes(status)) activeCount++;

        const rb = exec.runbook;
        const rbStats = (runbookMap[rb] ??= {
          count: 0,
          success: 0,
          durSum: 0,
          durCount: 0,
        });
        rbStats.count++;
        if (isSuccess) rbStats.success++;

        let durationMs: number | null = null;
        if (exec.start !== undefined && exec.end !== undefined) {
          const d = exec.end - exec.start;
          if (d >= 0) {
            durationMs = d;
            durationSum += d;
            durations.push(d);
            rbStats.durSum += d;
            rbStats.durCount++;
          }
        }

        const date = new Date(exec.requestedAt);
        const timeKey = hourlyGranularity
          ? `${String(date.getHours()).padStart(2, "0")}:00`
          : date.toISOString().split("T")[0];
        hourMap[timeKey] = (hourMap[timeKey] || 0) + 1;

        exportRows.push({
          execId,
          runbook: rb,
          status,
          requestedAt: exec.requestedAt,
          durationMs,
        });
      }

      const topRunbooks = Object.entries(runbookMap)
        .map(([name, stats]) => ({
          name,
          count: stats.count,
          success: stats.success,
        }))
        .sort((a, b) => b.count - a.count)
        .slice(0, 5);

      const slowestRunbooks = Object.entries(runbookMap)
        .filter(([, stats]) => stats.durCount > 0)
        .map(([name, stats]) => ({
          name,
          avgMs: stats.durSum / stats.durCount,
          count: stats.durCount,
        }))
        .sort((a, b) => b.avgMs - a.avgMs)
        .slice(0, 5);

      const hourValues = Object.values(hourMap);
      const peakThroughput = hourValues.length ? Math.max(...hourValues) : 0;
      durations.sort((a, b) => a - b);

      executionsRef.current = exportRows;

      setData({
        totalRequests: processedExecutions.length,
        successRate: processedExecutions.length
          ? (succeeded / processedExecutions.length) * 100
          : 0,
        peakThroughput,
        failedOnCalls: (statusMap["failed"] || 0) + (statusMap["error"] || 0),
        activeCount,
        avgDurationMs: durations.length ? durationSum / durations.length : 0,
        p95DurationMs: percentile(durations, 95),
        requestsByStatus: statusMap,
        requestsByHour: hourMap,
        topRunbooks,
        slowestRunbooks,
      });
      setLastUpdated(new Date());
    } catch (err) {
      if ((err as Error)?.name === "AbortError") return;
      console.error("Error fetching analytics:", err);
      setError("Impossibile caricare i dati di analytics. Riprova.");
    } finally {
      if (abortRef.current === controller) {
        setRefreshing(false);
        setLoading(false);
      }
    }
  }, [timeRange, customStart, customEnd]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- refetch when the time range changes
    fetchAnalytics();
    return () => abortRef.current?.abort();
  }, [fetchAnalytics]);

  // Optional periodic auto-refresh.
  useEffect(() => {
    if (!autoRefresh) return;
    const id = setInterval(() => fetchAnalytics(), 30000);
    return () => clearInterval(id);
  }, [autoRefresh, fetchAnalytics]);

  const applyCustomRange = useCallback(() => {
    if (!customStart || !customEnd || customEnd < customStart) {
      setError(
        "Seleziona un intervallo valido (la fine deve seguire l'inizio).",
      );
      return;
    }
    setError(null);
    setShowCustomPicker(false);
    setTimeRange("custom");
  }, [customStart, customEnd]);

  // Derived view-model values computed once per data change instead of inline in render.
  const statusEntries = useMemo(
    () => Object.entries(data.requestsByStatus).sort((a, b) => b[1] - a[1]),
    [data.requestsByStatus],
  );

  const timelineEntries = useMemo(
    () =>
      Object.entries(data.requestsByHour).sort(([a], [b]) =>
        a.localeCompare(b),
      ),
    [data.requestsByHour],
  );

  const maxHourCount = useMemo(
    () =>
      timelineEntries.length
        ? Math.max(...timelineEntries.map(([, c]) => c))
        : 0,
    [timelineEntries],
  );

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center h-full bg-cloudo-dark">
        <div className="w-8 h-8 border-2 border-cloudo-accent/30 border-t-cloudo-accent rounded-full animate-spin mb-4" />
        <span className="text-xs font-black uppercase tracking-[0.3em] text-cloudo-muted">
          Calculating Analytics...
        </span>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full bg-cloudo-dark text-cloudo-text font-mono">
      {/* Header Bar */}
      <div className="flex items-center justify-between px-8 py-4 border-b border-cloudo-border bg-cloudo-panel sticky top-0 z-20">
        <div className="flex items-center gap-4 shrink-0">
          <div className="p-2 bg-cloudo-accent/5 border border-cloudo-accent/20 shrink-0">
            <HiOutlineChartBar className="text-cloudo-accent w-5 h-5" />
          </div>
          <div>
            <h1 className="text-sm font-black tracking-[0.2em] text-cloudo-text uppercase">
              Advanced Analytics
            </h1>
            <p className="text-[11px] text-cloudo-muted font-bold uppercase tracking-[0.3em] opacity-70">
              Performance & Diagnostics
            </p>
          </div>
        </div>

        {/* Controls */}
        <div className="flex items-center gap-3">
          {lastUpdated && (
            <span className="hidden lg:flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-widest text-cloudo-muted/60">
              <HiOutlineClock className="w-3.5 h-3.5" />
              {lastUpdated.toLocaleTimeString()}
            </span>
          )}

          <button
            onClick={() => setAutoRefresh((v) => !v)}
            title="Auto-refresh ogni 30s"
            className={`flex items-center gap-1.5 px-3 py-2 border text-[10px] font-black uppercase tracking-widest transition-all ${
              autoRefresh
                ? "border-cloudo-ok/40 text-cloudo-ok bg-cloudo-ok/10"
                : "border-cloudo-border text-cloudo-muted hover:text-cloudo-text"
            }`}
          >
            <span
              className={`w-1.5 h-1.5 rounded-full ${
                autoRefresh ? "bg-cloudo-ok animate-pulse" : "bg-cloudo-muted"
              }`}
            />
            Auto
          </button>

          <button
            onClick={() => downloadCsv(executionsRef.current)}
            disabled={data.totalRequests === 0}
            title="Esporta in CSV"
            className="p-2 border border-cloudo-border text-cloudo-muted hover:text-cloudo-text hover:border-cloudo-accent/50 transition-all disabled:opacity-40"
          >
            <HiOutlineDownload className="w-4 h-4" />
          </button>

          <button
            onClick={fetchAnalytics}
            disabled={refreshing}
            title="Aggiorna dati"
            className="p-2 border border-cloudo-border text-cloudo-muted hover:text-cloudo-text hover:border-cloudo-accent/50 transition-all disabled:opacity-50"
          >
            <HiOutlineRefresh
              className={`w-4 h-4 ${refreshing ? "animate-spin" : ""}`}
            />
          </button>

          {/* Time Picker */}
          <div className="relative flex items-center gap-2 bg-cloudo-accent/10 border border-cloudo-border p-1">
            {["24h", "7d", "30d"].map((range) => (
              <button
                key={range}
                onClick={() => {
                  setShowCustomPicker(false);
                  setTimeRange(range);
                }}
                className={`px-4 py-1.5 text-[11px] font-black uppercase tracking-widest transition-all ${
                  timeRange === range
                    ? "bg-cloudo-accent text-cloudo-dark"
                    : "text-cloudo-muted hover:text-cloudo-text"
                }`}
              >
                {range}
              </button>
            ))}
            <div className="w-px h-4 bg-cloudo-border mx-2" />
            <button
              onClick={() => setShowCustomPicker((v) => !v)}
              className={`flex items-center gap-2 px-3 py-1.5 text-[10px] font-bold uppercase tracking-tighter transition-all ${
                timeRange === "custom"
                  ? "bg-cloudo-accent text-cloudo-dark"
                  : "text-cloudo-muted/70 hover:text-cloudo-text"
              }`}
            >
              <HiOutlineCalendar className="w-4 h-4" />
              {timeRange === "custom"
                ? `${customStart} → ${customEnd}`
                : "Custom Range"}
            </button>

            {showCustomPicker && (
              <div className="absolute top-full right-0 mt-2 w-72 bg-cloudo-panel border border-cloudo-border p-4 z-30 shadow-2xl space-y-3">
                <p className="text-[10px] font-black uppercase tracking-[0.3em] text-cloudo-muted">
                  Custom Range
                </p>
                <label className="block space-y-1">
                  <span className="text-[10px] font-bold uppercase tracking-widest text-cloudo-muted/70">
                    From
                  </span>
                  <input
                    type="date"
                    value={customStart}
                    max={customEnd}
                    onChange={(e) => setCustomStart(e.target.value)}
                    className="w-full bg-cloudo-dark border border-cloudo-border px-2 py-1.5 text-xs text-cloudo-text focus:border-cloudo-accent outline-none"
                  />
                </label>
                <label className="block space-y-1">
                  <span className="text-[10px] font-bold uppercase tracking-widest text-cloudo-muted/70">
                    To
                  </span>
                  <input
                    type="date"
                    value={customEnd}
                    min={customStart}
                    max={isoDateInput(new Date())}
                    onChange={(e) => setCustomEnd(e.target.value)}
                    className="w-full bg-cloudo-dark border border-cloudo-border px-2 py-1.5 text-xs text-cloudo-text focus:border-cloudo-accent outline-none"
                  />
                </label>
                <p className="text-[9px] text-cloudo-muted/50 uppercase tracking-widest">
                  Max {MAX_CUSTOM_DAYS} days
                </p>
                <div className="flex gap-2">
                  <button
                    onClick={() => setShowCustomPicker(false)}
                    className="flex-1 py-1.5 text-[10px] font-black uppercase tracking-widest border border-cloudo-border text-cloudo-muted hover:text-cloudo-text transition-all"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={applyCustomRange}
                    className="flex-1 py-1.5 text-[10px] font-black uppercase tracking-widest bg-cloudo-accent text-cloudo-dark hover:opacity-90 transition-all"
                  >
                    Apply
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-3 px-8 py-3 bg-cloudo-err/10 border-b border-cloudo-err/30 text-cloudo-err">
          <HiOutlineExclamationCircle className="w-4 h-4 shrink-0" />
          <span className="text-[11px] font-bold uppercase tracking-widest">
            {error}
          </span>
          <button
            onClick={fetchAnalytics}
            className="ml-auto text-[10px] font-black uppercase tracking-widest underline underline-offset-2 hover:opacity-80"
          >
            Riprova
          </button>
        </div>
      )}

      <div className="flex-1 overflow-auto p-8">
        <div className="max-w-[1400px] mx-auto space-y-8">
          {/* Main KPI Row */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <MetricCard
              title="Throughput"
              value={data.totalRequests.toLocaleString()}
              subValue="Requests Processed"
              icon={<HiOutlineLightningBolt />}
              trend="LIVE"
              positive={true}
            />
            <MetricCard
              title="Active Now"
              value={data.activeCount.toLocaleString()}
              subValue="In-Progress Execs"
              icon={<HiOutlinePlay />}
              trend={data.activeCount > 0 ? "RUNNING" : "IDLE"}
              positive={true}
              color="text-cloudo-accent"
            />
            <MetricCard
              title="Success Rate"
              value={`${data.successRate.toFixed(1)}%`}
              subValue="Overall Reliability"
              icon={<HiOutlineCheckCircle />}
              trend={data.successRate > 95 ? "OPTIMAL" : "STABLE"}
              positive={true}
              color="text-cloudo-ok"
            />
            <MetricCard
              title="Avg Latency"
              value={formatDuration(data.avgDurationMs)}
              subValue="Mean Exec Duration"
              icon={<HiOutlineClock />}
              trend={data.avgDurationMs > 600000 ? "SLOW" : "FAST"}
              positive={data.avgDurationMs <= 600000}
              color={
                data.avgDurationMs > 600000
                  ? "text-cloudo-warn"
                  : "text-cloudo-text"
              }
            />
            <MetricCard
              title="P95 Latency"
              value={formatDuration(data.p95DurationMs)}
              subValue="95th Percentile"
              icon={<HiOutlineTrendingUp />}
              trend={data.p95DurationMs > 120000 ? "TAIL" : "TIGHT"}
              positive={data.p95DurationMs <= 120000}
              color={
                data.p95DurationMs > 120000
                  ? "text-cloudo-warn"
                  : "text-cloudo-text"
              }
            />
            <MetricCard
              title="Peak Throughput"
              value={data.peakThroughput.toLocaleString()}
              subValue="Max Requests / Slot"
              icon={<HiOutlineTrendingUp />}
              trend={data.peakThroughput > 10 ? "HIGH" : "NORMAL"}
              positive={true}
            />
            <MetricCard
              title="Failed OnCalls"
              value={data.failedOnCalls.toLocaleString()}
              subValue="Failed Executions"
              icon={<HiOutlineExclamationCircle />}
              trend={data.failedOnCalls === 0 ? "ZERO" : "WARN"}
              positive={data.failedOnCalls === 0}
              color={
                data.failedOnCalls > 0 ? "text-cloudo-err" : "text-cloudo-ok"
              }
            />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
            {/* Status Distribution */}
            <div className="lg:col-span-1 space-y-4">
              <SectionHeader title="Status Distribution" />
              <div className="bg-cloudo-panel border border-cloudo-border p-6 space-y-4">
                {statusEntries.map(([status, count]) => (
                  <div key={status} className="space-y-1">
                    <div className="flex justify-between text-[11px] font-bold uppercase tracking-widest">
                      <span className="text-cloudo-muted">{status}</span>
                      <span className="text-cloudo-text">
                        {count.toLocaleString()} (
                        {data.totalRequests
                          ? ((count / data.totalRequests) * 100).toFixed(1)
                          : "0.0"}
                        %)
                      </span>
                    </div>
                    <div className="w-full h-1 bg-white/5 overflow-hidden">
                      <div
                        className={`h-full ${
                          SUCCESS_STATUSES.includes(status)
                            ? "bg-cloudo-ok"
                            : ["failed", "error"].includes(status)
                              ? "bg-cloudo-err"
                              : IN_PROGRESS_STATUSES.includes(status)
                                ? "bg-cloudo-accent"
                                : "bg-cloudo-muted"
                        }`}
                        style={{
                          width: `${
                            data.totalRequests
                              ? (count / data.totalRequests) * 100
                              : 0
                          }%`,
                        }}
                      />
                    </div>
                  </div>
                ))}
                {statusEntries.length === 0 && (
                  <div className="py-10 text-center text-cloudo-muted/60 text-xs italic">
                    NO_DATA_AVAILABLE
                  </div>
                )}
              </div>
            </div>

            {/* Top Runbooks Performance */}
            <div className="lg:col-span-2 space-y-4">
              <SectionHeader title="Top Runbooks Performance" />
              <div className="bg-cloudo-panel border border-cloudo-border overflow-hidden">
                <table className="w-full text-left border-collapse text-sm">
                  <thead className="bg-cloudo-panel-2 border-b border-cloudo-border">
                    <tr className="text-[11px] font-black text-cloudo-muted uppercase tracking-[0.3em]">
                      <th className="px-6 py-4">Runbook_ID</th>
                      <th className="px-6 py-4">Invocations</th>
                      <th className="px-6 py-4">Success_Rate</th>
                      <th className="px-6 py-4 text-right">Trend</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-cloudo-border/50">
                    {data.topRunbooks.map((rb, i) => (
                      <tr
                        key={i}
                        className="hover:bg-white/[0.02] transition-colors"
                      >
                        <td className="px-6 py-4 font-bold text-cloudo-text uppercase tracking-wider">
                          {rb.name}
                        </td>
                        <td className="px-6 py-4 font-mono text-cloudo-muted">
                          {rb.count}
                        </td>
                        <td className="px-6 py-4">
                          <div className="flex items-center gap-2">
                            <span className="font-mono text-xs">
                              {((rb.success / rb.count) * 100).toFixed(1)}%
                            </span>
                            <div className="flex-1 max-w-[100px] h-1 bg-white/5">
                              <div
                                className="h-full bg-cloudo-ok"
                                style={{
                                  width: `${(rb.success / rb.count) * 100}%`,
                                }}
                              />
                            </div>
                          </div>
                        </td>
                        <td className="px-6 py-4 text-right">
                          <HiOutlineTrendingUp className="inline text-cloudo-ok w-4 h-4" />
                        </td>
                      </tr>
                    ))}
                    {data.topRunbooks.length === 0 && (
                      <tr>
                        <td
                          colSpan={4}
                          className="py-20 text-center text-cloudo-muted/60 text-xs italic"
                        >
                          NO_ACTIVE_RUNBOOKS
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          {/* Slowest Runbooks */}
          <div className="space-y-4">
            <SectionHeader title="Slowest Runbooks (Avg Duration)" />
            <div className="bg-cloudo-panel border border-cloudo-border overflow-hidden">
              <table className="w-full text-left border-collapse text-sm">
                <thead className="bg-cloudo-panel-2 border-b border-cloudo-border">
                  <tr className="text-[11px] font-black text-cloudo-muted uppercase tracking-[0.3em]">
                    <th className="px-6 py-4">Runbook_ID</th>
                    <th className="px-6 py-4">Samples</th>
                    <th className="px-6 py-4">Avg_Duration</th>
                    <th className="px-6 py-4 text-right w-1/3">Relative</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-cloudo-border/50">
                  {data.slowestRunbooks.map((rb, i) => {
                    const max = data.slowestRunbooks[0]?.avgMs || 1;
                    return (
                      <tr
                        key={i}
                        className="hover:bg-white/[0.02] transition-colors"
                      >
                        <td className="px-6 py-4 font-bold text-cloudo-text uppercase tracking-wider">
                          {rb.name}
                        </td>
                        <td className="px-6 py-4 font-mono text-cloudo-muted">
                          {rb.count}
                        </td>
                        <td className="px-6 py-4 font-mono text-cloudo-text">
                          {formatDuration(rb.avgMs)}
                        </td>
                        <td className="px-6 py-4">
                          <div className="flex items-center justify-end">
                            <div className="w-full h-1.5 bg-white/5">
                              <div
                                className={`h-full ${
                                  rb.avgMs > 60000
                                    ? "bg-cloudo-warn"
                                    : "bg-cloudo-accent"
                                }`}
                                style={{ width: `${(rb.avgMs / max) * 100}%` }}
                              />
                            </div>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                  {data.slowestRunbooks.length === 0 && (
                    <tr>
                      <td
                        colSpan={4}
                        className="py-16 text-center text-cloudo-muted/60 text-xs italic"
                      >
                        NO_DURATION_DATA
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {/* Charts Row: Density timeline + Status donut */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
            {/* Activity Timeline */}
            <div className="lg:col-span-2 space-y-4">
              <SectionHeader title="Traffic Density Timeline" />
              <div className="bg-cloudo-panel border border-cloudo-border p-6">
                <AreaChart entries={timelineEntries} maxValue={maxHourCount} />
                {/* X-Axis */}
                <div className="flex justify-between px-1 mt-3 text-[10px] font-black text-cloudo-muted/70 uppercase tracking-[0.2em]">
                  {timelineEntries.length > 0 ? (
                    <>
                      <span>{timelineEntries[0][0]}</span>
                      <span className="opacity-40 text-[9px]">
                        {timeRange === "24h"
                          ? "HOURLY_DISTRIBUTION"
                          : "DAILY_DISTRIBUTION"}
                      </span>
                      <span>
                        {timelineEntries[timelineEntries.length - 1][0]}
                      </span>
                    </>
                  ) : (
                    <>
                      <span>
                        {timeRange === "24h" ? "00:00" : "OLDEST_DATA"}
                      </span>
                      <span className="opacity-40 text-[9px]">
                        TIMELINE_DISTRIBUTION
                      </span>
                      <span>
                        {timeRange === "24h" ? "23:00" : "RECENT_DATA"}
                      </span>
                    </>
                  )}
                </div>
              </div>
            </div>

            {/* Status Donut */}
            <div className="lg:col-span-1 space-y-4">
              <SectionHeader title="Status Mix" />
              <div className="bg-cloudo-panel border border-cloudo-border p-6">
                <DonutChart
                  segments={statusEntries.map(([status, count]) => ({
                    label: status,
                    value: count,
                    color: statusVar(status),
                  }))}
                  total={data.totalRequests}
                />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function AreaChart({
  entries,
  maxValue,
}: {
  entries: [string, number][];
  maxValue: number;
}) {
  const [hover, setHover] = useState<number | null>(null);

  if (entries.length === 0) {
    return (
      <div className="h-48 flex items-center justify-center text-cloudo-muted/80 italic text-xs">
        NO_TIMELINE_DATA
      </div>
    );
  }

  const W = 600;
  const H = 200;
  const padX = 10;
  const padTop = 16;
  const padBottom = 10;
  const max = Math.max(maxValue, 1);
  const n = entries.length;
  const innerW = W - padX * 2;
  const innerH = H - padTop - padBottom;

  const points = entries.map(([key, count], i) => {
    const x = n > 1 ? padX + (i / (n - 1)) * innerW : W / 2;
    const y = padTop + (1 - count / max) * innerH;
    return { x, y, key, count };
  });

  const line = points
    .map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y.toFixed(1)}`)
    .join(" ");
  const baseY = H - padBottom;
  const area = `${line} L${points[n - 1].x.toFixed(
    1,
  )},${baseY} L${points[0].x.toFixed(1)},${baseY} Z`;

  const colWidth = innerW / Math.max(n, 1);

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        className="w-full h-48"
        onMouseLeave={() => setHover(null)}
      >
        <defs>
          <linearGradient id="areaFill" x1="0" y1="0" x2="0" y2="1">
            <stop
              offset="0%"
              stopColor="var(--color-cloudo-accent)"
              stopOpacity="0.45"
            />
            <stop
              offset="100%"
              stopColor="var(--color-cloudo-accent)"
              stopOpacity="0.02"
            />
          </linearGradient>
        </defs>

        {/* Horizontal grid lines */}
        {[0, 0.25, 0.5, 0.75, 1].map((t) => {
          const y = padTop + t * innerH;
          return (
            <line
              key={t}
              x1={padX}
              x2={W - padX}
              y1={y}
              y2={y}
              stroke="var(--color-cloudo-border)"
              strokeOpacity="0.3"
              strokeWidth="1"
            />
          );
        })}

        <path d={area} fill="url(#areaFill)" />
        <path
          d={line}
          fill="none"
          stroke="var(--color-cloudo-accent)"
          strokeWidth="2"
          vectorEffect="non-scaling-stroke"
          strokeLinejoin="round"
          strokeLinecap="round"
        />

        {/* Hover crosshair + active point */}
        {hover !== null && (
          <line
            x1={points[hover].x}
            x2={points[hover].x}
            y1={padTop}
            y2={baseY}
            stroke="var(--color-cloudo-accent)"
            strokeOpacity="0.4"
            strokeWidth="1"
            vectorEffect="non-scaling-stroke"
          />
        )}
        {hover !== null && (
          <circle
            cx={points[hover].x}
            cy={points[hover].y}
            r="3.5"
            fill="var(--color-cloudo-accent)"
            stroke="var(--color-cloudo-panel)"
            strokeWidth="2"
            vectorEffect="non-scaling-stroke"
          />
        )}

        {/* Invisible hover targets */}
        {points.map((p, i) => (
          <rect
            key={p.key}
            x={p.x - colWidth / 2}
            y={0}
            width={colWidth}
            height={H}
            fill="transparent"
            onMouseEnter={() => setHover(i)}
          />
        ))}
      </svg>

      {/* HTML tooltip positioned over the active point */}
      {hover !== null && (
        <div
          className="absolute -translate-x-1/2 -translate-y-full pointer-events-none bg-cloudo-accent text-cloudo-dark text-[10px] font-black px-2 py-1 whitespace-nowrap z-20"
          style={{
            left: `${(points[hover].x / W) * 100}%`,
            top: `${(points[hover].y / H) * 100}%`,
          }}
        >
          {points[hover].key}: {points[hover].count} REQ
        </div>
      )}
    </div>
  );
}

function DonutChart({
  segments,
  total,
}: {
  segments: { label: string; value: number; color: string }[];
  total: number;
}) {
  const size = 180;
  const stroke = 24;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const cx = size / 2;
  const cy = size / 2;

  if (total === 0 || segments.length === 0) {
    return (
      <div className="h-44 flex items-center justify-center text-cloudo-muted/60 text-xs italic">
        NO_DATA_AVAILABLE
      </div>
    );
  }

  const arcs = segments.map((s, i) => {
    const offset =
      segments.slice(0, i).reduce((sum, x) => sum + x.value, 0) / total;
    return { ...s, frac: s.value / total, offset };
  });

  return (
    <div className="flex flex-col items-center gap-5">
      <div className="relative" style={{ width: size, height: size }}>
        <svg
          width={size}
          height={size}
          viewBox={`0 0 ${size} ${size}`}
          className="-rotate-90"
        >
          <circle
            cx={cx}
            cy={cy}
            r={r}
            fill="none"
            stroke="var(--color-cloudo-border)"
            strokeOpacity="0.25"
            strokeWidth={stroke}
          />
          {arcs.map((s) => {
            const dash = s.frac * c;
            return (
              <circle
                key={s.label}
                cx={cx}
                cy={cy}
                r={r}
                fill="none"
                stroke={s.color}
                strokeWidth={stroke}
                strokeDasharray={`${dash} ${c - dash}`}
                strokeDashoffset={-s.offset * c}
              />
            );
          })}
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-2xl font-black tracking-tighter text-cloudo-text">
            {total.toLocaleString()}
          </span>
          <span className="text-[9px] font-bold uppercase tracking-[0.3em] text-cloudo-muted/60">
            Total
          </span>
        </div>
      </div>

      {/* Legend */}
      <div className="w-full space-y-2">
        {segments.map((s) => (
          <div
            key={s.label}
            className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-widest"
          >
            <span
              className="w-2.5 h-2.5 shrink-0"
              style={{ backgroundColor: s.color }}
            />
            <span className="text-cloudo-muted flex-1 truncate">{s.label}</span>
            <span className="text-cloudo-text font-mono">
              {((s.value / total) * 100).toFixed(1)}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function MetricCard({
  title,
  value,
  subValue,
  icon,
  trend,
  positive,
  color = "text-cloudo-text",
}: {
  title: string;
  value: string | number;
  subValue: string;
  icon: React.ReactNode;
  trend: string;
  positive: boolean;
  color?: string;
}) {
  return (
    <div className="bg-cloudo-panel border border-cloudo-border p-6 relative overflow-hidden group">
      <div className="absolute top-0 right-0 p-4 opacity-40 group-hover:opacity-50 transition-opacity text-4xl">
        {icon}
      </div>
      <p className="text-[11px] font-black uppercase tracking-[0.2em] text-cloudo-muted/60 mb-1">
        {title}
      </p>
      <div className="flex items-baseline gap-3">
        <h3 className={`text-3xl font-black tracking-tighter ${color}`}>
          {value}
        </h3>
        <span
          className={`text-[10px] font-bold px-1.5 py-0.5 border ${
            positive
              ? "border-cloudo-ok/30 text-cloudo-ok bg-cloudo-ok/5"
              : "border-cloudo-err/30 text-cloudo-err bg-cloudo-err/5"
          }`}
        >
          {trend}
        </span>
      </div>
      <p className="text-[10px] font-bold text-cloudo-muted/70 uppercase mt-2 tracking-widest">
        {subValue}
      </p>
    </div>
  );
}

function SectionHeader({ title }: { title: string }) {
  return (
    <div className="flex items-center gap-3">
      <div className="w-1.5 h-4 bg-cloudo-accent" />
      <h2 className="text-sm font-black uppercase tracking-[0.4em] text-cloudo-text">
        {title}
      </h2>
    </div>
  );
}
