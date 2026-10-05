"use client";

import { useState, useEffect, useCallback } from "react";
import { cloudoFetch } from "@/lib/api";
import { formatRome } from "@/lib/time";
import {
  HiOutlineRefresh,
  HiOutlinePlay,
  HiOutlineServer,
  HiOutlineDatabase,
  HiOutlineChip,
  HiOutlineTerminal,
  HiOutlineSearch,
  HiOutlineExclamation,
  HiOutlineCheckCircle,
  HiOutlineExclamationCircle,
  HiOutlineX,
} from "react-icons/hi";
import { motion, AnimatePresence } from "framer-motion";

interface WorkerProcess {
  exec_id: string;
  name: string;
  id: string;
  runbook: string;
  status: string;
  startedAt?: string;
  requestedAt?: string;
  workerNode?: string;
  team?: string;
  can_stop?: boolean;
}

interface Worker {
  PartitionKey: string;
  RowKey: string;
  Queue: string;
  LastSeen: string;
  Region: string;
  Load: number;
  team?: string;
}

interface Notification {
  id: string;
  type: "success" | "error";
  message: string;
}

export function WorkersPanel() {
  const [workers, setWorkers] = useState<Worker[]>([]);
  const [selectedWorker, setSelectedWorker] = useState<string>("all");
  const [processes, setProcesses] = useState<WorkerProcess[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadingWorkers, setLoadingWorkers] = useState(true);
  const [user, setUser] = useState<{ role: string } | null>(null);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [processToStop, setProcessToStop] = useState<{
    worker: string;
    execId: string;
    id: string;
    name: string;
  } | null>(null);

  const addNotification = (type: "success" | "error", message: string) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    setNotifications((prev) => [...prev, { id, type, message }]);
    setTimeout(() => {
      setNotifications((prev) => prev.filter((n) => n.id !== id));
    }, 4000);
  };

  const removeNotification = (id: string) => {
    setNotifications((prev) => prev.filter((n) => n.id !== id));
  };

  const isViewer = user?.role === "VIEWER";

  const fetchWorkers = async () => {
    setLoadingWorkers(true);
    try {
      const res = await cloudoFetch(`/workers?for=monitoring`);
      const data = await res.json();
      setWorkers(Array.isArray(data) ? data : []);
    } catch (error) {
      console.error("Error fetching workers:", error);
      setWorkers([]);
    } finally {
      setLoadingWorkers(false);
    }
  };

  useEffect(() => {
    fetchWorkers();
    const userData = localStorage.getItem("cloudo_user");
    if (userData) {
      try {
        setUser(JSON.parse(userData));
      } catch (e) {
        console.error("Failed to parse user data", e);
      }
    }
  }, []);

  const fetchProcesses = useCallback(
    async (worker: string) => {
      if (!worker) return;
      setLoading(true);
      try {
        if (worker === "all") {
          const allProcesses = await Promise.all(
            workers.map(async (w) => {
              try {
                const res = await cloudoFetch(
                  `/workers/processes?worker=${encodeURIComponent(w.RowKey)}`,
                );
                if (!res.ok) return [];
                const data = await res.json();
                const items = Array.isArray(data)
                  ? data
                  : data.runs || data.processes || [];
                return items.map((item: Record<string, unknown>) => ({
                  ...item,
                  workerNode: w.RowKey,
                }));
              } catch (e) {
                console.error(`Error fetching processes for ${w.RowKey}:`, e);
                return [];
              }
            }),
          );
          setProcesses(allProcesses.flat() as unknown as WorkerProcess[]);
        } else {
          const res = await cloudoFetch(
            `/workers/processes?worker=${encodeURIComponent(worker)}`,
          );
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const data = await res.json();
          const processesData = Array.isArray(data)
            ? data
            : data.runs || data.processes || [];
          setProcesses(
            processesData.map((p: Record<string, unknown>) => ({
              ...p,
              workerNode: worker,
            })) as unknown as WorkerProcess[],
          );
        }
      } catch (error) {
        console.error("Error fetching processes:", error);
        setProcesses([]);
      } finally {
        setLoading(false);
      }
    },
    [workers],
  );

  useEffect(() => {
    if (workers.length > 0 && selectedWorker === "all") {
      fetchProcesses("all");
    }
  }, [workers, selectedWorker, fetchProcesses]);

  const stopProcess = async (worker: string, execId: string) => {
    setLoading(true);
    setProcessToStop(null);
    try {
      const res = await cloudoFetch(
        `/workers/stop?worker=${encodeURIComponent(
          worker,
        )}&exec_id=${encodeURIComponent(execId)}`,
        {
          method: "POST",
        },
      );

      if (res.ok) {
        addNotification(
          "success",
          `Process ${execId.slice(0, 8)}... stopped on ${worker}`,
        );
        // Refresh processes after stop
        fetchProcesses(selectedWorker);
      } else {
        const data = await res.json();
        addNotification(
          "error",
          `Failed to stop process: ${data.error || res.statusText}`,
        );
      }
    } catch (error) {
      console.error("Error stopping process:", error);
      addNotification("error", "Network error while stopping process");
    } finally {
      setLoading(false);
    }
  };
  // BUG exec id duplciato e non prende quello giusto
  const getStatusBadgeClass = (status: string) => {
    const s = status.toLowerCase();
    if (s === "succeeded" || s === "completed")
      return "border-cloudo-ok/30 text-cloudo-ok bg-cloudo-ok/5";
    if (s === "running")
      return "border-cloudo-accent/30 text-cloudo-accent bg-cloudo-accent/5";
    if (s === "failed" || s === "error")
      return "border-cloudo-err/30 text-cloudo-err bg-cloudo-err/5";
    return "border-cloudo-muted/60 text-cloudo-muted bg-cloudo-muted/5";
  };

  const workersByCapability = workers.reduce(
    (acc, worker) => {
      const cap = worker.PartitionKey || "unknown";
      if (!acc[cap]) acc[cap] = [];
      acc[cap].push(worker);
      return acc;
    },
    {} as Record<string, Worker[]>,
  );

  const parseLastSeen = (value?: string) => {
    if (!value) return null;

    const raw = String(value).trim();
    const numeric = Number(raw);
    if (!Number.isNaN(numeric) && raw !== "") {
      const ts = raw.length <= 10 ? numeric * 1000 : numeric;
      const parsedNumeric = new Date(ts);
      if (!Number.isNaN(parsedNumeric.getTime())) return parsedNumeric;
    }

    const normalized = raw.includes("T") ? raw : raw.replace(" ", "T");
    const parsed = new Date(normalized);
    if (Number.isNaN(parsed.getTime())) return null;
    return parsed;
  };

  const getHeartbeatState = (
    value?: string,
  ): "online" | "stale" | "unknown" => {
    const parsed = parseLastSeen(value);
    if (!parsed) return "unknown";
    const diffMinutes = (Date.now() - parsed.getTime()) / (1000 * 60);
    return diffMinutes <= 2 ? "online" : "stale";
  };

  const formatLastSeen = (value?: string) => {
    const parsed = parseLastSeen(value);
    if (!parsed) return value || "-";
    return formatRome(parsed);
  };

  const formatHeartbeatAge = (value?: string) => {
    const parsed = parseLastSeen(value);
    if (!parsed) return "-";
    const diffMs = Date.now() - parsed.getTime();
    if (diffMs < 60 * 1000) return "now";
    const minutes = Math.floor(diffMs / (60 * 1000));
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    return `${days}d ago`;
  };

  const totalWorkers = workers.length;
  const capabilityCount = Object.keys(workersByCapability).length;
  const activeQueues = new Set(
    workers
      .map((worker) => (worker.Queue || "").trim())
      .filter((queue) => queue.length > 0),
  ).size;
  const staleWorkers = workers.filter(
    (worker) => getHeartbeatState(worker.LastSeen) === "stale",
  ).length;
  const unknownHeartbeatWorkers = workers.filter(
    (worker) => getHeartbeatState(worker.LastSeen) === "unknown",
  ).length;
  const heartbeatIssues = staleWorkers + unknownHeartbeatWorkers;

  return (
    <div className="flex flex-col h-full bg-cloudo-dark text-cloudo-text font-mono relative">
      {/* Notifications */}
      <div className="fixed top-8 right-8 z-[100] flex flex-col gap-3 pointer-events-none">
        {notifications.map((n) => (
          <div
            key={n.id}
            className={`px-6 py-4 flex items-center gap-4 animate-in slide-in-from-right-full duration-300 border shadow-2xl pointer-events-auto min-w-[300px] relative overflow-hidden ${
              n.type === "success"
                ? "bg-cloudo-panel border-cloudo-ok/30 text-cloudo-ok"
                : "bg-cloudo-panel border-cloudo-err/30 text-cloudo-err"
            }`}
          >
            {/* Background Accent */}
            <div
              className={`absolute top-0 left-0 w-1 h-full ${
                n.type === "success" ? "bg-cloudo-ok" : "bg-cloudo-err"
              }`}
            />

            <div
              className={`p-2 ${
                n.type === "success" ? "bg-cloudo-ok/10" : "bg-cloudo-err/10"
              } shrink-0`}
            >
              {n.type === "success" ? (
                <HiOutlineCheckCircle className="w-5 h-5" />
              ) : (
                <HiOutlineExclamationCircle className="w-5 h-5" />
              )}
            </div>

            <div className="flex flex-col gap-1 flex-1">
              <span className="text-[10px] font-black uppercase tracking-[0.2em]">
                {n.type === "success" ? "System Success" : "Engine Error"}
              </span>
              <span className="text-[11px] font-bold text-cloudo-text/90 uppercase tracking-widest leading-tight">
                {n.message}
              </span>
            </div>

            <button
              onClick={() => removeNotification(n.id)}
              className="p-1 hover:bg-white/5 transition-colors opacity-40 hover:opacity-100"
            >
              <HiOutlineX className="w-3.5 h-3.5" />
            </button>
          </div>
        ))}
      </div>

      {/* Confirmation Modal */}
      <AnimatePresence>
        {processToStop && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 bg-cloudo-dark/90 backdrop-blur-sm flex items-center justify-center z-[110] p-4"
            onClick={() => setProcessToStop(null)}
          >
            <motion.div
              initial={{ scale: 0.9, opacity: 0, y: 20 }}
              animate={{ scale: 1, opacity: 1, y: 0 }}
              exit={{ scale: 0.9, opacity: 0, y: 20 }}
              className="bg-cloudo-panel border border-cloudo-err/30 shadow-2xl w-full max-w-md overflow-hidden relative"
              onClick={(e) => e.stopPropagation()}
            >
              {/* Decorative corner */}
              <div className="absolute top-0 right-0 w-12 h-12 overflow-hidden pointer-events-none">
                <div className="absolute top-[-24px] right-[-24px] w-12 h-12 bg-cloudo-err rotate-45" />
              </div>

              <div className="px-8 py-6 border-b border-cloudo-border flex items-center gap-4 bg-cloudo-err/5">
                <div className="p-3 bg-cloudo-err/10 border border-cloudo-err/20">
                  <HiOutlineExclamation className="text-cloudo-err w-6 h-6" />
                </div>
                <div>
                  <h3 className="text-xs font-black uppercase tracking-[0.2em] text-cloudo-text">
                    Critical Action Required
                  </h3>
                  <p className="text-[10px] text-cloudo-err font-bold uppercase tracking-widest opacity-80 mt-1">
                    SIGKILL // TERMINATE_PROCESS
                  </p>
                </div>
              </div>

              <div className="p-8 space-y-6">
                <div className="space-y-4">
                  <p className="text-[11px] text-cloudo-muted font-bold uppercase tracking-widest leading-relaxed">
                    You are about to issue a termination command to the
                    following execution context:
                  </p>
                  <div className="bg-cloudo-accent/5 border border-cloudo-border p-4 font-mono space-y-2">
                    <div className="flex justify-between items-center text-[10px]">
                      <span className="text-cloudo-muted uppercase">
                        EXEC_ID:
                      </span>
                      <span className="text-cloudo-accent font-bold truncate ml-4">
                        {processToStop.execId}
                      </span>
                    </div>
                    <div className="flex justify-between items-center text-[10px]">
                      <span className="text-cloudo-muted uppercase">ID:</span>
                      <span className="text-cloudo-text font-bold truncate ml-4">
                        {processToStop.id}
                      </span>
                    </div>
                    <div className="flex justify-between items-center text-[10px]">
                      <span className="text-cloudo-muted uppercase">NAME:</span>
                      <span className="text-cloudo-text font-bold truncate ml-4">
                        {processToStop.name}
                      </span>
                    </div>
                    <div className="flex justify-between items-center text-[10px]">
                      <span className="text-cloudo-muted uppercase">NODE:</span>
                      <span className="text-cloudo-text font-bold truncate ml-4">
                        {processToStop.worker}
                      </span>
                    </div>
                  </div>
                  <p className="text-[10px] text-cloudo-err/60 font-black uppercase tracking-tighter italic">
                    Warning: This action cannot be reversed. Running processes
                    may leave inconsistent state.
                  </p>
                </div>

                <div className="flex gap-4">
                  <button
                    onClick={() => setProcessToStop(null)}
                    className="flex-1 bg-transparent border border-cloudo-border text-cloudo-muted hover:bg-white/5 py-4 text-[10px] font-black uppercase tracking-[0.3em] transition-all"
                  >
                    Abort_Action
                  </button>
                  <button
                    onClick={() =>
                      stopProcess(processToStop.worker, processToStop.execId)
                    }
                    className="flex-1 bg-cloudo-err text-cloudo-dark hover:bg-cloudo-err/90 py-4 text-[10px] font-black uppercase tracking-[0.3em] transition-all shadow-[0_0_15px_rgba(var(--color-cloudo-err),0.3)]"
                  >
                    Confirm_Kill
                  </button>
                </div>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Header Sezionale */}
      <div className="flex items-center justify-between px-8 py-5 border-b border-cloudo-border bg-cloudo-panel sticky top-0 z-20">
        <div className="flex items-center gap-4 shrink-0">
          <div className="p-2 bg-cloudo-accent/5 border border-cloudo-accent/20 shrink-0">
            <HiOutlineServer className="text-cloudo-accent w-5 h-5" />
          </div>
          <div>
            <h1 className="text-sm font-black tracking-[0.2em] text-cloudo-text uppercase">
              Compute Infrastructure
            </h1>
            <p className="text-[11px] text-cloudo-muted font-black uppercase tracking-[0.3em] opacity-70">
              Fleet Management // RUNTIME
            </p>
          </div>
        </div>
        <button
          onClick={fetchWorkers}
          disabled={loadingWorkers}
          className="btn btn-primary h-10"
        >
          <HiOutlineRefresh
            className={`w-4 h-4 ${loadingWorkers ? "animate-spin" : ""}`}
          />
          Refresh
        </button>
      </div>

      <div className="flex-1 overflow-auto p-8 space-y-12">
        <div className="max-w-[1400px] mx-auto space-y-12">
          {/* Section: Worker Processes */}
          <section className="space-y-6">
            <div className="flex items-center gap-3">
              <div className="w-1.5 h-4 bg-cloudo-accent" />
              <h2 className="text-sm font-black uppercase tracking-[0.4em] text-cloudo-text">
                Runtime Inspection
              </h2>
            </div>

            <div className="bg-cloudo-panel border border-cloudo-border p-6">
              <div className="flex flex-col md:flex-row gap-6 items-end">
                <div className="flex-1 space-y-2">
                  <label className="text-[11px] font-black uppercase tracking-[0.3em] text-cloudo-muted ml-1 block">
                    Target Worker Pool
                  </label>
                  <div className="relative group">
                    <div className="flex gap-4">
                      <div className="relative flex-1">
                        <HiOutlineSearch className="absolute left-3 top-1/2 -translate-y-1/2 text-cloudo-muted/70 w-4 h-4 group-focus-within:text-cloudo-accent transition-colors" />
                        <select
                          className="input-executions w-full pl-10 pr-4 h-13 appearance-none"
                          value={selectedWorker}
                          onChange={(e) => setSelectedWorker(e.target.value)}
                          disabled={workers.length === 0}
                        >
                          <option value="">SELECT_NODE...</option>
                          <option
                            value="all"
                            className="font-bold text-cloudo-accent"
                          >
                            SCAN_ALL_NODES
                          </option>
                          {workers.map((worker) => (
                            <option key={worker.RowKey} value={worker.RowKey}>
                              {worker.RowKey} [{worker.PartitionKey}]
                            </option>
                          ))}
                        </select>
                      </div>
                      <button
                        onClick={() => fetchProcesses(selectedWorker)}
                        disabled={!selectedWorker || loading}
                        className="btn btn-primary h-13 px-8"
                      >
                        {loading ? (
                          <HiOutlineRefresh className="w-4 h-4 animate-spin" />
                        ) : (
                          <HiOutlinePlay className="w-4 h-4" />
                        )}
                        Inspect
                      </button>
                    </div>
                  </div>
                </div>
              </div>

              {processes.length > 0 && (
                <div className="mt-8 border border-cloudo-border bg-cloudo-accent/10 overflow-hidden">
                  <table className="w-full text-left border-collapse text-sm">
                    <thead>
                      <tr className="bg-cloudo-panel-2 border-b border-cloudo-border text-cloudo-muted uppercase font-black tracking-[0.2em] text-[11px]">
                        <th className="px-6 py-4">Exec_ID</th>
                        <th className="px-6 py-4">Instance_Task</th>
                        <th className="px-6 py-4">Asset_Path</th>
                        <th className="px-6 py-4 text-center">Status</th>
                        <th className="px-6 py-4">Team</th>
                        <th className="px-6 py-4 text-right">Timestamp</th>
                        <th className="px-6 py-4 text-right w-24">Actions</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-cloudo-border/50">
                      {(
                        processes as (WorkerProcess & { workerNode?: string })[]
                      ).map((proc) => (
                        <tr
                          key={proc.exec_id}
                          className="hover:bg-white/[0.02] transition-colors group"
                        >
                          <td className="px-6 py-4 font-mono text-cloudo-muted/60 group-hover:text-cloudo-accent">
                            {proc.exec_id?.slice(0, 8)}
                          </td>
                          <td className="px-6 py-4">
                            <div className="font-bold text-cloudo-text uppercase tracking-widest">
                              {proc.name}
                            </div>
                            <div className="text-[11px] text-cloudo-muted opacity-70 font-mono mt-0.5">
                              {proc.id}
                            </div>
                            {selectedWorker === "all" && (
                              <div className="text-[10px] text-cloudo-accent/40 uppercase mt-1">
                                Pool: {proc.workerNode}
                              </div>
                            )}
                          </td>
                          <td className="px-6 py-4">
                            <div className="flex items-center gap-2 text-cloudo-accent/60 font-mono text-xs">
                              <HiOutlineTerminal className="w-4 h-4 opacity-60" />
                              {proc.runbook}
                            </div>
                          </td>
                          <td className="px-6 py-4 text-center">
                            <div className="flex items-center justify-center gap-2">
                              {proc.status.toLowerCase() === "running" && (
                                <div className="w-2 h-2 bg-cloudo-accent animate-pulse ring-2 ring-cloudo-accent/30 rounded-full" />
                              )}
                              <span
                                className={`px-2 py-0.5 border text-[11px] font-black uppercase tracking-widest ${getStatusBadgeClass(
                                  proc.status,
                                )}`}
                              >
                                {proc.status}
                              </span>
                            </div>
                          </td>
                          <td className="px-6 py-4">
                            <span className="px-2 py-0.5 border border-cloudo-border text-[11px] font-black uppercase tracking-widest text-cloudo-muted">
                              {proc.team || "default"}
                            </span>
                          </td>
                          <td className="px-6 py-4 text-right text-cloudo-muted font-mono opacity-50">
                            {formatRome(proc.startedAt || proc.requestedAt)}
                          </td>
                          <td className="px-6 py-4 text-right">
                            {proc.status.toLowerCase() === "running" &&
                              proc.can_stop &&
                              !isViewer && (
                                <button
                                  onClick={() =>
                                    setProcessToStop({
                                      worker: proc.workerNode || selectedWorker,
                                      execId: proc.exec_id,
                                      id: proc.id,
                                      name: proc.name,
                                    })
                                  }
                                  className="text-[11px] font-black text-cloudo-err hover:bg-cloudo-err hover:text-cloudo-text border border-cloudo-err/30 px-2 py-1 transition-all uppercase tracking-widest"
                                >
                                  KILL_PROC
                                </button>
                              )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {processes.length === 0 && selectedWorker && !loading && (
                <div className="mt-8 py-20 text-center border border-cloudo-border bg-cloudo-accent/5">
                  <HiOutlineDatabase className="h-12 w-12 text-cloudo-muted mx-auto mb-4 opacity-40" />
                  <p className="text-[11px] uppercase font-black tracking-[0.3em] text-cloudo-muted">
                    Interrogated node is currently idle // No processes found
                  </p>
                </div>
              )}
            </div>
          </section>

          {/* Section: Workers Registry */}
          <section className="space-y-6">
            <div className="flex items-center gap-3">
              <div className="w-1.5 h-4 bg-cloudo-accent" />
              <h2 className="text-sm font-black uppercase tracking-[0.4em] text-cloudo-text">
                Active Infrastructure
              </h2>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
              <div className="bg-cloudo-panel border border-cloudo-border px-4 py-3">
                <p className="text-[10px] uppercase tracking-[0.2em] text-cloudo-muted font-black">
                  Total Workers
                </p>
                <p className="text-xl font-black text-cloudo-text mt-1">
                  {totalWorkers}
                </p>
              </div>
              <div className="bg-cloudo-panel border border-cloudo-border px-4 py-3">
                <p className="text-[10px] uppercase tracking-[0.2em] text-cloudo-muted font-black">
                  Capabilities
                </p>
                <p className="text-xl font-black text-cloudo-text mt-1">
                  {capabilityCount}
                </p>
              </div>
              <div className="bg-cloudo-panel border border-cloudo-border px-4 py-3">
                <p className="text-[10px] uppercase tracking-[0.2em] text-cloudo-muted font-black">
                  Active Queues
                </p>
                <p className="text-xl font-black text-cloudo-accent mt-1">
                  {activeQueues}
                </p>
              </div>
              <div className="bg-cloudo-panel border border-cloudo-border px-4 py-3">
                <p className="text-[10px] uppercase tracking-[0.2em] text-cloudo-muted font-black">
                  Heartbeat Issues
                </p>
                <p
                  className={`text-xl font-black mt-1 ${
                    heartbeatIssues > 0 ? "text-cloudo-err" : "text-cloudo-ok"
                  }`}
                >
                  {heartbeatIssues}
                </p>
              </div>
            </div>

            {loadingWorkers ? (
              <div className="py-20 text-center flex flex-col items-center gap-3">
                <div className="w-8 h-8 border-2 border-cloudo-accent/30 border-t-cloudo-accent rounded-full animate-spin" />
                <span className="text-[11px] font-black uppercase tracking-widest text-cloudo-muted">
                  Polling Workers...
                </span>
              </div>
            ) : Object.keys(workersByCapability).length === 0 ? (
              <div className="py-20 text-center border border-cloudo-border bg-cloudo-accent/5">
                <p className="text-[11px] uppercase font-black tracking-widest text-cloudo-muted">
                  Registry Empty // No active nodes
                </p>
              </div>
            ) : (
              <div className="space-y-6">
                {Object.entries(workersByCapability)
                  .sort(([a], [b]) => a.localeCompare(b))
                  .map(([capability, workerList]) => (
                    <div
                      key={capability}
                      className="bg-cloudo-panel border border-cloudo-border"
                    >
                      <div className="px-5 py-4 border-b border-cloudo-border/70 flex items-center gap-3">
                        <HiOutlineChip className="text-cloudo-accent w-4 h-4 opacity-80 shrink-0" />
                        <h3 className="text-xs font-black text-cloudo-text uppercase tracking-[0.2em] truncate">
                          {capability}
                        </h3>
                        <div className="h-px flex-1 bg-cloudo-border" />
                        <span className="text-[11px] font-mono text-cloudo-muted/70 shrink-0">
                          {workerList.length} node
                          {workerList.length > 1 ? "s" : ""}
                        </span>
                      </div>

                      <div className="overflow-x-auto">
                        <table className="w-full text-left border-collapse">
                          <thead>
                            <tr className="text-[10px] uppercase tracking-[0.2em] font-black text-cloudo-muted border-b border-cloudo-border/60">
                              <th className="px-5 py-3">Worker</th>
                              <th className="px-5 py-3">Team</th>
                              <th className="px-5 py-3">Queue</th>
                              <th className="px-5 py-3">Region</th>
                              <th className="px-5 py-3">Seen Ago</th>
                              <th className="px-5 py-3">Last Heartbeat</th>
                              <th className="px-5 py-3 text-right">Status</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-cloudo-border/40">
                            {[...workerList]
                              .sort((a, b) => a.RowKey.localeCompare(b.RowKey))
                              .map((worker) => {
                                const heartbeat = getHeartbeatState(
                                  worker.LastSeen,
                                );
                                return (
                                  <tr
                                    key={worker.RowKey}
                                    className="hover:bg-white/[0.02] transition-colors"
                                  >
                                    <td className="px-5 py-3 text-sm font-mono text-cloudo-text">
                                      {worker.RowKey}
                                    </td>
                                    <td className="px-5 py-3 text-[11px] font-black uppercase tracking-widest text-cloudo-muted">
                                      {worker.team || "default"}
                                    </td>
                                    <td className="px-5 py-3 text-[12px] font-mono text-cloudo-accent/80">
                                      {worker.Queue || "-"}
                                    </td>
                                    <td className="px-5 py-3 text-[12px] text-cloudo-text/80">
                                      {worker.Region || "-"}
                                    </td>
                                    <td className="px-5 py-3 text-[12px] font-mono text-cloudo-text/80">
                                      {formatHeartbeatAge(worker.LastSeen)}
                                    </td>
                                    <td className="px-5 py-3 text-[12px] font-mono text-cloudo-muted">
                                      {formatLastSeen(worker.LastSeen)}
                                    </td>
                                    <td className="px-5 py-3">
                                      <div className="flex items-center justify-end gap-2">
                                        <div
                                          className={`w-2 h-2 rounded-full ${
                                            heartbeat === "online"
                                              ? "bg-cloudo-ok"
                                              : heartbeat === "stale"
                                                ? "bg-cloudo-err"
                                                : "bg-cloudo-muted"
                                          }`}
                                        />
                                        <span
                                          className={`text-[10px] font-black uppercase tracking-widest ${
                                            heartbeat === "online"
                                              ? "text-cloudo-ok"
                                              : heartbeat === "stale"
                                                ? "text-cloudo-err"
                                                : "text-cloudo-muted"
                                          }`}
                                        >
                                          {heartbeat}
                                        </span>
                                      </div>
                                    </td>
                                  </tr>
                                );
                              })}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  ))}
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
