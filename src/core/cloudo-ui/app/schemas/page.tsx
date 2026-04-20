"use client";

import { useState, useEffect, useMemo, useRef } from "react";
import { cloudoFetch } from "@/lib/api";
import {
  HiOutlinePlus,
  HiOutlineSearch,
  HiOutlineChip,
  HiOutlineTerminal,
  HiOutlineUserGroup,
  HiOutlineShieldCheck,
  HiOutlineTrash,
  HiOutlinePlay,
  HiOutlinePencil,
  HiOutlineX,
  HiOutlineClipboardCopy,
  HiOutlineCheck,
  HiOutlineCheckCircle,
  HiOutlineExclamationCircle,
  HiOutlineRefresh,
  HiOutlineCloud,
  HiOutlineChevronLeft,
  HiOutlineChevronRight,
  HiOutlineEye,
  HiOutlineViewGrid,
  HiOutlineViewList,
  HiOutlineBan,
} from "react-icons/hi";
import { MdOutlineSchema } from "react-icons/md";
import { SiTerraform } from "react-icons/si";
import { DeleteConfirmationModal } from "../utils/modals";
import { parseRunbookIntoCells } from "../utils/parser";
import { Schema, Notification } from "./types";
import { StatSmall } from "./components/StatSmall";
import { SchemaForm } from "./components/SchemaForm";
import { SchemaCard } from "./components/SchemaCard";
import { SchemaTable } from "./components/SchemaTable";
import { SchemaFilters } from "./components/SchemaFilters";

