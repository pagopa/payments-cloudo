"use client";

import { useEffect, useState } from "react";
import { cloudoFetch } from "@/lib/api";
import {
  HiOutlineCheckCircle,
  HiOutlineClock,
  HiOutlineTerminal,
  HiOutlineDatabase,
  HiOutlineArrowRight,
  HiOutlineServer,
  HiOutlineLightningBolt,
  HiOutlineTrendingUp,
  HiOutlineChartBar,
} from "react-icons/hi";
import { MdOutlineSpaceDashboard } from "react-icons/md";

interface DashboardStats {
  totalExecutions: number;
  totalFinished: number;
  successRate: number;
  activeWorkers: number;
  pendingApprovals: number;
  recentExecutions: Record<string, unknown>[];
  liveProcesses: Record<string, unknown>[];
  execTrend: number[];
  successTrend: number[];
  statusBreakdown: Record<string, number>;
  topRunbooks: { name: string; count: number; success: number }[];
  failedToday: number;
  avgDurationMs: number;
}

const STATUS_BAR_COLOR: Record<string, string> = {
  succeeded: "bg-cloudo-ok",
  completed: "bg-cloudo-ok",
  failed: "bg-cloudo-err",
  error: "bg-cloudo-err",
  running: "bg-cloudo-accent",
  routed: "bg-cloudo-accent",
  accepted: "bg-cloudo-accent",
  pending: "bg-cloudo-warn",
  rejected: "bg-cloudo-warn",
  stopped: "bg-cloudo-muted",
  skipped: "bg-cloudo-muted",
};

