"use client";

import { useState, useEffect, useMemo } from "react";
import { cloudoFetch } from "@/lib/api";
import { DeleteConfirmationModal } from "../utils/modals";
import { parseRunbookIntoCells } from "../utils/parser";

import {
  HiOutlinePlus,
  HiOutlineSearch,
  HiOutlineClock,
  HiOutlineTerminal,
  HiOutlineTrash,
  HiOutlinePencil,
  HiOutlineX,
  HiOutlineCheck,
  HiOutlineCheckCircle,
  HiOutlineExclamationCircle,
  HiOutlineRefresh,
  HiOutlineSwitchHorizontal,
  HiOutlineBan,
  HiOutlineClipboardCopy,
  HiOutlineChip,
} from "react-icons/hi";

interface Schedule {
  id: string;
  name: string;
  cron: string;
  runbook: string;
  run_args: string;
  queue?: string;
  worker_pool?: string;
  enabled: boolean;
  oncall: boolean;
  last_run?: string;
}

interface Notification {
  id: string;
  type: "success" | "error";
  message: string;
}

export default function SchedulesPage() {
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [modalMode, setModalMode] = useState<"create" | "edit" | null>(null);
  const [selectedSchedule, setSelectedSchedule] = useState<Schedule | null>(
    null,
  );
  const [scheduleToDelete, setScheduleToDelete] = useState<Schedule | null>(
    null,
  );
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [togglingId, setTogglingId] = useState<string | null>(null);
  const [user, setUser] = useState<{ role: string } | null>(null);

  const [runbookContent, setRunbookContent] = useState<string | null>(null);
  const [isRunbookModalOpen, setIsRunbookModalOpen] = useState(false);
  const [fetchingRunbook, setFetchingRunbook] = useState(false);
  const [availableRunbooks, setAvailableRunbooks] = useState<string[]>([]);
  const [availableWorkers, setAvailableWorkers] = useState<string[]>([]);

  const addNotification = (type: "success" | "error", message: string) => {
    const id = Date.now().toString();
    setNotifications((prev) => [...prev, { id, type, message }]);
    setTimeout(() => {
      setNotifications((prev) => prev.filter((n) => n.id !== id));
    }, 4000);
  };

  const removeNotification = (id: string) => {
    setNotifications((prev) => prev.filter((n) => n.id !== id));
  };

  useEffect(() => {
    fetchSchedules();
    const userData = localStorage.getItem("cloudo_user");
    if (userData) {
      try {
        setUser(JSON.parse(userData));
      } catch (e) {
        console.error("Failed to parse user data", e);
      }
    }
  }, []);

  const isViewer = user?.role === "VIEWER";

  const fetchSchedules = async () => {
    setLoading(true);
    try {
      const res = await cloudoFetch(`/schedules`);
      const data = await res.json();
      setSchedules(Array.isArray(data) ? data : []);
    } catch {
      setSchedules([]);
    } finally {
      setLoading(false);
    }
  };

  const fetchAvailableRunbooks = async () => {
    try {
      const res = await cloudoFetch(`/runbooks/list`);
      const data = await res.json();
      if (res.ok && Array.isArray(data.runbooks)) {
        setAvailableRunbooks(data.runbooks);
      }
    } catch {
      console.error("Failed to fetch available runbooks");
    }
  };

  const fetchWorkers = async () => {
    try {
      const res = await cloudoFetch(`/workers`);
      const data = await res.json();
      if (res.ok && Array.isArray(data)) {
        // Use PartitionKey as the capability as requested
        const capabilities = Array.from(
          new Set(
            data
              .map((w: { PartitionKey?: string }) => w.PartitionKey)
              .filter((c) => c),
          ),
        ) as string[];
        setAvailableWorkers(capabilities);
      }
    } catch {
      console.error("Failed to fetch available workers");
    }
  };

  const fetchRunbookContent = async (runbook: string) => {
    setFetchingRunbook(true);
    setRunbookContent(null);
    setIsRunbookModalOpen(true);
    try {
      const res = await cloudoFetch(
        `/runbooks/content?name=${encodeURIComponent(runbook)}`,
      );
      const data = await res.json();
      if (res.ok) {
        setRunbookContent(data.content);
      } else {
        setRunbookContent(`Error: ${data.error || "Failed to fetch content"}`);
      }
    } catch {
      setRunbookContent(
        "Error: Network failure while fetching runbook content",
      );
    } finally {
      setFetchingRunbook(false);
    }
  };

  const toggleSchedule = async (schedule: Schedule) => {
    setTogglingId(schedule.id);
    try {
      const updatedSchedule = { ...schedule, enabled: !schedule.enabled };

      const res = await cloudoFetch(`/schedules`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(updatedSchedule),
      });

      if (res.ok) {
        addNotification(
          "success",
          `Schedule ${schedule.id} ${
            updatedSchedule.enabled ? "enabled" : "disabled"
          }`,
        );
        fetchSchedules();
      } else {
        const d = await res.json();
        addNotification("error", d.error || "Operation failed");
      }
    } catch {
      addNotification("error", "Network error");
    } finally {
      setTogglingId(null);
    }
  };

  const filteredSchedules = useMemo(() => {
    return schedules.filter(
      (s) =>
        s.name?.toLowerCase().includes(searchQuery.toLowerCase()) ||
        s.id?.toLowerCase().includes(searchQuery.toLowerCase()),
    );
  }, [schedules, searchQuery]);

  if (
    user &&
    user.role !== "ADMIN" &&
    user.role !== "OPERATOR" &&
    user.role !== "VIEWER"
  ) {
    return (
      <div className="flex flex-col items-center justify-center h-full bg-cloudo-dark text-cloudo-text font-mono">
        <HiOutlineBan className="w-16 h-16 text-cloudo-err mb-4" />
        <h1 className="text-xl font-black uppercase tracking-[0.3em]">
          Access Denied
        </h1>
        <p className="text-cloudo-muted mt-2 uppercase tracking-widest text-sm">
          Authorized role required to view schedules
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full bg-cloudo-dark text-cloudo-text font-mono selection:bg-cloudo-accent/30">
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

      {/* Top Bar */}
      <div className="flex items-center justify-between px-8 py-4 border-b border-cloudo-border bg-cloudo-panel sticky top-0 z-20">
        <div className="flex items-center gap-4 shrink-0">
          <div className="p-2 bg-cloudo-accent/5 border border-cloudo-accent/20 shrink-0">
            <HiOutlineClock className="text-cloudo-accent w-4 h-4" />
          </div>
          <div>
            <h1 className="text-sm font-black tracking-[0.2em] text-cloudo-text uppercase">
              Automated Schedules
            </h1>
            <p className="text-[11px] text-cloudo-muted font-bold uppercase tracking-[0.3em] opacity-70">
              Cron Engine // CRON_DB
            </p>
          </div>
        </div>

        <div className="flex items-center gap-6">
          <div className="relative group">
            <HiOutlineSearch className="absolute left-3 top-1/2 -translate-y-1/2 text-cloudo-muted/70 w-4 h-4 group-focus-within:text-cloudo-accent transition-colors" />
            <input
              type="text"
              placeholder="Search schedules..."
              className="input input-icon pr-10"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery("")}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-cloudo-muted hover:text-cloudo-accent transition-colors"
              >
                <HiOutlineX className="w-4 h-4" />
              </button>
            )}
          </div>
          <button
            onClick={() => {
              setSelectedSchedule(null);
              setModalMode("create");
              fetchAvailableRunbooks();
              fetchWorkers();
            }}
            className={`btn btn-primary h-10 px-4 flex items-center gap-2 group ${
              isViewer || (user?.role !== "ADMIN" && user?.role !== "OPERATOR")
                ? "hidden"
                : ""
            }`}
          >
            <HiOutlinePlus className="w-4 h-4 group-hover:rotate-90 transition-transform" />{" "}
            New Schedule
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-auto p-8">
        <div className="max-w-[1400px] mx-auto">
          <div className="border border-cloudo-border bg-cloudo-panel overflow-hidden relative">
            <div className="absolute top-0 left-0 w-8 h-8 border-t border-l border-cloudo-accent/20 pointer-events-none" />
            <div className="absolute bottom-0 right-0 w-8 h-8 border-b border-r border-cloudo-accent/20 pointer-events-none" />

            <table className="w-full text-left border-collapse text-sm">
              <thead>
                <tr className="border-b border-cloudo-border bg-cloudo-accent/10">
                  <th className="px-8 py-5 font-black text-cloudo-muted uppercase tracking-[0.3em] text-[11px]">
                    Task Name
                  </th>
                  <th className="px-8 py-5 font-black text-cloudo-muted uppercase tracking-[0.3em] text-[11px]">
                    Cron Expression
                  </th>
                  <th className="px-8 py-5 font-black text-cloudo-muted uppercase tracking-[0.3em] text-[11px]">
                    Runbook Path
                  </th>
                  <th className="px-8 py-5 font-black text-cloudo-muted uppercase tracking-[0.3em] text-[11px]">
                    Last Execution
                  </th>
                  <th className="px-8 py-5 font-black text-cloudo-muted uppercase tracking-[0.3em] text-[11px]">
                    On Call
                  </th>
                  <th className="px-8 py-5 font-black text-cloudo-muted uppercase tracking-[0.3em] text-right text-[11px]">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-cloudo-border/30">
                {loading ? (
                  <tr>
                    <td
                      colSpan={5}
                      className="py-32 text-center text-cloudo-muted italic animate-pulse uppercase tracking-[0.5em] font-black opacity-50"
                    >
                      Syncing Cron Registry...
                    </td>
                  </tr>
                ) : filteredSchedules.length === 0 ? (
                  <tr>
                    <td
                      colSpan={5}
                      className="py-32 text-center text-sm font-black uppercase tracking-[0.5em] opacity-40 italic"
                    >
                      NO_SCHEDULES_FOUND
                    </td>
                  </tr>
                ) : (
                  filteredSchedules.map((s) => (
                    <tr
                      key={s.id}
                      className="group hover:bg-cloudo-accent/[0.02] transition-colors relative border-l-2 border-l-transparent hover:border-l-cloudo-accent/40"
                    >
                      <td className="px-8 py-6">
                        <div className="flex items-center gap-3">
                          <div
                            className={`w-2 h-2 rounded-full ${
                              s.enabled
                                ? "bg-cloudo-ok animate-pulse"
                                : "bg-cloudo-muted opacity-60"
                            }`}
                          />
                          <div className="flex flex-col">
                            <span className="text-sm font-black text-cloudo-text tracking-[0.1em] uppercase group-hover:text-cloudo-accent transition-colors">
                              {s.name}
                            </span>
                            <span className="text-[11px] text-cloudo-muted/70 font-mono mt-0.5">
                              ID: {s.id}
                            </span>
                          </div>
                        </div>
                      </td>
                      <td className="px-8 py-6">
                        <div className="bg-cloudo-accent/10 border border-cloudo-border px-3 py-1.5 font-mono text-cloudo-accent/80 text-xs w-fit">
                          {s.cron}
                        </div>
                      </td>
                      <td className="px-8 py-6 text-cloudo-text/70 font-mono">
                        <div className="flex items-center gap-3">
                          <button
                            onClick={() => fetchRunbookContent(s.runbook)}
                            className="p-1.5 bg-cloudo-accent/10 border border-cloudo-border hover:bg-cloudo-accent/20 transition-all cursor-pointer"
                            title="View Source Code"
                          >
                            <HiOutlineTerminal className="opacity-150 w-4 h-4" />
                          </button>
                          <span
                            className="truncate cursor-pointer hover:text-cloudo-accent transition-colors"
                            onClick={() => fetchRunbookContent(s.runbook)}
                          >
                            {s.runbook}
                          </span>
                        </div>
                      </td>
                      <td className="px-8 py-6 text-cloudo-muted opacity-70 font-mono">
                        {s.last_run
                          ? new Date(s.last_run).toLocaleString()
                          : "NEVER_EXECUTED"}
                      </td>
                      <td className="px-4 py-4 text-center">
                        {s.oncall && (
                          <div className="flex justify-center">
                            <div className="w-2 h-2 bg-cloudo-err animate-pulse" />
                          </div>
                        )}
                      </td>
                      <td className="px-8 py-6 text-right">
                        <div className="flex items-center justify-end gap-2">
                          <button
                            onClick={() => toggleSchedule(s)}
                            disabled={togglingId === s.id}
                            className={`p-2.5 border transition-all ${
                              s.enabled
                                ? "bg-cloudo-accent/10 border-cloudo-border text-cloudo-muted hover:border-cloudo-muted/40"
                                : "bg-cloudo-accent/10 border-cloudo-border text-cloudo-ok hover:border-white/20"
                            } ${
                              togglingId === s.id
                                ? "opacity-50 cursor-wait"
                                : ""
                            } ${
                              user?.role !== "ADMIN" &&
                              user?.role !== "OPERATOR"
                                ? "hidden"
                                : ""
                            }`}
                            title={
                              s.enabled ? "Disable Schedule" : "Enable Schedule"
                            }
                          >
                            {togglingId === s.id ? (
                              <HiOutlineRefresh className="w-4 h-4 animate-spin" />
                            ) : s.enabled ? (
                              <HiOutlineBan className="w-4 h-4" />
                            ) : (
                              <HiOutlineCheck className="w-4 h-4" />
                            )}
                          </button>
                          <button
                            onClick={() => {
                              setSelectedSchedule(s);
                              setModalMode("edit");
                              fetchAvailableRunbooks();
                              fetchWorkers();
                            }}
                            className={`p-2.5 bg-cloudo-accent/10 border border-cloudo-border hover:border-white/20 text-cloudo-muted hover:text-cloudo-text transition-all group/btn ${
                              user?.role !== "ADMIN" &&
                              user?.role !== "OPERATOR"
                                ? "hidden"
                                : ""
                            }`}
                            title="Edit Schedule"
                          >
                            <HiOutlinePencil className="w-4 h-4" />
                          </button>
                          <button
                            onClick={() => setScheduleToDelete(s)}
                            className={`p-2.5 bg-cloudo-accent/10 border border-cloudo-border hover:border-cloudo-err/40 text-cloudo-err hover:bg-cloudo-err hover:text-cloudo-text transition-all group/btn ${
                              user?.role !== "ADMIN" &&
                              user?.role !== "OPERATOR"
                                ? "hidden"
                                : ""
                            }`}
                            title="Delete Schedule"
                          >
                            <HiOutlineTrash className="w-4 h-4" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* Modal */}
      {modalMode && (
        <div
          className="fixed inset-0 bg-cloudo-dark/90 backdrop-blur-sm flex items-center justify-center z-50 p-4"
          onClick={() => setModalMode(null)}
        >
          <div
            className="bg-cloudo-panel border border-cloudo-border shadow-2xl w-full max-w-xl overflow-hidden animate-in zoom-in-95 duration-200"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="px-8 py-5 border-b border-cloudo-border flex justify-between items-center bg-cloudo-accent/5">
              <div className="flex items-center gap-3">
                <HiOutlineClock className="text-cloudo-accent w-5 h-5" />
                <h3 className="text-sm font-black uppercase tracking-[0.3em] text-cloudo-text">
                  {modalMode === "create"
                    ? "Provision Schedule"
                    : "Update Cron Schedule"}
                </h3>
              </div>
              <button
                onClick={() => setModalMode(null)}
                className="p-1.5 hover:bg-cloudo-err hover:text-cloudo-text border border-cloudo-border text-cloudo-muted transition-colors"
              >
                <HiOutlineX className="w-5 h-5" />
              </button>
            </div>

            <ScheduleForm
              initialData={selectedSchedule}
              mode={modalMode}
              availableRunbooks={availableRunbooks}
              availableWorkers={availableWorkers}
              onSuccess={(msg: string) => {
                fetchSchedules();
                setModalMode(null);
                addNotification("success", msg);
              }}
              onCancel={() => setModalMode(null)}
              onError={(msg: string) => addNotification("error", msg)}
            />
          </div>
        </div>
      )}

      {/* Delete Confirmation Modal */}
      {scheduleToDelete && (
        <DeleteConfirmationModal
          schema={scheduleToDelete}
          onClose={() => setScheduleToDelete(null)}
          type="schedules"
          onSuccess={(message) => {
            fetchSchedules();
            addNotification("success", message);
          }}
          onError={(message) => addNotification("error", message)}
        />
      )}

      {/* Runbook Source Modal */}
      {isRunbookModalOpen && (
        <div
          className="fixed inset-0 bg-cloudo-dark/95 backdrop-blur-md flex items-center justify-center z-[70] p-4"
          onClick={() => setIsRunbookModalOpen(false)}
        >
          <div
            className="bg-cloudo-panel border border-cloudo-border shadow-2xl w-full max-w-4xl max-h-[80vh] flex flex-col animate-in zoom-in-95 duration-200 relative"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="px-8 py-4 border-b border-cloudo-border flex justify-between items-center bg-cloudo-accent/5">
              <div className="flex items-center gap-3">
                <HiOutlineTerminal className="text-cloudo-accent w-4 h-4" />
                <h3 className="text-[10px] font-black uppercase tracking-[0.3em] text-cloudo-text">
                  Runbook Source Viewer
                </h3>
              </div>
              <button
                onClick={() => setIsRunbookModalOpen(false)}
                className="p-1.5 hover:bg-cloudo-err hover:text-cloudo-text border border-cloudo-border text-cloudo-muted transition-colors"
              >
                <HiOutlineX className="w-4 h-4" />
              </button>
            </div>

            {/* ── content area ── */}
            <div className="flex-1 overflow-auto p-6 input-editor font-mono text-xs bg-black/40 space-y-2">
              {fetchingRunbook ? (
                <div className="flex items-center justify-center h-64 text-cloudo-accent animate-pulse uppercase tracking-widest font-black">
                  Retrieving Source from Git...
                </div>
              ) : (
                parseRunbookIntoCells(runbookContent || "").map((cell, i) => (
                  <div key={i}>
                    {cell.heading && (
                      <p className="text-[9px] font-black uppercase tracking-widest text-cloudo-accent/70 mb-1.5 mt-4 first:mt-0">
                        {cell.heading}
                      </p>
                    )}
                    <div className="border border-cloudo-border bg-cloudo-panel/60">
                      <pre className="p-4 text-cloudo-text/90 whitespace-pre-wrap break-all leading-relaxed">
                        {cell.code}
                      </pre>
                    </div>
                  </div>
                ))
              )}
            </div>

            <div className="px-8 py-3 border-t border-cloudo-border bg-cloudo-panel flex justify-between items-center">
              <span className="text-[9px] text-cloudo-muted uppercase font-bold tracking-widest opacity-60">
                System Isolated Viewer // READ_ONLY
              </span>
              <button
                onClick={() => {
                  if (runbookContent) {
                    navigator.clipboard.writeText(runbookContent);
                    addNotification("success", "Source copied to clipboard");
                  }
                }}
                disabled={!runbookContent || fetchingRunbook}
                className="flex items-center gap-2 text-[9px] font-black uppercase tracking-widest text-cloudo-accent hover:text-white transition-colors disabled:opacity-30"
              >
                <HiOutlineClipboardCopy className="w-3.5 h-3.5" /> Copy Code
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function ScheduleForm({
  initialData,
  mode,
  availableRunbooks,
  availableWorkers,
  onSuccess,
  onCancel,
  onError,
}: {
  initialData: Schedule | null;
  mode: "create" | "edit";
  availableRunbooks: string[];
  availableWorkers: string[];
  onSuccess: (msg: string) => void;
  onCancel: () => void;
  onError: (msg: string) => void;
}) {
  const [formData, setFormData] = useState({
    id: initialData?.id || "",
    name: initialData?.name || "",
    cron: initialData?.cron || "0 */1 * * * *",
    runbook: initialData?.runbook || "",
    run_args: initialData?.run_args || "",
    worker_pool: initialData?.worker_pool || "",
    enabled: initialData?.enabled ?? true,
    oncall: initialData?.oncall ?? false,
  });
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    try {
      const res = await cloudoFetch(`/schedules`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(formData),
      });
      if (res.ok) {
        onSuccess(
          mode === "create" ? "Schedule provisioned" : "Schedule updated",
        );
      } else {
        const d = await res.json();
        onError(d.error || "Operation failed");
      }
    } catch {
      onError("Uplink failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="p-8 space-y-6">
      <div className="grid grid-cols-2 gap-6">
        <div className="space-y-2">
          <label className="text-[11px] font-black uppercase tracking-widest text-cloudo-muted ml-1 block">
            Task Name
          </label>
          <input
            type="text"
            required
            className="input h-11 w-full"
            value={formData.name}
            onChange={(e) => setFormData({ ...formData, name: e.target.value })}
            placeholder="NIGHTLY_CLEANUP"
          />
        </div>
        <div className="space-y-2">
          <label className="text-[11px] font-black uppercase tracking-widest text-cloudo-muted ml-1 block">
            Cron Expression (Azure Format)
          </label>
          <input
            type="text"
            required
            className="input h-11 font-mono text-cloudo-accent w-full"
            value={formData.cron}
            onChange={(e) => setFormData({ ...formData, cron: e.target.value })}
            placeholder="0 */5 * * * *"
          />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-6">
        <div className="space-y-2">
          <label className="text-[11px] font-black uppercase tracking-widest text-cloudo-muted ml-1 block">
            Runbook Path
          </label>
          <div className="relative group">
            <div className="absolute inset-y-0 left-0 w-10 flex items-center justify-center border-r border-cloudo-border/30 group-focus-within:border-cloudo-accent/50 bg-cloudo-accent/5">
              <HiOutlineTerminal className="text-cloudo-muted/70 w-4 h-4" />
            </div>
            <input
              type="text"
              required
              className="input input-icon h-11 font-mono w-full"
              value={formData.runbook}
              onChange={(e) =>
                setFormData({ ...formData, runbook: e.target.value })
              }
              placeholder="scripts/cleanup.sh"
              list="runbooks-list-schedules"
            />
            <datalist id="runbooks-list-schedules">
              {availableRunbooks.map((rb: string) => (
                <option key={rb} value={rb} />
              ))}
            </datalist>
          </div>
        </div>
        <div className="space-y-2">
          <label className="text-[11px] font-black uppercase tracking-widest text-cloudo-muted ml-1 block">
            Runtime Arguments
          </label>
          <input
            type="text"
            className="input h-11 font-mono w-full"
            value={formData.run_args}
            onChange={(e) =>
              setFormData({ ...formData, run_args: e.target.value })
            }
            placeholder="arg1 arg2 --quiet"
          />
        </div>
      </div>

      <div className="space-y-2">
        Worker Capability *
        <div className="relative group">
          <div className="absolute inset-y-0 left-0 w-10 flex items-center justify-center border-r border-cloudo-border/30 group-focus-within:border-cloudo-accent/50 bg-cloudo-accent/5">
            <HiOutlineChip className="text-cloudo-muted/70 w-4 h-4" />
          </div>
          <select
            required
            className="input input-icon font-mono w-full appearance-none cursor-pointer"
            value={formData.worker_pool}
            onChange={(e) =>
              setFormData({ ...formData, worker_pool: e.target.value })
            }
          >
            <option
              value=""
              disabled
              className="bg-cloudo-panel text-cloudo-muted italic"
            >
              Select Worker Capability...
            </option>
            {availableWorkers.map((worker_pool) => (
              <option
                key={worker_pool}
                value={worker_pool}
                className="bg-cloudo-panel text-cloudo-text py-2"
              >
                {worker_pool}
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

      <div
        className="flex items-center justify-between p-4 bg-cloudo-accent/10 border border-cloudo-border group hover:border-cloudo-accent/40 transition-all cursor-pointer"
        onClick={() => setFormData({ ...formData, enabled: !formData.enabled })}
      >
        <div className="space-y-1">
          <p className="text-sm font-black text-cloudo-text uppercase tracking-widest">
            Enabled
          </p>
          <p className="text-[11px] text-cloudo-muted uppercase font-bold opacity-70">
            Set Schedule State
          </p>
        </div>
        <div
          className={`flex items-center gap-2 px-3 py-1 border font-black text-[11px] uppercase tracking-widest transition-all ${
            formData.enabled
              ? "bg-cloudo-ok/10 border-cloudo-ok text-cloudo-ok"
              : "bg-cloudo-muted/10 border-cloudo-muted text-cloudo-muted opacity-70"
          }`}
        >
          {formData.enabled ? (
            <HiOutlineSwitchHorizontal className="w-4 h-4 text-cloudo-accent" />
          ) : (
            <HiOutlineBan className="w-4 h-4 text-cloudo-muted" />
          )}
          {formData.enabled ? "Yes" : "Nope"}
        </div>
      </div>
      <div
        className="flex items-center justify-between p-4 bg-cloudo-accent/10 border border-cloudo-border group hover:border-cloudo-accent/40 transition-all cursor-pointer"
        onClick={() => setFormData({ ...formData, oncall: !formData.oncall })}
      >
        <div className="space-y-1">
          <p className="text-sm font-black text-cloudo-text uppercase tracking-widest">
            On Call
          </p>
          <p className="text-[11px] text-cloudo-muted uppercase font-bold opacity-70">
            Set On Call Flow
          </p>
        </div>
        <div
          className={`flex items-center gap-2 px-3 py-1 border font-black text-[11px] uppercase tracking-widest transition-all ${
            formData.oncall
              ? "bg-cloudo-ok/10 border-cloudo-ok text-cloudo-ok"
              : "bg-cloudo-muted/10 border-cloudo-muted text-cloudo-muted opacity-70"
          }`}
        >
          {formData.oncall ? (
            <HiOutlineSwitchHorizontal className="w-4 h-4 text-cloudo-accent" />
          ) : (
            <HiOutlineBan className="w-4 h-4 text-cloudo-muted" />
          )}
          {formData.oncall ? "Yes" : "Nope"}
        </div>
      </div>

      <div className="flex gap-4 pt-6 border-t border-cloudo-border">
        <button
          type="button"
          onClick={onCancel}
          className="btn btn-ghost flex-1 h-12"
        >
          Abort
        </button>
        <button
          type="submit"
          disabled={submitting}
          className="btn btn-primary flex-1 h-12"
        >
          {submitting ? "Saving..." : "Save Schedule"}
        </button>
      </div>
    </form>
  );
}
