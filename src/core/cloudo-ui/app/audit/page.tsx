"use client";

import { useState, useEffect, useMemo } from "react";
import { cloudoFetch } from "@/lib/api";
import { useRouter } from "next/navigation";
import {
  HiOutlineClipboardList,
  HiOutlineSearch,
  HiOutlineRefresh,
  HiOutlineUser,
  HiOutlineExclamationCircle,
  HiOutlineClock,
  HiOutlineTag,
  HiOutlinePlus,
  HiOutlineTrash,
  HiOutlineCheckCircle,
  HiOutlineTerminal,
  HiOutlineChevronLeft,
  HiOutlineChevronRight,
  HiOutlineX,
} from "react-icons/hi";

interface AuditLog {
  timestamp: string;
  operator: string;
  action: string;
  target: string;
  details: string;
  team?: string;
}

export default function AuditPage() {
  const router = useRouter();
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [activeFilter, setActiveFilter] = useState<
    "all" | "manual" | "api" | "action"
  >("all");
  const [actionTypeFilter, setActionTypeFilter] = useState<
    "all" | "mutation" | "destruction"
  >("all");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [fetchLimit, setFetchLimit] = useState<string>("100");
  const [teamFilter, setTeamFilter] = useState("all");

  // Pagination state
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [isInitialized, setIsInitialized] = useState(false);

  // Load filters from localStorage on mount
  useEffect(() => {
    const savedFilters = localStorage.getItem("cloudo_audit_filters");
    if (savedFilters) {
      try {
        const filters = JSON.parse(savedFilters);
        if (filters.searchQuery) setSearchQuery(filters.searchQuery);
        if (filters.activeFilter) setActiveFilter(filters.activeFilter);
        if (filters.actionTypeFilter)
          setActionTypeFilter(filters.actionTypeFilter);
        if (filters.fromDate) setFromDate(filters.fromDate);
        if (filters.toDate) setToDate(filters.toDate);
        if (filters.fetchLimit) setFetchLimit(filters.fetchLimit);
        if (filters.pageSize) setPageSize(filters.pageSize);
      } catch (e) {
        console.error("Failed to parse saved audit filters", e);
      }
    }
    setIsInitialized(true);
  }, []);

  // Save filters to localStorage when they change
  useEffect(() => {
    if (!isInitialized) return;

    const filters = {
      searchQuery,
      activeFilter,
      actionTypeFilter,
      fromDate,
      toDate,
      fetchLimit,
      pageSize,
    };
    localStorage.setItem("cloudo_audit_filters", JSON.stringify(filters));
  }, [
    searchQuery,
    activeFilter,
    actionTypeFilter,
    fromDate,
    toDate,
    fetchLimit,
    pageSize,
    isInitialized,
  ]);

  useEffect(() => {
    const userData = localStorage.getItem("cloudo_user");
    if (userData) {
      try {
        const user = JSON.parse(userData);
        if (user.role !== "ADMIN") {
          router.push("/");
          return;
        }
      } catch {
        router.push("/login");
        return;
      }
    } else {
      router.push("/login");
      return;
    }
    fetchLogs();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router, fetchLimit]);

  const fetchLogs = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await cloudoFetch(`/audit?limit=${fetchLimit}`);

      if (!res.ok) throw new Error(`HTTP Error: ${res.status}`);

      const data = await res.json();
      setLogs(Array.isArray(data) ? data : []);
    } catch (e) {
      console.error("Failed to fetch audit logs", e);
      setError("Uplink to Security Vault failed. Check system status.");
    } finally {
      setLoading(false);
    }
  };

  const teamOptions = useMemo(
    () => Array.from(new Set(logs.map((log) => log.team || "default"))).sort(),
    [logs],
  );

  const filteredLogs = useMemo(() => {
    return logs.filter((log) => {
      if (teamFilter !== "all" && (log.team || "default") !== teamFilter) {
        return false;
      }
      const matchesSearch =
        log.operator?.toLowerCase().includes(searchQuery.toLowerCase()) ||
        log.action?.toLowerCase().includes(searchQuery.toLowerCase()) ||
        log.target?.toLowerCase().includes(searchQuery.toLowerCase()) ||
        log.details?.toLowerCase().includes(searchQuery.toLowerCase());

      const matchesFilter =
        activeFilter === "all" ||
        (activeFilter === "manual" &&
          log.operator !== "api" &&
          !log.operator?.endsWith("-api") &&
          log.operator !== "azure-action") ||
        (activeFilter === "api" &&
          (log.operator === "api" || log.operator?.endsWith("-api"))) ||
        (activeFilter === "action" && log.operator === "azure-action");

      const isMutation =
        log.action?.includes("CREATE") || log.action?.includes("UPSERT");
      const isDestruction = log.action?.includes("DELETE");
      const matchesActionType =
        actionTypeFilter === "all" ||
        (actionTypeFilter === "mutation" && isMutation) ||
        (actionTypeFilter === "destruction" && isDestruction);

      const logDate = log.timestamp?.split("T")[0];
      const matchesFrom = !fromDate || logDate >= fromDate;
      const matchesTo = !toDate || logDate <= toDate;

      return (
        matchesSearch &&
        matchesFilter &&
        matchesActionType &&
        matchesFrom &&
        matchesTo
      );
    });
  }, [
    logs,
    searchQuery,
    activeFilter,
    actionTypeFilter,
    fromDate,
    toDate,
    teamFilter,
  ]);

  // Reset pagination when filters change
  useEffect(() => {
    setCurrentPage(1);
  }, [
    searchQuery,
    activeFilter,
    actionTypeFilter,
    fromDate,
    toDate,
    teamFilter,
  ]);
  const totalPages = Math.ceil(filteredLogs.length / pageSize);
  const paginatedLogs = useMemo(() => {
    const start = (currentPage - 1) * pageSize;
    return filteredLogs.slice(start, start + pageSize);
  }, [filteredLogs, currentPage, pageSize]);

  const stats = useMemo(() => {
    const baseFiltered = logs.filter((log) => {
      if (teamFilter !== "all" && (log.team || "default") !== teamFilter) {
        return false;
      }
      const matchesSearch =
        log.operator?.toLowerCase().includes(searchQuery.toLowerCase()) ||
        log.action?.toLowerCase().includes(searchQuery.toLowerCase()) ||
        log.target?.toLowerCase().includes(searchQuery.toLowerCase()) ||
        log.details?.toLowerCase().includes(searchQuery.toLowerCase());

      const matchesFilter =
        activeFilter === "all" ||
        (activeFilter === "manual" &&
          log.operator !== "api" &&
          !log.operator?.endsWith("-api") &&
          log.operator !== "azure-action") ||
        (activeFilter === "api" &&
          (log.operator === "api" || log.operator?.endsWith("-api"))) ||
        (activeFilter === "action" && log.operator === "azure-action");

      const logDate = log.timestamp?.split("T")[0];
      const matchesFrom = !fromDate || logDate >= fromDate;
      const matchesTo = !toDate || logDate <= toDate;

      return matchesSearch && matchesFilter && matchesFrom && matchesTo;
    });

    return {
      total: baseFiltered.length,
      create: baseFiltered.filter(
        (l) => l.action?.includes("CREATE") || l.action?.includes("UPSERT"),
      ).length,
      delete: baseFiltered.filter((l) => l.action?.includes("DELETE")).length,
    };
  }, [logs, searchQuery, activeFilter, fromDate, toDate, teamFilter]);

  const getActionColor = (action: string) => {
    if (
      action.includes("DELETE") ||
      action.includes("REVOKE") ||
      action.includes("REJECT")
    )
      return "text-cloudo-err border-cloudo-err/30 bg-cloudo-err/5";
    if (
      action.includes("CREATE") ||
      action.includes("ENROLL") ||
      action.includes("APPROVE") ||
      action.includes("UPSERT")
    )
      return "text-cloudo-ok border-cloudo-ok/30 bg-cloudo-ok/5";
    if (action.includes("UPDATE"))
      return "text-cloudo-warn border-cloudo-warn/30 bg-cloudo-warn/5";
    if (action.includes("RUNBOOK"))
      return "text-pink-400 border-pink-400/30 bg-pink-400/5";
    return "text-cloudo-muted border-cloudo-muted/60 bg-cloudo-muted/5";
  };

  return (
    <div className="flex flex-col h-full bg-cloudo-dark text-cloudo-text font-mono selection:bg-cloudo-accent/30">
      {/* Top Bar */}
      <div className="flex flex-col border-b border-cloudo-border bg-cloudo-panel sticky top-0 z-20">
        <div className="flex items-center justify-between px-8 py-4">
          <div className="flex items-center gap-4 shrink-0">
            <div className="p-2 bg-cloudo-err/5 border border-cloudo-err/20 shrink-0">
              <HiOutlineClipboardList className="text-cloudo-err w-4 h-4" />
            </div>
            <div>
              <h1 className="text-sm font-black tracking-[0.2em] text-cloudo-text uppercase">
                Security Audit Log
              </h1>
              <p className="text-[11px] text-cloudo-muted font-bold uppercase tracking-[0.3em] opacity-70">
                Immutable Action Trace // SEC_VAULT
              </p>
            </div>
          </div>

          <div className="flex items-center gap-6">
            <div className="relative group">
              <HiOutlineSearch className="absolute left-3 top-1/2 -translate-y-1/2 text-cloudo-muted/70 w-4 h-4 group-focus-within:text-cloudo-accent transition-colors" />
              <input
                type="text"
                placeholder="Search audit trail..."
                className="input input-icon w-64 h-10 border-cloudo-border/50 focus:border-cloudo-accent/50 pr-10"
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
              onClick={fetchLogs}
              disabled={loading}
              className="btn btn-ghost h-10 px-4 flex items-center gap-2"
            >
              <HiOutlineRefresh
                className={`w-4 h-4 ${loading ? "animate-spin" : ""}`}
              />
              Refresh Trail
            </button>
            <div className="flex items-center gap-2 border-l border-cloudo-border pl-6">
              <span className="text-[10px] font-black text-cloudo-muted uppercase tracking-widest">
                Fetch
              </span>
              <select
                value={fetchLimit}
                onChange={(e) => setFetchLimit(e.target.value)}
                className="bg-cloudo-dark border border-cloudo-border text-cloudo-text text-[10px] font-black px-2 py-1 outline-none focus:border-cloudo-accent/50 transition-colors cursor-pointer h-10"
              >
                {[50, 100, 500, 1000, "all"].map((limit) => (
                  <option key={limit} value={limit}>
                    {limit === "all" ? "ALL" : limit}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </div>

        {/* Filter Bar */}
        <div className="flex items-center gap-2 px-8 pb-4">
          <button
            onClick={() => setActiveFilter("all")}
            className={`px-3 py-1.5 text-[10px] font-black uppercase tracking-widest border transition-all ${
              activeFilter === "all"
                ? "bg-cloudo-err border-cloudo-err text-white"
                : "bg-cloudo-err/5 border-cloudo-border text-cloudo-muted hover:border-cloudo-err/30"
            }`}
          >
            All Logs
          </button>
          <button
            onClick={() => setActiveFilter("manual")}
            className={`px-3 py-1.5 text-[10px] font-black uppercase tracking-widest border transition-all flex items-center gap-2 ${
              activeFilter === "manual"
                ? "bg-cloudo-accent border-cloudo-accent text-cloudo-dark"
                : "bg-cloudo-accent/5 border-cloudo-border text-cloudo-muted hover:border-cloudo-accent/30"
            }`}
          >
            <HiOutlineUser className="w-3 h-3" /> Manual Actions
          </button>
          <button
            onClick={() => setActiveFilter("api")}
            className={`px-3 py-1.5 text-[10px] font-black uppercase tracking-widest border transition-all flex items-center gap-2 ${
              activeFilter === "api"
                ? "bg-cloudo-warn border-cloudo-warn text-cloudo-dark"
                : "bg-cloudo-warn/5 border-cloudo-border text-cloudo-muted hover:border-cloudo-warn/30"
            }`}
          >
            <HiOutlineTerminal className="w-3 h-3" /> API Calls
          </button>
          <button
            onClick={() => setActiveFilter("action")}
            className={`px-3 py-1.5 text-[10px] font-black uppercase tracking-widest border transition-all flex items-center gap-2 ${
              activeFilter === "action"
                ? "bg-blue-500 border-blue-500 text-white"
                : "bg-blue-500/5 border-cloudo-border text-cloudo-muted hover:border-blue-500/30"
            }`}
          >
            <HiOutlineCheckCircle className="w-3 h-3" /> Azure Actions
          </button>

          <div className="flex-1" />

          <div className="flex items-center gap-2">
            <span className="text-[9px] font-black text-cloudo-muted uppercase tracking-tighter">
              Team
            </span>
            <select
              value={teamFilter}
              onChange={(e) => setTeamFilter(e.target.value)}
              className="bg-cloudo-dark border border-cloudo-border text-cloudo-text text-[10px] font-black px-2 h-9 outline-none focus:border-cloudo-accent/50 transition-colors cursor-pointer uppercase"
            >
              <option value="all">All teams</option>
              {teamOptions.map((team) => (
                <option key={team} value={team}>
                  {team}
                </option>
              ))}
            </select>
          </div>

          <div className="flex items-center gap-4 bg-cloudo-dark/30 px-3 py-1 border border-cloudo-border/50">
            <div className="flex items-center gap-2">
              <span className="text-[9px] font-black text-cloudo-muted uppercase tracking-tighter">
                From
              </span>
              <input
                type="date"
                className="input h-7 px-2 text-[10px] font-black uppercase tracking-widest border-cloudo-border/30 focus:border-cloudo-accent/50 w-32 bg-cloudo-panel/50 [color-scheme:dark]"
                value={fromDate}
                onChange={(e) => setFromDate(e.target.value)}
              />
            </div>

            <div className="w-[1px] h-4 bg-cloudo-border/30" />

            <div className="flex items-center gap-2">
              <span className="text-[9px] font-black text-cloudo-muted uppercase tracking-tighter">
                To
              </span>
              <input
                type="date"
                className="input h-7 px-2 text-[10px] font-black uppercase tracking-widest border-cloudo-border/30 focus:border-cloudo-accent/50 w-32 bg-cloudo-panel/50 [color-scheme:dark]"
                value={toDate}
                onChange={(e) => setToDate(e.target.value)}
              />
            </div>

            {(fromDate || toDate) && (
              <button
                onClick={() => {
                  setFromDate("");
                  setToDate("");
                }}
                className="ml-2 p-1 text-cloudo-muted hover:text-cloudo-err transition-colors flex items-center gap-1 border-l border-cloudo-border/30 pl-3"
                title="Clear Range"
              >
                <HiOutlineRefresh className="w-3 h-3" />
                <span className="text-[9px] font-black uppercase tracking-tighter">
                  Reset
                </span>
              </button>
            )}
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-auto p-8 space-y-8">
        <div className="max-w-[1400px] mx-auto space-y-8">
          {/* Statistics Grid */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <StatSmall
              title="Total Events"
              value={stats.total}
              icon={<HiOutlineClipboardList />}
              label="LOG_ENTRIES"
              onClick={() => setActionTypeFilter("all")}
              isActive={actionTypeFilter === "all"}
            />
            <StatSmall
              title="Mutations"
              value={stats.create}
              icon={<HiOutlinePlus />}
              label="WRITE_OPS"
              color="text-cloudo-ok"
              onClick={() =>
                setActionTypeFilter(
                  actionTypeFilter === "mutation" ? "all" : "mutation",
                )
              }
              isActive={actionTypeFilter === "mutation"}
            />
            <StatSmall
              title="Destructions"
              value={stats.delete}
              icon={<HiOutlineTrash />}
              label="DELETE_OPS"
              color="text-cloudo-err"
              onClick={() =>
                setActionTypeFilter(
                  actionTypeFilter === "destruction" ? "all" : "destruction",
                )
              }
              isActive={actionTypeFilter === "destruction"}
            />
          </div>

          <div className="border border-cloudo-border bg-cloudo-panel overflow-hidden relative">
            {/* Decorative corners */}
            <div className="absolute top-0 left-0 w-8 h-8 border-t border-l border-cloudo-err/20 pointer-events-none" />
            <div className="absolute bottom-0 right-0 w-8 h-8 border-b border-r border-cloudo-err/20 pointer-events-none" />

            <table className="w-full text-left border-collapse text-sm">
              <thead>
                <tr className="border-b border-cloudo-border bg-cloudo-accent/10">
                  <th className="px-8 py-5 font-black text-cloudo-muted uppercase tracking-[0.3em] w-48 text-[11px]">
                    Timestamp
                  </th>
                  <th className="px-8 py-5 font-black text-cloudo-muted uppercase tracking-[0.3em] w-40 text-[11px]">
                    Operator
                  </th>
                  <th className="px-8 py-5 font-black text-cloudo-muted uppercase tracking-[0.3em] w-32 text-[11px]">
                    Team
                  </th>
                  <th className="px-8 py-5 font-black text-cloudo-muted uppercase tracking-[0.3em] w-48 text-[11px]">
                    Action Event
                  </th>
                  <th className="px-8 py-5 font-black text-cloudo-muted uppercase tracking-[0.3em] w-48 text-[11px]">
                    Resource Target
                  </th>
                  <th className="px-8 py-5 font-black text-cloudo-muted uppercase tracking-[0.3em] text-[11px]">
                    Event Metadata
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-cloudo-border/30">
                {loading ? (
                  <tr key="loading-row">
                    <td
                      colSpan={6}
                      className="py-32 text-center text-cloudo-muted italic animate-pulse uppercase tracking-[0.5em] font-black opacity-50"
                    >
                      Extracting Vault Data...
                    </td>
                  </tr>
                ) : error ? (
                  <tr key="error-row">
                    <td
                      colSpan={6}
                      className="py-32 text-center text-cloudo-err font-black uppercase tracking-[0.2em]"
                    >
                      <div className="flex flex-col items-center gap-4">
                        <HiOutlineExclamationCircle className="w-8 h-8 opacity-70" />
                        {error}
                      </div>
                    </td>
                  </tr>
                ) : filteredLogs.length === 0 ? (
                  <tr key="empty-row">
                    <td
                      colSpan={6}
                      className="py-32 text-center text-sm font-black uppercase tracking-[0.5em] opacity-40 italic"
                    >
                      NO_AUDIT_EVENTS_CAPTURED
                    </td>
                  </tr>
                ) : (
                  paginatedLogs.map((log, idx) => (
                    <tr
                      key={`${log.timestamp}-${idx}`}
                      className="group hover:bg-white/[0.02] transition-colors relative border-l-2 border-l-transparent hover:border-l-cloudo-err/40"
                    >
                      <td className="px-8 py-6 whitespace-nowrap">
                        <div className="flex items-center gap-2 text-cloudo-text/80 font-mono">
                          <HiOutlineClock className="w-4 h-4 opacity-60" />
                          <span>
                            {log.timestamp?.replace("T", " ").split(".")[0]}
                          </span>
                        </div>
                      </td>
                      <td className="px-8 py-6">
                        <div className="flex items-center gap-2">
                          <HiOutlineUser className="w-4 h-4 text-cloudo-accent opacity-70" />
                          <span className="font-black text-cloudo-text uppercase tracking-widest">
                            {log.operator}
                          </span>
                        </div>
                      </td>
                      <td className="px-8 py-6">
                        <span className="px-2 py-0.5 border border-cloudo-border text-[11px] font-black uppercase tracking-widest text-cloudo-muted">
                          {log.team || "default"}
                        </span>
                      </td>
                      <td className="px-8 py-6">
                        <span
                          className={`px-2 py-0.5 border text-[11px] font-black uppercase tracking-widest ${getActionColor(
                            log.action,
                          )}`}
                        >
                          {log.action}
                        </span>
                      </td>
                      <td className="px-8 py-6">
                        <div className="flex items-center gap-2 font-mono text-cloudo-accent/80">
                          <HiOutlineTag className="w-4 h-4 opacity-60" />
                          <span className="truncate max-w-[160px] text-[11px]">
                            {log.target}
                          </span>
                        </div>
                      </td>
                      <td className="px-8 py-6 text-cloudo-muted font-mono text-[11px] leading-relaxed italic opacity-60 group-hover:opacity-400 transition-opacity">
                        {log.details || "---"}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>

          {/* Pagination Controls */}
          {filteredLogs.length > 0 && (
            <div className="flex flex-col md:flex-row items-center justify-between gap-4 px-2 py-4 border-t border-cloudo-border/30 bg-cloudo-panel/30">
              <div className="flex items-center gap-4">
                <div className="flex items-center gap-1">
                  <span className="text-[10px] font-black text-cloudo-muted uppercase tracking-widest">
                    Show
                  </span>
                  <select
                    value={pageSize}
                    onChange={(e) => {
                      setPageSize(Number(e.target.value));
                      setCurrentPage(1);
                    }}
                    className="bg-cloudo-dark border border-cloudo-border text-cloudo-text text-[10px] font-black px-2 py-1 outline-none focus:border-cloudo-accent/50 transition-colors cursor-pointer"
                  >
                    {[10, 20, 50, 100, 500].map((size) => (
                      <option key={size} value={size}>
                        {size}
                      </option>
                    ))}
                  </select>
                  <span className="text-[10px] font-black text-cloudo-muted uppercase tracking-widest ml-1">
                    Entries
                  </span>
                </div>
                <div className="h-4 w-[1px] bg-cloudo-border/30" />
                <span className="text-[10px] font-black text-cloudo-muted uppercase tracking-widest">
                  Showing{" "}
                  <span className="text-cloudo-text">
                    {(currentPage - 1) * pageSize + 1}
                  </span>{" "}
                  to{" "}
                  <span className="text-cloudo-text">
                    {Math.min(currentPage * pageSize, filteredLogs.length)}
                  </span>{" "}
                  of{" "}
                  <span className="text-cloudo-text">
                    {filteredLogs.length}
                  </span>
                </span>
              </div>

              <div className="flex items-center gap-2">
                <button
                  onClick={() =>
                    setCurrentPage((prev) => Math.max(1, prev - 1))
                  }
                  disabled={currentPage === 1}
                  className="p-2 border border-cloudo-border text-cloudo-muted hover:text-cloudo-accent hover:border-cloudo-accent/40 disabled:opacity-20 disabled:cursor-not-allowed transition-all"
                >
                  <HiOutlineChevronLeft className="w-4 h-4" />
                </button>

                <div className="flex items-center gap-1 mx-2">
                  <input
                    type="number"
                    min={1}
                    max={totalPages}
                    value={currentPage}
                    onChange={(e) => {
                      const val = parseInt(e.target.value);
                      if (!isNaN(val) && val >= 1 && val <= totalPages) {
                        setCurrentPage(val);
                      }
                    }}
                    className="w-12 bg-cloudo-dark border border-cloudo-border text-cloudo-text text-[10px] font-black px-2 py-1 text-center outline-none focus:border-cloudo-accent/50 transition-colors"
                  />
                  <span className="text-[10px] font-black text-cloudo-muted uppercase tracking-widest mx-1">
                    of
                  </span>
                  <span className="text-[10px] font-black text-cloudo-text uppercase tracking-widest">
                    {totalPages}
                  </span>
                </div>

                <button
                  onClick={() =>
                    setCurrentPage((prev) => Math.min(totalPages, prev + 1))
                  }
                  disabled={currentPage === totalPages}
                  className="p-2 border border-cloudo-border text-cloudo-muted hover:text-cloudo-accent hover:border-cloudo-accent/40 disabled:opacity-20 disabled:cursor-not-allowed transition-all"
                >
                  <HiOutlineChevronRight className="w-4 h-4" />
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function StatSmall({
  title,
  value,
  icon,
  label,
  color = "text-cloudo-text",
  onClick,
  isActive,
}: {
  title: string;
  value: number;
  icon: React.ReactNode;
  label: string;
  color?: string;
  onClick?: () => void;
  isActive?: boolean;
}) {
  return (
    <div
      onClick={onClick}
      className={`bg-cloudo-panel border p-5 flex items-center justify-between relative overflow-hidden group transition-all cursor-pointer ${
        isActive
          ? "border-cloudo-accent ring-1 ring-cloudo-accent/20"
          : "border-cloudo-border hover:border-cloudo-accent/40"
      }`}
    >
      <div
        className={`absolute top-0 left-0 w-[2px] h-full transition-colors ${
          isActive ? "bg-cloudo-accent" : "bg-cloudo-err/10"
        }`}
      />
      <div className="relative z-10">
        <p className="text-[11px] font-black uppercase tracking-[0.2em] text-cloudo-muted/60 mb-1">
          {title}
        </p>
        <p className={`text-2xl font-black ${color} tracking-tighter`}>
          {value}
        </p>
        <p className="text-[11px] font-bold text-cloudo-muted/60 uppercase mt-1 tracking-widest">
          {label}
        </p>
      </div>
      <div
        className={`p-2.5 border text-lg shrink-0 transition-colors ${
          isActive
            ? "bg-cloudo-accent/20 border-cloudo-accent"
            : "bg-cloudo-err/10 border-cloudo-border opacity-70"
        }`}
      >
        <div
          className={`${
            isActive ? "text-white" : "text-cloudo-err"
          } w-5 h-5 flex items-center justify-center`}
        >
          {icon}
        </div>
      </div>
    </div>
  );
}
