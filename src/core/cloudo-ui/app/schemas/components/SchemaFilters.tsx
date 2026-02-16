import React from "react";
import { HiOutlineX } from "react-icons/hi";
import { SiTerraform } from "react-icons/si";

interface SchemaFiltersProps {
  searchQuery: string;
  setSearchQuery: (query: string) => void;
  activeFilter: "all" | "terraform" | "ui";
  setActiveFilter: (filter: "all" | "terraform" | "ui") => void;
  workerFilter: string;
  setWorkerFilter: (worker: string) => void;
  approvalFilter: "all" | "required" | "auto";
  setApprovalFilter: (filter: "all" | "required" | "auto") => void;
  oncallFilter: "all" | "active" | "inactive";
  setOncallFilter: (filter: "all" | "active" | "inactive") => void;
  enabledFilter: "all" | "enabled" | "disabled";
  setEnabledFilter: (filter: "all" | "enabled" | "disabled") => void;
  tagFilter: string;
  setTagFilter: (tag: string) => void;
  availableWorkers: string[];
  availableTags: string[];
}

export function SchemaFilters({
  searchQuery,
  setSearchQuery,
  activeFilter,
  setActiveFilter,
  workerFilter,
  setWorkerFilter,
  approvalFilter,
  setApprovalFilter,
  oncallFilter,
  setOncallFilter,
  enabledFilter,
  setEnabledFilter,
  tagFilter,
  setTagFilter,
  availableWorkers,
  availableTags,
}: SchemaFiltersProps) {
  const hasActiveFilters =
    searchQuery !== "" ||
    activeFilter !== "all" ||
    workerFilter !== "all" ||
    approvalFilter !== "all" ||
    oncallFilter !== "all" ||
    enabledFilter !== "all" ||
    tagFilter !== "all";

  const clearFilters = () => {
    setSearchQuery("");
    setActiveFilter("all");
    setWorkerFilter("all");
    setApprovalFilter("all");
    setOncallFilter("all");
    setEnabledFilter("all");
    setTagFilter("all");
  };

  return (
    <div className="flex flex-col gap-4 bg-cloudo-panel/50 border border-cloudo-border p-4">
      <div className="flex flex-wrap items-center gap-4">
        {/* Source Filter */}
        <div className="flex items-center border border-cloudo-border p-1 bg-cloudo-dark/50">
          <button
            onClick={() => setActiveFilter("all")}
            className={`px-3 py-1 text-[10px] font-black uppercase tracking-widest transition-all ${
              activeFilter === "all"
                ? "bg-cloudo-accent text-cloudo-dark"
                : "text-cloudo-muted hover:text-cloudo-text"
            }`}
          >
            All
          </button>
          <button
            onClick={() => setActiveFilter("terraform")}
            className={`px-3 py-1 text-[10px] font-black uppercase tracking-widest transition-all flex items-center gap-2 ${
              activeFilter === "terraform"
                ? "bg-[#7B42BC] text-white"
                : "text-cloudo-muted hover:text-cloudo-text"
            }`}
          >
            <SiTerraform className="w-3 h-3" /> TF
          </button>
          <button
            onClick={() => setActiveFilter("ui")}
            className={`px-3 py-1 text-[10px] font-black uppercase tracking-widest transition-all ${
              activeFilter === "ui"
                ? "bg-cloudo-accent text-cloudo-dark"
                : "text-cloudo-muted hover:text-cloudo-text"
            }`}
          >
            UI
          </button>
        </div>

        {/* Worker Filter */}
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-black uppercase tracking-widest text-cloudo-muted">
            Worker:
          </span>
          <select
            value={workerFilter}
            onChange={(e) => setWorkerFilter(e.target.value)}
            className="bg-cloudo-dark border border-cloudo-border text-cloudo-text text-[10px] font-black px-2 py-1 outline-none focus:border-cloudo-accent/50 transition-colors"
          >
            <option value="all">ALL_WORKERS</option>
            {availableWorkers.map((w) => (
              <option key={w} value={w}>
                {w.toUpperCase()}
              </option>
            ))}
          </select>
        </div>

        {/* Approval Filter */}
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-black uppercase tracking-widest text-cloudo-muted">
            Auth:
          </span>
          <select
            value={approvalFilter}
            onChange={(e) =>
              setApprovalFilter(e.target.value as "all" | "required" | "auto")
            }
            className="bg-cloudo-dark border border-cloudo-border text-cloudo-text text-[10px] font-black px-2 py-1 outline-none focus:border-cloudo-accent/50 transition-colors"
          >
            <option value="all">ALL_STATUS</option>
            <option value="required">GATE_REQUIRED</option>
            <option value="auto">AUTO_EXECUTE</option>
          </select>
        </div>

        {/* On-Call Filter */}
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-black uppercase tracking-widest text-cloudo-muted">
            On-Call:
          </span>
          <select
            value={oncallFilter}
            onChange={(e) =>
              setOncallFilter(e.target.value as "all" | "active" | "inactive")
            }
            className="bg-cloudo-dark border border-cloudo-border text-cloudo-text text-[10px] font-black px-2 py-1 outline-none focus:border-cloudo-accent/50 transition-colors"
          >
            <option value="all">ALL_FLOWS</option>
            <option value="active">CRITICAL_ONLY</option>
            <option value="inactive">STANDARD_ONLY</option>
          </select>
        </div>

        {/* Enabled Filter */}
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-black uppercase tracking-widest text-cloudo-muted">
            Status:
          </span>
          <select
            value={enabledFilter}
            onChange={(e) =>
              setEnabledFilter(e.target.value as "all" | "enabled" | "disabled")
            }
            className="bg-cloudo-dark border border-cloudo-border text-cloudo-text text-[10px] font-black px-2 py-1 outline-none focus:border-cloudo-accent/50 transition-colors"
          >
            <option value="all">ALL_STATUS</option>
            <option value="enabled">ENABLED_ONLY</option>
            <option value="disabled">DISABLED_ONLY</option>
          </select>
        </div>

        {/* Tag Filter */}
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-black uppercase tracking-widest text-cloudo-muted">
            Tag:
          </span>
          <select
            value={tagFilter}
            onChange={(e) => setTagFilter(e.target.value)}
            className="bg-cloudo-dark border border-cloudo-border text-cloudo-text text-[10px] font-black px-2 py-1 outline-none focus:border-cloudo-accent/50 transition-colors"
          >
            <option value="all">ALL_TAGS</option>
            {availableTags.map((tag) => (
              <option key={tag} value={tag}>
                {tag.toUpperCase()}
              </option>
            ))}
          </select>
        </div>

        {hasActiveFilters && (
          <button
            onClick={clearFilters}
            className="flex items-center gap-2 px-3 py-1 text-[10px] font-black uppercase tracking-widest text-cloudo-err hover:bg-cloudo-err/10 transition-colors border border-cloudo-err/30"
          >
            <HiOutlineX className="w-3 h-3" /> Clear Filters
          </button>
        )}
      </div>
    </div>
  );
}