export default function SchemasPage() {
  const [schemas, setSchemas] = useState<Schema[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [activeFilter, setActiveFilter] = useState<"all" | "terraform" | "ui">(
    "all",
  );
  const [workerFilter, setWorkerFilter] = useState("all");
  const [approvalFilter, setApprovalFilter] = useState<
    "all" | "required" | "auto"
  >("all");
  const [oncallFilter, setOncallFilter] = useState<
    "all" | "active" | "inactive"
  >("all");
  const [enabledFilter, setEnabledFilter] = useState<
    "all" | "enabled" | "disabled"
  >("all");
  const [tagFilter, setTagFilter] = useState("all");

  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<"grid" | "table">("grid");
  const [isInitialized, setIsInitialized] = useState(false);

  // Load filters from localStorage on mount
  useEffect(() => {
    const savedFilters = localStorage.getItem("cloudo_schema_filters");
    if (savedFilters) {
      try {
        const filters = JSON.parse(savedFilters);
        if (filters.searchQuery) setSearchQuery(filters.searchQuery);
        if (filters.activeFilter) setActiveFilter(filters.activeFilter);
        if (filters.workerFilter) setWorkerFilter(filters.workerFilter);
        if (filters.approvalFilter) setApprovalFilter(filters.approvalFilter);
        if (filters.oncallFilter) setOncallFilter(filters.oncallFilter);
        if (filters.enabledFilter) setEnabledFilter(filters.enabledFilter);
        if (filters.tagFilter) setTagFilter(filters.tagFilter);
        if (filters.viewMode) setViewMode(filters.viewMode);
      } catch (e) {
        console.error("Failed to parse saved filters", e);
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
      workerFilter,
      approvalFilter,
      oncallFilter,
      enabledFilter,
      tagFilter,
      viewMode,
    };
    localStorage.setItem("cloudo_schema_filters", JSON.stringify(filters));
  }, [
    searchQuery,
    activeFilter,
    workerFilter,
    approvalFilter,
    oncallFilter,
    enabledFilter,
    tagFilter,
    viewMode,
    isInitialized,
  ]);

  const [modalMode, setModalMode] = useState<"create" | "edit" | "view" | null>(
    null,
  );
  const [selectedSchema, setSelectedSchema] = useState<Schema | null>(null);
  const [schemaToDelete, setSchemaToDelete] = useState<Schema | null>(null);
  const [confirmRunId, setConfirmRunId] = useState<string | null>(null);
  const [executingId, setExecutingId] = useState<string | null>(null);
  const [togglingId, setTogglingId] = useState<string | null>(null);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [user, setUser] = useState<{ role: string } | null>(null);

  const [runbookContent, setRunbookContent] = useState<string | null>(null);
  const [isRunbookModalOpen, setIsRunbookModalOpen] = useState(false);
  const [fetchingRunbook, setFetchingRunbook] = useState(false);
  const [availableRunbooks, setAvailableRunbooks] = useState<string[]>([]);
  const [availableWorkers, setAvailableWorkers] = useState<string[]>([]);
  const [availableTags, setAvailableTags] = useState<string[]>([]);

  // Pagination state
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

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
    fetchSchemas();
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

  const fetchSchemas = async () => {
    setLoading(true);
    try {
      const res = await cloudoFetch(`/schemas`);
      const data = await res.json();
      const schemasList = Array.isArray(data) ? data : [];
      setSchemas(schemasList);
      extractAvailableTags(schemasList);
    } catch {
      setSchemas([]);
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

  const extractAvailableTags = (schemasList: Schema[]) => {
    const tags = new Set<string>();
    schemasList.forEach((s) => {
      if (s.tags) {
        s.tags
          .split(",")
          .map((t) => t.trim().toLowerCase())
          .filter((t) => t !== "" && t !== "terraform" && t !== "ui")
          .forEach((t) => tags.add(t));
      }
    });
    setAvailableTags(Array.from(tags).sort());
  };

  const copyToClipboard = (id: string) => {
    navigator.clipboard.writeText(id);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const handleRun = async (id: string) => {
    setExecutingId(id);
    setConfirmRunId(null);
    try {
      const response = await cloudoFetch(`/Trigger?id=${id}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ source: "schemas-manual" }),
      });

      if (!response.ok) {
        const error = await response.text();
        addNotification("error", `Execution failed: ${error}`);
      } else {
        addNotification("success", `Execution triggered for ${id}`);
      }
    } catch {
      addNotification("error", "Network error // execution failed");
    } finally {
      setExecutingId(null);
    }
  };

  const handleToggleSchema = async (schema: Schema) => {
    setTogglingId(schema.id);
    try {
      const updatedSchema = {
        ...schema,
        enabled: schema.enabled === false,
      };

      const res = await cloudoFetch(`/schemas`, {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          ...updatedSchema,
          PartitionKey: schema.PartitionKey || "RunbookSchema",
          RowKey: schema.RowKey || schema.id,
        }),
      });

      if (res.ok) {
        addNotification(
          "success",
          `Runbook ${schema.id} ${
            updatedSchema.enabled ? "enabled" : "disabled"
          }`,
        );
        fetchSchemas();
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

  const isTerraformSchema = (tags?: string) =>
    tags
      ?.split(",")
      .map((t) => t.trim().toLowerCase())
      .includes("terraform");

  const filteredSchemas = useMemo(() => {
    return schemas.filter((s) => {
      const matchesSearch =
        s.name?.toLowerCase().includes(searchQuery.toLowerCase()) ||
        s.id?.toLowerCase().includes(searchQuery.toLowerCase()) ||
        s.description?.toLowerCase().includes(searchQuery.toLowerCase());

      const isTf = isTerraformSchema(s.tags);
      const matchesFilter =
        activeFilter === "all" ||
        (activeFilter === "terraform" && isTf) ||
        (activeFilter === "ui" && !isTf);

      const matchesWorker = workerFilter === "all" || s.worker === workerFilter;

      const matchesApproval =
        approvalFilter === "all" ||
        (approvalFilter === "required" &&
          String(s.require_approval) === "true") ||
        (approvalFilter === "auto" && String(s.require_approval) !== "true");

      const matchesOncall =
        oncallFilter === "all" ||
        (oncallFilter === "active" && s.oncall === "true") ||
        (oncallFilter === "inactive" && s.oncall !== "true");

      const matchesEnabled =
        enabledFilter === "all" ||
        (enabledFilter === "enabled" && s.enabled !== false) ||
        (enabledFilter === "disabled" && s.enabled === false);

      const matchesTag =
        tagFilter === "all" ||
        (s.tags &&
          s.tags
            .split(",")
            .map((t) => t.trim().toLowerCase())
            .includes(tagFilter.toLowerCase()));

      return (
        matchesSearch &&
        matchesFilter &&
        matchesWorker &&
        matchesApproval &&
        matchesOncall &&
        matchesEnabled &&
        matchesTag
      );
    });
  }, [
    schemas,
    searchQuery,
    activeFilter,
    workerFilter,
    approvalFilter,
    oncallFilter,
    enabledFilter,
    tagFilter,
  ]);

  // Reset pagination when filters change
  useEffect(() => {
    setCurrentPage(1);
  }, [
    searchQuery,
    activeFilter,
    workerFilter,
    approvalFilter,
    oncallFilter,
    tagFilter,
  ]);

  useEffect(() => {
    fetchWorkers();
  }, [schemas]);

  const totalPages = Math.ceil(filteredSchemas.length / pageSize);
  const paginatedSchemas = useMemo(() => {
    const start = (currentPage - 1) * pageSize;
    return filteredSchemas.slice(start, start + pageSize);
  }, [filteredSchemas, currentPage, pageSize]);

  const stats = useMemo(() => {
    const isTrue = (val: string | boolean | undefined | null): boolean => {
      if (typeof val === "boolean") return val;
      return val === "true";
    };

    return {
      total: schemas.length,
      approvalRequired: schemas.filter((s) => isTrue(s.require_approval))
        .length,
      onCall: schemas.filter((s) => isTrue(s.oncall)).length,
      disabled: schemas.filter((s) => s.enabled === false).length,
    };
  }, [schemas]);

  return (
    <div className="flex flex-col h-full bg-cloudo-dark text-cloudo-text font-mono selection:bg-cloudo-accent/30">
      {/* Notifications */}
      <div className="fixed top-8 right-8 z-[100] flex flex-col gap-3 pointer-events-none">
        {notifications.map((n) => (
          <div
            key={n.id}
            className={`px-6 py-4 flex items-center gap-4 animate-in slide-in-from-right-full duration-300 border shadow-2xl pointer-events-auto min-w-75 relative overflow-hidden ${
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
      <div className="flex flex-col border-b border-cloudo-border bg-cloudo-panel sticky top-0 z-20">
        <div className="flex items-center justify-between px-8 py-4">
          <div className="flex items-center gap-4 shrink-0">
            <div className="p-2 bg-cloudo-accent/5 border border-cloudo-accent/20 shrink-0">
              <MdOutlineSchema className="text-cloudo-accent w-4 h-4" />
            </div>
            <div>
              <h1 className="text-sm font-black tracking-[0.2em] text-cloudo-text uppercase">
                Runbook Schemas
              </h1>
              <p className="text-[11px] text-cloudo-muted font-bold uppercase tracking-[0.3em] opacity-70">
                System Inventory // ASSET_DB
              </p>
            </div>
          </div>

          <div className="flex items-center gap-6">
            <div className="relative group">
              <HiOutlineSearch className="absolute left-3 top-1/2 -translate-y-1/2 text-cloudo-muted/70 w-4 h-4 group-focus-within:text-cloudo-accent transition-colors" />
              <input
                type="text"
                placeholder="Search schemas..."
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
            {!isViewer &&
              (user?.role === "ADMIN" || user?.role === "OPERATOR") && (
                <button
                  onClick={() => {
                    setSelectedSchema(null);
                    setModalMode("create");
                    fetchAvailableRunbooks();
                    fetchWorkers();
                  }}
                  className="btn btn-primary h-10 px-4 flex items-center gap-2 group"
                >
                  <HiOutlinePlus className="w-4 h-4 group-hover:rotate-90 transition-transform" />{" "}
                  New Schema
                </button>
              )}
          </div>
        </div>

        {/* Filter Bar */}
        <div className="px-8 pb-4">
          <div className="flex flex-col gap-4">
            <SchemaFilters
              searchQuery={searchQuery}
              setSearchQuery={setSearchQuery}
              activeFilter={activeFilter}
              setActiveFilter={setActiveFilter}
              workerFilter={workerFilter}
              setWorkerFilter={setWorkerFilter}
              approvalFilter={approvalFilter}
              setApprovalFilter={setApprovalFilter}
              oncallFilter={oncallFilter}
              setOncallFilter={setOncallFilter}
              enabledFilter={enabledFilter}
              setEnabledFilter={setEnabledFilter}
              tagFilter={tagFilter}
              setTagFilter={setTagFilter}
              availableWorkers={availableWorkers}
              availableTags={availableTags}
            />

            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-[10px] font-black text-cloudo-muted uppercase tracking-widest">
                  View Mode:
                </span>
                <div className="flex items-center border border-cloudo-border p-1 bg-cloudo-dark/50">
                  <button
                    onClick={() => setViewMode("grid")}
                    className={`p-1.5 transition-all ${
                      viewMode === "grid"
                        ? "bg-cloudo-accent text-cloudo-dark"
                        : "text-cloudo-muted hover:text-cloudo-text"
                    }`}
                    title="Grid View"
                  >
                    <HiOutlineViewGrid className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => setViewMode("table")}
                    className={`p-1.5 transition-all ${
                      viewMode === "table"
                        ? "bg-cloudo-accent text-cloudo-dark"
                        : "text-cloudo-muted hover:text-cloudo-text"
                    }`}
                    title="Table View"
                  >
                    <HiOutlineViewList className="w-4 h-4" />
                  </button>
                </div>
              </div>

              <div className="text-[10px] font-black text-cloudo-muted uppercase tracking-widest">
                Results:{" "}
                <span className="text-cloudo-text">
                  {filteredSchemas.length}
                </span>{" "}
                / {schemas.length}
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-auto p-8 space-y-8">
        <div className="max-w-[1600px] mx-auto space-y-8">
          {/* Statistics Grid */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <StatSmall
              title="Total Assets"
              value={stats.total}
              icon={<HiOutlineTerminal />}
              label="SCHEMAS_LOAD"
            />
            <StatSmall
              title="Gate Required"
              value={stats.approvalRequired}
              icon={<HiOutlineShieldCheck />}
              label="AUTH_PENDING"
              color="text-cloudo-warn"
            />
            <StatSmall
              title="Active On-Call"
              value={stats.onCall}
              icon={<HiOutlineUserGroup />}
              label="CRITICAL_PATH"
              color="text-cloudo-accent"
            />
            <StatSmall
              title="Disabled"
              value={stats.disabled}
              icon={<HiOutlineBan />}
              label="INACTIVE_RUNBOOKS"
              color="text-cloudo-err"
            />
          </div>

          {loading ? (
            <div className="py-32 text-center text-cloudo-muted italic animate-pulse uppercase tracking-[0.5em] font-black opacity-50">
              Refreshing Schema Data...
            </div>
          ) : filteredSchemas.length === 0 ? (
            <div className="py-32 text-center text-sm font-black uppercase tracking-[0.5em] opacity-40 italic border border-cloudo-border bg-cloudo-panel">
              NO_ENTRIES_FOUND
            </div>
          ) : viewMode === "grid" ? (
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6">
              {paginatedSchemas.map((schema) => (
                <SchemaCard
                  key={schema.RowKey}
                  schema={schema}
                  isViewer={isViewer}
                  userRole={user?.role}
                  copiedId={copiedId}
                  confirmRunId={confirmRunId}
                  executingId={executingId}
                  togglingId={togglingId}
                  onCopyId={copyToClipboard}
                  onRun={handleRun}
                  onToggle={handleToggleSchema}
                  onConfirmRun={setConfirmRunId}
                  onViewSource={fetchRunbookContent}
                  onEdit={(s) => {
                    setSelectedSchema(s);
                    setModalMode(
                      !isViewer &&
                        (user?.role === "ADMIN" || user?.role === "OPERATOR") &&
                        !isTerraformSchema(s.tags)
                        ? "edit"
                        : "view",
                    );
                    fetchAvailableRunbooks();
                    fetchWorkers();
                  }}
                  onDelete={setSchemaToDelete}
                />
              ))}
            </div>
          ) : (
            <SchemaTable
              schemas={paginatedSchemas}
              isViewer={isViewer}
              userRole={user?.role}
              copiedId={copiedId}
              confirmRunId={confirmRunId}
              executingId={executingId}
              togglingId={togglingId}
              onCopyId={copyToClipboard}
              onRun={handleRun}
              onToggle={handleToggleSchema}
              onConfirmRun={setConfirmRunId}
              onViewSource={fetchRunbookContent}
              onEdit={(s) => {
                setSelectedSchema(s);
                setModalMode(
                  !isViewer &&
                    (user?.role === "ADMIN" || user?.role === "OPERATOR") &&
                    !isTerraformSchema(s.tags)
                    ? "edit"
                    : "view",
                );
                fetchAvailableRunbooks();
                fetchWorkers();
              }}
              onDelete={setSchemaToDelete}
            />
          )}

          {/* Pagination Controls */}
          {filteredSchemas.length > 0 && (
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
                    {[5, 10, 25, 50].map((size) => (
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
                    {Math.min(currentPage * pageSize, filteredSchemas.length)}
                  </span>{" "}
                  of{" "}
                  <span className="text-cloudo-text">
                    {filteredSchemas.length}
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

      {/* Form Modal */}
      {modalMode && (
        <div
          className="fixed inset-0 bg-cloudo-dark/90 backdrop-blur-sm flex items-center justify-center z-50 p-4"
          onClick={() => setModalMode(null)}
        >
          <div
            className="bg-cloudo-panel border border-cloudo-border shadow-2xl w-full max-w-xl overflow-hidden animate-in zoom-in-95 duration-200 relative"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Decorative corner */}
            <div className="absolute top-0 right-0 w-12 h-12 overflow-hidden pointer-events-none">
              <div className="absolute top-[-24px] right-[-24px] w-12 h-12 bg-cloudo-border rotate-45" />
            </div>

            <div className="px-8 py-5 border-b border-cloudo-border flex justify-between items-center bg-cloudo-accent/5">
              <div className="flex items-center gap-3">
                <div className="w-1.5 h-1.5 bg-cloudo-accent animate-pulse" />
                <h3 className="text-[10px] font-black uppercase tracking-[0.3em] text-cloudo-text">
                  {modalMode === "create"
                    ? "Register New Schema"
                    : "Update Configuration"}
                </h3>
              </div>
              <button
                onClick={() => setModalMode(null)}
                className="p-1.5 hover:bg-cloudo-err hover:text-cloudo-text border border-cloudo-border text-cloudo-muted transition-colors"
              >
                <HiOutlineX className="w-4 h-4" />
              </button>
            </div>

            <SchemaForm
              initialData={selectedSchema}
              mode={modalMode}
              availableRunbooks={availableRunbooks}
              availableWorkers={availableWorkers}
              onSuccess={(message) => {
                fetchSchemas();
                setModalMode(null);
                addNotification("success", message);
              }}
              onCancel={() => setModalMode(null)}
              onError={(message) => addNotification("error", message)}
            />
          </div>
        </div>
      )}

      {/* Delete Confirmation Modal */}
      {schemaToDelete && (
        <DeleteConfirmationModal
          schema={schemaToDelete}
          type="schemas"
          onClose={() => setSchemaToDelete(null)}
          onSuccess={(message) => {
            fetchSchemas();
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