function fmtDuration(ms: number): string {
  if (!ms || ms <= 0) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${Math.round(s % 60)}s`;
}

function greeting(d: Date): string {
  const h = d.getHours();
  if (h < 6) return "Good night";
  if (h < 12) return "Good morning";
  if (h < 18) return "Good afternoon";
  return "Good evening";
}

export default function DashboardPage() {
  const [stats, setStats] = useState<DashboardStats>({
    totalExecutions: 0,
    totalFinished: 0,
    successRate: 0,
    activeWorkers: 0,
    pendingApprovals: 0,
    recentExecutions: [],
    liveProcesses: [],
    execTrend: [],
    successTrend: [],
    statusBreakdown: {},
    topRunbooks: [],
    failedToday: 0,
    avgDurationMs: 0,
  });
  const [loading, setLoading] = useState(true);
  const [isBackendDown, setIsBackendDown] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const [user, setUser] = useState<{ role: string; team: string } | null>(null);

  useEffect(() => {
    const userData = localStorage.getItem("cloudo_user");
    if (userData) {
      try {
        setUser(JSON.parse(userData));
      } catch (e) {
        console.error("Failed to parse user data", e);
      }
    }
    fetchDashboardData();
    const interval = setInterval(fetchDashboardData, 30000);
    return () => clearInterval(interval);
  }, []);

  const fetchDashboardData = async () => {
    try {
      const workersRes = await cloudoFetch(`/workers`);

      if (!workersRes.ok) throw new Error("Backend unreachable");

      const workers = await workersRes.json();
      const activeWorkers = Array.isArray(workers) ? workers : [];

      const processesPromises = activeWorkers.map(
        async (w: { RowKey: string }) => {
          try {
            const res = await cloudoFetch(
              `/workers/processes?worker=${encodeURIComponent(w.RowKey)}`,
            );
            if (!res.ok) return [];
            const data = await res.json();
            const procList = Array.isArray(data)
              ? data
              : data.runs || data.processes || [];
            return procList.map((p: Record<string, unknown>) => ({
              ...p,
              workerNode: w.RowKey,
            }));
          } catch {
            return [];
          }
        },
      );

      const allLiveProcesses = (await Promise.all(processesPromises)).flat();

      const today = new Date();
      const partitionKey = `${today.getFullYear()}${String(
        today.getMonth() + 1,
      ).padStart(2, "0")}${String(today.getDate()).padStart(2, "0")}`;
      const logsRes = await cloudoFetch(
        `/logs/query?partitionKey=${partitionKey}&limit=5000`,
      );
      const logsData = await logsRes.json();

      const executions = logsData.items || [];

      // Group by ExecId and keep only the final status
      const groupedByExecId = new Map<string, Record<string, unknown>>();
      const statusPriority: Record<string, number> = {
        succeeded: 5,
        completed: 5,
        failed: 4,
        error: 4,
        running: 3,
        rejected: 3,
        stopped: 3,
        skipped: 2,
        accepted: 1,
        pending: 1,
        routed: 1,
      };

      executions.forEach((log: Record<string, unknown>) => {
        const execId = log.ExecId as string;
        const existing = groupedByExecId.get(execId);

        if (!existing) {
          groupedByExecId.set(execId, log);
        } else {
          const currentPriority =
            statusPriority[(log.Status as string)?.toLowerCase()] || 0;
          const existingPriority =
            statusPriority[(existing.Status as string)?.toLowerCase()] || 0;

          if (currentPriority > existingPriority) {
            groupedByExecId.set(execId, log);
          } else if (currentPriority === existingPriority) {
            if (
              new Date(log.RequestedAt as string).getTime() >
              new Date(existing.RequestedAt as string).getTime()
            ) {
              groupedByExecId.set(execId, log);
            }
          }
        }
      });

      const finalExecutions = Array.from(groupedByExecId.values());

      const succeeded = finalExecutions.filter((e: Record<string, unknown>) =>
        ["succeeded", "completed"].includes(
          ((e.Status as string) || "").toLowerCase(),
        ),
      ).length;

      const failed = finalExecutions.filter((e: Record<string, unknown>) =>
        ["failed", "error"].includes(
          ((e.Status as string) || "").toLowerCase(),
        ),
      ).length;

      const oneHourAgo = Date.now() - 60 * 60 * 1000; // Timestamp di un'ora fa

      const pending = finalExecutions.filter((e: Record<string, unknown>) => {
        const isPending = ["pending"].includes(
          ((e.Status as string) || "").toLowerCase(),
        );
        const executionTime = new Date(e.CreatedAt as string).getTime();
        return isPending && executionTime > oneHourAgo;
      }).length;

      const sortedExecutions = [...finalExecutions]
        .sort(
          (a, b) =>
            new Date((b.RequestedAt as string) || 0).getTime() -
            new Date((a.RequestedAt as string) || 0).getTime(),
        )
        .slice(0, 5);

      // Real hourly trends over the last 12 hours (no more fake sparklines).
      const HOURS = 12;
      const nowTs = Date.now();
      const execBuckets = new Array(HOURS).fill(0);
      const succBuckets = new Array(HOURS).fill(0);
      const finishedBuckets = new Array(HOURS).fill(0);

      finalExecutions.forEach((e: Record<string, unknown>) => {
        const t = new Date(e.RequestedAt as string).getTime();
        if (isNaN(t)) return;
        const hoursAgo = Math.floor((nowTs - t) / 3600000);
        if (hoursAgo < 0 || hoursAgo >= HOURS) return;
        const idx = HOURS - 1 - hoursAgo;
        execBuckets[idx]++;
        const st = ((e.Status as string) || "").toLowerCase();
        if (["succeeded", "completed", "failed", "error"].includes(st)) {
          finishedBuckets[idx]++;
          if (["succeeded", "completed"].includes(st)) succBuckets[idx]++;
        }
      });

      const execTrend = execBuckets;
      const successTrend = finishedBuckets.map((tot, i) =>
        tot > 0 ? (succBuckets[i] / tot) * 100 : 0,
      );

      // Status distribution + top runbooks from final executions.
      const statusBreakdown: Record<string, number> = {};
      const runbookMap: Record<string, { count: number; success: number }> = {};
      finalExecutions.forEach((e: Record<string, unknown>) => {
        const st = ((e.Status as string) || "unknown").toLowerCase();
        statusBreakdown[st] = (statusBreakdown[st] || 0) + 1;
        const rb = (e.Runbook as string) || "Unknown";
        const entry = (runbookMap[rb] ??= { count: 0, success: 0 });
        entry.count++;
        if (["succeeded", "completed"].includes(st)) entry.success++;
      });
      const topRunbooks = Object.entries(runbookMap)
        .map(([name, s]) => ({ name, count: s.count, success: s.success }))
        .sort((a, b) => b.count - a.count)
        .slice(0, 5);

      // Average execution duration (min in-progress -> max terminal per ExecId).
      const durMap = new Map<string, { start?: number; end?: number }>();
      executions.forEach((log: Record<string, unknown>) => {
        const id = log.ExecId as string;
        if (!id) return;
        const ts = new Date(log.RequestedAt as string).getTime();
        if (isNaN(ts)) return;
        const st = ((log.Status as string) || "").toLowerCase();
        let d = durMap.get(id);
        if (!d) {
          d = {};
          durMap.set(id, d);
        }
        if (["accepted", "pending", "routed", "scheduled"].includes(st)) {
          if (d.start === undefined || ts < d.start) d.start = ts;
        }
        if (["succeeded", "completed", "failed", "error"].includes(st)) {
          if (d.end === undefined || ts > d.end) d.end = ts;
        }
      });
      let durSum = 0;
      let durCount = 0;
      durMap.forEach((d) => {
        if (d.start !== undefined && d.end !== undefined && d.end >= d.start) {
          durSum += d.end - d.start;
          durCount++;
        }
      });
      const avgDurationMs = durCount > 0 ? durSum / durCount : 0;

      const totalFinished = succeeded + failed;
      setStats({
        totalExecutions: finalExecutions.length,
        totalFinished: totalFinished,
        successRate:
          totalFinished > 0
            ? +((succeeded / totalFinished) * 100).toFixed(2)
            : 0,
        activeWorkers: activeWorkers.length,
        pendingApprovals: pending,
        recentExecutions: sortedExecutions,
        liveProcesses: allLiveProcesses as Record<string, unknown>[],
        execTrend,
        successTrend,
        statusBreakdown,
        topRunbooks,
        failedToday: failed,
        avgDurationMs,
      });
      setIsBackendDown(false);
      setLastUpdated(new Date());
    } catch (error) {
      console.error("Error fetching dashboard data:", error);
      setIsBackendDown(true);
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center h-full bg-cloudo-dark">
        <div className="w-8 h-8 border-2 border-cloudo-accent/30 border-t-cloudo-accent rounded-full animate-spin mb-4" />
        <span className="text-xs font-black uppercase tracking-[0.3em] text-cloudo-muted">
          Booting Systems...
        </span>
      </div>
    );
  }

  return (
    <div
      className="flex flex-col h-full bg-cloudo-dark text-cloudo-text font-mono selection:bg-cloudo-accent/30"
      data-user-role={user?.role || undefined}
    >
      {/* Header Bar */}
      <div className="flex items-center justify-between px-8 py-4 border-b border-cloudo-border bg-cloudo-panel sticky top-0 z-20">
        <div className="flex items-center gap-4 shrink-0">
          <div className="p-2 bg-cloudo-accent/5 border border-cloudo-accent/20 shrink-0">
            <MdOutlineSpaceDashboard className="text-cloudo-accent w-5 h-5" />
          </div>
          <div>
            <h1 className="text-sm font-black tracking-[0.2em] text-cloudo-text uppercase">
              Operations Dashboard
            </h1>
            <p className="text-[11px] text-cloudo-muted font-bold uppercase tracking-[0.3em] opacity-70">
              System Telemetry // LIVE
            </p>
          </div>
        </div>
        <div className="flex items-center gap-4">
          {lastUpdated && !isBackendDown && (
            <span className="hidden md:flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-widest text-cloudo-muted/60">
              <HiOutlineClock className="w-3.5 h-3.5" />
              Sync {lastUpdated.toLocaleTimeString([], { hour12: false })}
            </span>
          )}
          <div className="flex items-center gap-2 px-3 py-1 bg-cloudo-accent/10 border border-cloudo-border">
            <span className="relative flex h-2 w-2">
              <span
                className={`animate-ping absolute inline-flex h-full w-full rounded-full opacity-70 ${
                  isBackendDown ? "bg-cloudo-err" : "bg-cloudo-ok"
                }`}
              ></span>
              <span
                className={`relative inline-flex rounded-full h-2 w-2 ${
                  isBackendDown ? "bg-cloudo-err" : "bg-cloudo-ok"
                }`}
              ></span>
            </span>
            <span
              className={`text-[11px] font-black uppercase tracking-widest ${
                isBackendDown
                  ? "text-cloudo-err animate-pulse"
                  : "text-cloudo-muted"
              }`}
            >
              {isBackendDown
                ? "CONNECTION_LOST"
                : "UPLINK_STABLE | Live Stream • 30s"}
            </span>
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-auto p-5 sm:p-6 lg:p-8">
        <div className="max-w-[1400px] mx-auto space-y-6">
          {/* Welcome Hero */}
          <div className="relative overflow-hidden bg-cloudo-panel border border-cloudo-border p-5 sm:p-6">
            <div className="absolute -right-10 -top-10 w-48 h-48 bg-cloudo-accent/5 rounded-full blur-3xl pointer-events-none" />
            <div className="relative flex flex-col md:flex-row md:items-center md:justify-between gap-4">
              <div className="space-y-1.5">
                <p className="text-[11px] font-black uppercase tracking-[0.3em] text-cloudo-accent">
                  {greeting(new Date())}
                  {user?.role ? ` // ${user.role}` : ""}
                  {user?.team ? ` // ${user.team}` : ""}
                </p>
                <h2 className="text-xl font-black tracking-tight text-cloudo-text">
                  Welcome back to Cloudo
                </h2>
                <p className="text-[12px] font-bold text-cloudo-muted/80 tracking-wide max-w-xl">
                  {stats.totalExecutions.toLocaleString()} workloads today ·{" "}
                  <span className="text-cloudo-ok">{stats.successRate}%</span>{" "}
                  success ·{" "}
                  <span className="text-cloudo-text">
                    {stats.activeWorkers}
                  </span>{" "}
                  active nodes ·{" "}
                  <span
                    className={
                      stats.liveProcesses.length > 0
                        ? "text-cloudo-accent"
                        : "text-cloudo-muted"
                    }
                  >
                    {stats.liveProcesses.length} running
                  </span>
                </p>
              </div>
              <div className="flex items-center gap-3 shrink-0">
                <a
                  href="/studio"
                  className="flex-1 md:flex-none justify-center flex items-center gap-2 px-5 py-2.5 bg-cloudo-accent text-cloudo-dark text-[11px] font-black uppercase tracking-widest hover:opacity-90 transition-all"
                >
                  <HiOutlineLightningBolt className="w-4 h-4" />
                  Open Studio
                </a>
                <a
                  href="/executions"
                  className="flex-1 md:flex-none justify-center flex items-center gap-2 px-5 py-2.5 border border-cloudo-border text-cloudo-text text-[11px] font-black uppercase tracking-widest hover:border-cloudo-accent hover:text-cloudo-accent transition-all"
                >
                  <HiOutlineChartBar className="w-4 h-4" />
                  Executions
                </a>
              </div>
            </div>
          </div>

          {/* Stats Cards Row */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4">
            <StatCard
              title="Workload Executions"
              value={stats.totalExecutions}
              icon={<HiOutlineTerminal className="text-cloudo-accent" />}
              status="TOTAL_LOAD"
              trend={stats.execTrend}
            />
            <StatCard
              title="Success Rate"
              value={`${stats.successRate}%`}
              icon={<HiOutlineCheckCircle className="text-cloudo-ok" />}
              status="COMPLIANCE_RATIO"
              trend={stats.successTrend}
              trendColor="var(--color-cloudo-ok)"
            />
            <StatCard
              title="Compute Nodes"
              value={stats.activeWorkers}
              icon={<HiOutlineServer className="text-cloudo-accent" />}
              status="ACTIVE_CAPACITY"
            />
            <StatCard
              title="Governance Queue"
              value={stats.pendingApprovals}
              icon={<HiOutlineClock className="text-cloudo-warn" />}
              status="AWAITING_SIG"
              highlight={stats.pendingApprovals > 0}
            />
          </div>

          {/* Insight Widgets Row */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 items-stretch">
            {/* System Health */}
            <div className="flex flex-col gap-4">
              <SectionTitle title="Runbook Status" />
              <div className="bg-cloudo-panel border border-cloudo-border p-5 sm:p-6 space-y-5 flex-1">
                <div className="flex items-baseline justify-between">
                  <span className="text-[11px] font-black uppercase tracking-[0.2em] text-cloudo-muted/70">
                    Reliability
                  </span>
                  <span
                    className={`text-2xl font-black tracking-tighter ${
                      stats.successRate >= 95
                        ? "text-cloudo-ok"
                        : stats.successRate >= 80
                          ? "text-cloudo-warn"
                          : "text-cloudo-err"
                    }`}
                  >
                    {stats.successRate}%
                  </span>
                </div>

                {/* Stacked status bar */}
                <div className="space-y-3">
                  <div className="flex w-full h-2.5 overflow-hidden bg-white/5">
                    {Object.entries(stats.statusBreakdown).length === 0 ? (
                      <div className="w-full bg-cloudo-muted/20" />
                    ) : (
                      Object.entries(stats.statusBreakdown)
                        .sort((a, b) => b[1] - a[1])
                        .map(([st, count]) => (
                          <div
                            key={st}
                            className={`${
                              STATUS_BAR_COLOR[st] || "bg-cloudo-muted"
                            } h-full`}
                            style={{
                              width: `${
                                (count / Math.max(stats.totalExecutions, 1)) *
                                100
                              }%`,
                            }}
                            title={`${st}: ${count}`}
                          />
                        ))
                    )}
                  </div>
                  <div className="grid grid-cols-2 gap-x-4 gap-y-2">
                    {Object.entries(stats.statusBreakdown)
                      .sort((a, b) => b[1] - a[1])
                      .slice(0, 6)
                      .map(([st, count]) => (
                        <div
                          key={st}
                          className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-widest"
                        >
                          <span
                            className={`w-2 h-2 shrink-0 ${
                              STATUS_BAR_COLOR[st] || "bg-cloudo-muted"
                            }`}
                          />
                          <span className="text-cloudo-muted truncate flex-1">
                            {st}
                          </span>
                          <span className="text-cloudo-text font-mono">
                            {count}
                          </span>
                        </div>
                      ))}
                    {Object.keys(stats.statusBreakdown).length === 0 && (
                      <span className="text-[10px] text-cloudo-muted/50 italic uppercase tracking-widest">
                        NO_DATA
                      </span>
                    )}
                  </div>
                </div>
              </div>
            </div>

            {/* Top Runbooks */}
            <div className="flex flex-col gap-4">
              <SectionTitle title="Top Runbooks" href="/analytics" />
              <div className="bg-cloudo-panel border border-cloudo-border p-5 sm:p-6 flex-1">
                {stats.topRunbooks.length === 0 ? (
                  <div className="py-12 text-center text-cloudo-muted/50 text-xs italic uppercase tracking-widest">
                    NO_ACTIVE_RUNBOOKS
                  </div>
                ) : (
                  <div className="space-y-4">
                    {stats.topRunbooks.map((rb) => {
                      const rate = rb.count ? (rb.success / rb.count) * 100 : 0;
                      return (
                        <div key={rb.name} className="space-y-1.5">
                          <div className="flex items-center justify-between text-[11px] font-bold uppercase tracking-widest">
                            <span className="text-cloudo-text truncate max-w-[60%]">
                              {rb.name}
                            </span>
                            <span className="text-cloudo-muted font-mono">
                              {rb.count} · {rate.toFixed(0)}%
                            </span>
                          </div>
                          <div className="w-full h-1 bg-white/5 overflow-hidden">
                            <div
                              className={`h-full ${
                                rate >= 95
                                  ? "bg-cloudo-ok"
                                  : rate >= 70
                                    ? "bg-cloudo-warn"
                                    : "bg-cloudo-err"
                              }`}
                              style={{ width: `${rate}%` }}
                            />
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </div>

            {/* Throughput & Latency */}
            <div className="flex flex-col gap-4">
              <SectionTitle title="Throughput & Latency" />
              <div className="bg-cloudo-panel border border-cloudo-border p-5 sm:p-6 flex-1 flex flex-col gap-5">
                <div className="flex items-end justify-between">
                  <div>
                    <p className="text-[10px] font-black uppercase tracking-[0.2em] text-cloudo-muted/60">
                      Avg Duration
                    </p>
                    <p className="text-2xl font-black tracking-tighter text-cloudo-text mt-1">
                      {fmtDuration(stats.avgDurationMs)}
                    </p>
                  </div>
                  <div className="text-right">
                    <p className="text-[10px] font-black uppercase tracking-[0.2em] text-cloudo-muted/60">
                      Failed Today
                    </p>
                    <p
                      className={`text-2xl font-black tracking-tighter mt-1 ${
                        stats.failedToday > 0
                          ? "text-cloudo-err"
                          : "text-cloudo-ok"
                      }`}
                    >
                      {stats.failedToday}
                    </p>
                  </div>
                </div>

                <div className="mt-auto space-y-2">
                  <div className="flex items-center justify-between text-[10px] font-black uppercase tracking-widest text-cloudo-muted/60">
                    <span className="flex items-center gap-1.5">
                      <HiOutlineTrendingUp className="w-3.5 h-3.5" />
                      12h Throughput
                    </span>
                    <span className="font-mono text-cloudo-text">
                      {stats.execTrend.reduce((a, b) => a + b, 0)} exec
                    </span>
                  </div>
                  <div className="bg-cloudo-dark/40 border border-cloudo-border/50 p-3">
                    <BigSparkline data={stats.execTrend} />
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* Operational Stream e Live Worker Processes */}
            <div className="lg:col-span-2 flex flex-col gap-4">
              <div className="flex items-center gap-3">
                <div className="w-1.5 h-4 bg-cloudo-accent" />
                <h2 className="text-sm font-black uppercase tracking-[0.4em] text-cloudo-text">
                  Operational Stream
                </h2>
                <a
                  href="/executions"
                  className="ml-auto text-[10px] font-black uppercase tracking-widest text-cloudo-accent hover:text-cloudo-text transition-colors flex items-center gap-1"
                >
                  View All <HiOutlineArrowRight className="w-3 h-3" />
                </a>
              </div>
              <div className="bg-cloudo-panel border border-cloudo-border overflow-hidden">
                <table className="w-full text-left border-collapse text-sm">
                  <thead className="bg-cloudo-panel-2 border-b border-cloudo-border">
                    <tr className="text-[11px] font-black text-cloudo-muted uppercase tracking-[0.3em]">
                      <th className="px-6 py-4">Event_ID</th>
                      <th className="px-6 py-4">Asset_Path</th>
                      <th className="px-6 py-4">Status</th>
                      <th className="px-6 py-4 text-right">Timestamp</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-cloudo-border/50">
                    {stats.recentExecutions.length === 0 ? (
                      <tr>
                        <td
                          colSpan={4}
                          className="py-20 text-center text-sm uppercase font-bold text-cloudo-muted italic opacity-60"
                        >
                          NO_DATA_STREAM
                        </td>
                      </tr>
                    ) : (
                      stats.recentExecutions.map(
                        (exec: Record<string, unknown>) => (
                          <tr
                            key={exec.RowKey as string}
                            className="group hover:bg-white/[0.02] transition-colors cursor-pointer"
                            onClick={() => {
                              window.location.href = `/executions?execId=${exec.ExecId}&partitionKey=${exec.PartitionKey}`;
                            }}
                          >
                            <td className="px-6 py-4 font-mono">
                              <div className="text-cloudo-text font-bold">
                                {(exec.ExecId as string)?.slice(0, 8)}
                              </div>
                              <div className="text-[11px] text-cloudo-muted/60">
                                {(exec.Name as string) || "SYS_TASK"}
                              </div>
                            </td>
                            <td className="px-6 py-4 font-mono hover:text-cloudo-accent">
                              {(exec.Runbook as string) || "--"}
                            </td>
                            <td className="px-6 py-4">
                              <StatusIndicator status={exec.Status as string} />
                            </td>
                            <td className="px-6 py-4 text-right font-mono text-cloudo-muted">
                              {new Date(
                                exec.RequestedAt as string,
                              ).toLocaleTimeString([], {
                                hour: "2-digit",
                                minute: "2-digit",
                                second: "2-digit",
                                hour12: false,
                              })}
                            </td>
                          </tr>
                        ),
                      )
                    )}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="flex flex-col gap-4">
              <div className="flex items-center gap-3">
                <div className="w-1.5 h-4 bg-cloudo-accent" />
                <h2 className="text-sm font-black uppercase tracking-[0.4em] text-cloudo-text">
                  Runtime Processes
                </h2>
                <a
                  href="/workers"
                  className="ml-auto text-[10px] font-black uppercase tracking-widest text-cloudo-accent hover:text-cloudo-text transition-colors flex items-center gap-1"
                >
                  View All <HiOutlineArrowRight className="w-3 h-3" />
                </a>
              </div>
              <div className="bg-cloudo-panel border border-cloudo-border p-4 space-y-3 flex-1 min-h-[320px] max-h-[500px] overflow-y-auto custom-scrollbar">
                {stats.liveProcesses.length === 0 ? (
                  <div className="py-20 text-center opacity-50 flex flex-col items-center gap-3">
                    <HiOutlineServer className="w-8 h-8" />
                    <span className="text-[11px] font-black uppercase tracking-widest text-center">
                      IDLE_STATE
                      <br />
                      NO_ACTIVE_WORKLOADS
                    </span>
                  </div>
                ) : (
                  stats.liveProcesses.map((proc: Record<string, unknown>) => (
                    <div
                      key={proc.exec_id as string}
                      className="bg-cloudo-accent/10 border border-cloudo-border p-3 border-l-2 border-l-cloudo-accent group hover:bg-cloudo-accent/5 transition-all cursor-pointer"
                      onClick={() => {
                        window.location.href = `/workers`;
                      }}
                    >
                      <div className="flex justify-between items-start mb-2">
                        <div className="flex items-center gap-2">
                          <div className="w-1.5 h-1.5 bg-cloudo-accent animate-pulse" />
                          <span className="text-sm font-bold text-cloudo-text truncate max-w-[140px] uppercase tracking-widest">
                            {proc.name as string}
                          </span>
                        </div>
                        <span className="text-[11px] font-mono text-cloudo-muted/60">
                          {String(proc.workerNode)}
                        </span>
                      </div>
                      <div className="flex justify-between items-center text-[11px] text-cloudo-muted uppercase font-bold tracking-widest">
                        <span className="flex items-center gap-1 opacity-60">
                          <HiOutlineTerminal className="w-4 h-4" />{" "}
                          {proc.runbook as string}
                        </span>
                        <span className="opacity-60">
                          {String(proc.exec_id).slice(0, 8)}
                        </span>
                      </div>
                    </div>
                  ))
                )}
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-3 lg:grid-cols-1 gap-2">
                <QuickLink
                  icon={<HiOutlineDatabase />}
                  label="Schemas"
                  href="/schemas"
                />
                <QuickLink
                  icon={<HiOutlineClock />}
                  label="Schedules"
                  href="/schedules"
                />
                <QuickLink
                  icon={<HiOutlineServer />}
                  label="Compute"
                  href="/workers"
                />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function StatCard({
  title,
  value,
  icon,
  status,
  highlight = false,
  trend,
  trendColor = "var(--color-cloudo-accent)",
}: {
  title: string;
  value: string | number;
  icon: React.ReactNode;
  status: string;
  highlight?: boolean;
  trend?: number[];
  trendColor?: string;
}) {
  const hasTrend = trend && trend.some((v) => v > 0);
  return (
    <div className="bg-cloudo-panel border border-cloudo-border p-4 sm:p-6 flex items-center justify-between gap-3 relative overflow-hidden group">
      <div className="absolute top-0 left-0 w-[2px] h-full bg-cloudo-accent/20" />
      <div className="relative z-10 min-w-0">
        <p className="text-[10px] sm:text-[11px] font-black uppercase tracking-[0.2em] text-cloudo-muted/80 truncate">
          {title}
        </p>
        <p
          className={`text-2xl sm:text-3xl font-black mt-1 ${
            highlight ? "text-cloudo-warn" : "text-cloudo-text"
          } tracking-tighter`}
        >
          {value}
        </p>
        <div className="flex items-center gap-2 mt-2">
          <p className="text-[10px] sm:text-[11px] font-bold text-cloudo-muted/80 uppercase tracking-[0.1em] truncate">
            {status}
          </p>
          {hasTrend && <Sparkline data={trend!} color={trendColor} />}
        </div>
      </div>
      <div className="hidden sm:block p-3 bg-cloudo-accent/10 border border-cloudo-border text-xl shrink-0">
        {icon}
      </div>
    </div>
  );
}

function Sparkline({
  data,
  color = "var(--color-cloudo-accent)",
}: {
  data: number[];
  color?: string;
}) {
  if (!data || data.length === 0) return null;
  const W = 72;
  const H = 18;
  const max = Math.max(...data);
  const min = Math.min(...data);
  const range = max - min || 1;
  const n = data.length;
  const pts = data.map((v, i) => {
    const x = n > 1 ? (i / (n - 1)) * W : W / 2;
    const y = H - 2 - ((v - min) / range) * (H - 4);
    return [x, y] as const;
  });
  const line = pts
    .map(
      (p, i) => `${i === 0 ? "M" : "L"}${p[0].toFixed(1)},${p[1].toFixed(1)}`,
    )
    .join(" ");
  const area = `${line} L${W},${H} L0,${H} Z`;
  const last = pts[n - 1];
  const gradId = `spark-${color.replace(/[^a-z]/gi, "")}`;

  return (
    <svg
      width={W}
      height={H}
      viewBox={`0 0 ${W} ${H}`}
      className="overflow-visible opacity-80 group-hover:opacity-100 transition-opacity"
    >
      <defs>
        <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.35" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${gradId})`} />
      <path
        d={line}
        fill="none"
        stroke={color}
        strokeWidth="1.5"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      <circle cx={last[0]} cy={last[1]} r="1.6" fill={color} />
    </svg>
  );
}

function SectionTitle({ title, href }: { title: string; href?: string }) {
  return (
    <div className="flex items-center gap-3">
      <div className="w-1.5 h-4 bg-cloudo-accent" />
      <h2 className="text-sm font-black uppercase tracking-[0.4em] text-cloudo-text">
        {title}
      </h2>
      {href && (
        <a
          href={href}
          className="ml-auto text-[10px] font-black uppercase tracking-widest text-cloudo-accent hover:text-cloudo-text transition-colors flex items-center gap-1"
        >
          View All <HiOutlineArrowRight className="w-3 h-3" />
        </a>
      )}
    </div>
  );
}

function BigSparkline({ data }: { data: number[] }) {
  const color = "var(--color-cloudo-accent)";
  if (!data || data.length === 0) {
    return (
      <div className="h-12 flex items-center justify-center text-[10px] text-cloudo-muted/50 italic uppercase tracking-widest">
        NO_TREND_DATA
      </div>
    );
  }
  const W = 280;
  const H = 48;
  const max = Math.max(...data, 1);
  const n = data.length;
  const pts = data.map((v, i) => {
    const x = n > 1 ? (i / (n - 1)) * W : W / 2;
    const y = H - 4 - (v / max) * (H - 8);
    return [x, y] as const;
  });
  const line = pts
    .map(
      (p, i) => `${i === 0 ? "M" : "L"}${p[0].toFixed(1)},${p[1].toFixed(1)}`,
    )
    .join(" ");
  const area = `${line} L${W},${H} L0,${H} Z`;
  return (
    <svg
      width="100%"
      height={H}
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      className="block"
    >
      <defs>
        <linearGradient id="bigSpark" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.35" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill="url(#bigSpark)" />
      <path
        d={line}
        fill="none"
        stroke={color}
        strokeWidth="1.5"
        vectorEffect="non-scaling-stroke"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}

function StatusIndicator({ status }: { status: string }) {
  const s = (status || "").toLowerCase();
  const colors: Record<string, string> = {
    succeeded: "bg-cloudo-ok",
    running: "bg-cloudo-accent",
    routed: "bg-cloudo-accent",
    failed: "bg-cloudo-err",
    error: "bg-cloudo-err",
    pending: "bg-cloudo-warn",
    accepted: "bg-cloudo-warn",
  };

  const isRunning = s === "running" || s === "accepted" || s === "routed";

  return (
    <div className="flex items-center gap-2">
      <div
        className={`w-2 h-2 ${colors[s] || "bg-cloudo-muted"} ${
          isRunning ? "animate-pulse ring-2 ring-cloudo-accent/30" : ""
        } rounded-full`}
      />
      <span className="text-xs font-black uppercase tracking-widest text-cloudo-text/80">
        {status}
      </span>
    </div>
  );
}

function QuickLink({
  icon,
  label,
  href,
}: {
  icon: React.ReactNode;
  label: string;
  href: string;
}) {
  return (
    <a
      href={href}
      className="flex items-center justify-between p-4 bg-cloudo-panel border border-cloudo-border hover:bg-cloudo-accent/5 hover:border-cloudo-accent transition-all group"
    >
      <div className="flex items-center gap-3 min-w-0">
        <div className="text-cloudo-muted group-hover:text-cloudo-accent transition-colors shrink-0">
          {icon}
        </div>
        <span className="text-xs font-black text-cloudo-text uppercase tracking-[0.2em] truncate">
          {label}
        </span>
      </div>
      <HiOutlineArrowRight className="text-cloudo-muted/70 group-hover:text-cloudo-accent transition-all transform group-hover:translate-x-1 shrink-0 w-4 h-4" />
    </a>
  );
}
