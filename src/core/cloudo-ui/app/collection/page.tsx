"use client";

import React, { useState, useEffect, useMemo, useCallback } from "react";
import { cloudoFetch } from "@/lib/api";
import { parseRunbookIntoCells } from "../utils/parser";
import {
  HiOutlineSearch,
  HiOutlineTerminal,
  HiOutlineClipboardCopy,
  HiOutlineX,
  HiOutlineCheckCircle,
  HiOutlineExclamationCircle,
  HiOutlineRefresh,
  HiOutlineDownload,
  HiOutlineFolder,
  HiOutlineFolderOpen,
  HiCode,
} from "react-icons/hi";
import { HiOutlineCollection } from "react-icons/hi";
import { HiMiniComputerDesktop } from "react-icons/hi2";

interface Notification {
  id: string;
  type: "success" | "error";
  message: string;
}

export default function CollectionPage() {
  const [runbooks, setRunbooks] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [currentPath, setCurrentPath] = useState<string>("");
  const [notifications, setNotifications] = useState<Notification[]>([]);

  const [runbookContent, setRunbookContent] = useState<string | null>(null);
  const [selectedRunbook, setSelectedRunbook] = useState<string | null>(null);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [fetchingContent, setFetchingContent] = useState(false);
  const [codeSourceSelector, setCodeSourceSelector] = useState<
    "parsed" | "source"
  >("parsed");

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
    fetchRunbooks();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const fetchRunbooks = useCallback(async () => {
    setLoading(true);
    try {
      const res = await cloudoFetch(`/runbooks/list`);
      const data = await res.json();
      if (res.ok && Array.isArray(data.runbooks)) {
        setRunbooks(data.runbooks);
      } else {
        setRunbooks([]);
      }
    } catch {
      setRunbooks([]);
      addNotification("error", "Failed to fetch runbooks");
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchRunbookContent = async (name: string) => {
    setFetchingContent(true);
    setRunbookContent(null);
    setSelectedRunbook(name);
    setIsModalOpen(true);
    try {
      const res = await cloudoFetch(
        `/runbooks/content?name=${encodeURIComponent(name)}`,
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
      setFetchingContent(false);
    }
  };

  const downloadRunbook = (name: string, content: string) => {
    const element = document.createElement("a");
    const file = new Blob([content], { type: "text/plain" });
    element.href = URL.createObjectURL(file);
    element.download = name;
    document.body.appendChild(element);
    element.click();
    document.body.removeChild(element);
  };

  const filteredRunbooks = useMemo(() => {
    return runbooks.filter((rb) =>
      rb.toLowerCase().includes(searchQuery.toLowerCase()),
    );
  }, [runbooks, searchQuery]);

  const currentLevelItems = useMemo(() => {
    if (searchQuery) return { files: filteredRunbooks, folders: [] };

    const folders = new Set<string>();
    const files: string[] = [];

    runbooks.forEach((rb) => {
      if (currentPath === "") {
        const parts = rb.split("/");
        if (parts.length > 1) {
          folders.add(parts[0]);
        } else {
          files.push(rb);
        }
      } else {
        if (rb.startsWith(currentPath + "/")) {
          const relativePath = rb.substring(currentPath.length + 1);
          const parts = relativePath.split("/");
          if (parts.length > 1) {
            folders.add(parts[0]);
          } else {
            files.push(rb);
          }
        }
      }
    });

    return {
      folders: Array.from(folders).sort(),
      files: files.sort(),
    };
  }, [runbooks, currentPath, searchQuery, filteredRunbooks]);

  const breadcrumbs = useMemo(() => {
    if (currentPath === "") return [];
    const parts = currentPath.split("/");
    return parts.map((part, index) => ({
      name: part,
      path: parts.slice(0, index + 1).join("/"),
    }));
  }, [currentPath]);

  const navigateTo = (path: string) => {
    setCurrentPath(path);
    setSearchQuery("");
  };

  const navigateUp = () => {
    const parts = currentPath.split("/");
    if (parts.length <= 1) {
      setCurrentPath("");
    } else {
      setCurrentPath(parts.slice(0, -1).join("/"));
    }
  };

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
            <HiOutlineCollection className="text-cloudo-accent w-4 h-4" />
          </div>
          <div>
            <h1 className="text-sm font-black tracking-[0.2em] text-cloudo-text uppercase">
              Runbook Collection
            </h1>
            <p className="text-[11px] text-cloudo-muted font-bold uppercase tracking-[0.3em] opacity-70">
              Source Repository // SCRIPTS_LIST
            </p>
          </div>
        </div>

        <div className="flex items-center gap-6">
          <div className="relative group">
            <HiOutlineSearch className="absolute left-3 top-1/2 -translate-y-1/2 text-cloudo-muted/70 w-4 h-4 group-focus-within:text-cloudo-accent transition-colors" />
            <input
              type="text"
              placeholder="Search runbooks..."
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
            onClick={() => {
              fetchRunbooks();
              setCurrentPath("");
              setSearchQuery("");
            }}
            className="btn btn-primary h-10 px-4 flex items-center gap-2 group"
          >
            <HiOutlineRefresh
              className={`w-4 h-4 ${loading ? "animate-spin" : ""}`}
            />{" "}
            Refresh
          </button>
        </div>
      </div>

      {/* Breadcrumbs / Path Bar */}
      {!searchQuery && (
        <div className="px-8 py-3 bg-cloudo-panel/50 border-b border-cloudo-border flex items-center gap-2 text-[10px] font-black uppercase tracking-widest overflow-x-auto">
          <button
            onClick={() => navigateTo("")}
            className={`hover:text-cloudo-accent transition-colors shrink-0 ${
              currentPath === "" ? "text-cloudo-accent" : "text-cloudo-muted"
            }`}
          >
            ROOT_DIR
          </button>
          {breadcrumbs.map((bc, idx) => (
            <React.Fragment key={bc.path}>
              <span className="text-cloudo-muted/40 shrink-0">/</span>
              <button
                onClick={() => navigateTo(bc.path)}
                className={`hover:text-cloudo-accent transition-colors shrink-0 ${
                  idx === breadcrumbs.length - 1
                    ? "text-cloudo-accent"
                    : "text-cloudo-muted"
                }`}
              >
                {bc.name}
              </button>
            </React.Fragment>
          ))}
        </div>
      )}

      <div className="flex-1 overflow-auto p-8">
        <div className="max-w-[1400px] mx-auto">
          <div className="border border-cloudo-border bg-cloudo-panel overflow-hidden relative group/table">
            <div className="absolute top-0 left-0 w-8 h-8 border-t border-l border-cloudo-accent/20 pointer-events-none" />
            <div className="absolute bottom-0 right-0 w-8 h-8 border-b border-r border-cloudo-accent/20 pointer-events-none" />

            <table className="w-full text-left border-collapse table-fixed text-sm">
              <thead>
                <tr className="border-b border-cloudo-border bg-cloudo-accent/10">
                  <th className="w-[70%] px-8 py-5 font-black text-cloudo-muted uppercase tracking-[0.3em] text-[11px]">
                    Runbooks
                  </th>
                  <th className="w-[30%] px-8 py-5 font-black text-cloudo-muted uppercase tracking-[0.3em] text-right text-[11px]">
                    Source
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-cloudo-border/30">
                {loading ? (
                  <tr>
                    <td
                      colSpan={2}
                      className="py-32 text-center text-cloudo-muted italic animate-pulse uppercase tracking-[0.5em] font-black opacity-50"
                    >
                      Loading Collection...
                    </td>
                  </tr>
                ) : currentLevelItems.folders.length === 0 &&
                  currentLevelItems.files.length === 0 ? (
                  <tr>
                    <td
                      colSpan={2}
                      className="py-32 text-center text-sm font-black uppercase tracking-[0.5em] opacity-40 italic"
                    >
                      NO_ENTRIES_FOUND
                    </td>
                  </tr>
                ) : (
                  <>
                    {/* Back button if in a subfolder */}
                    {!searchQuery && currentPath !== "" && (
                      <tr
                        className="bg-cloudo-accent/5 border-b border-cloudo-border/50 cursor-pointer hover:bg-cloudo-accent/10 transition-colors"
                        onClick={navigateUp}
                      >
                        <td colSpan={2} className="px-8 py-3">
                          <div className="flex items-center gap-3">
                            <HiOutlineFolderOpen className="text-cloudo-accent w-4 h-4" />
                            <span className="text-[11px] font-black text-cloudo-accent uppercase tracking-[0.3em]">
                              .. (GO_BACK_UP)
                            </span>
                          </div>
                        </td>
                      </tr>
                    )}

                    {/* Folders */}
                    {currentLevelItems.folders.map((folder) => (
                      <tr
                        key={folder}
                        className="bg-cloudo-accent/5 border-b border-cloudo-border/50 cursor-pointer hover:bg-cloudo-accent/10 transition-colors group"
                        onClick={() =>
                          navigateTo(
                            currentPath ? `${currentPath}/${folder}` : folder,
                          )
                        }
                      >
                        <td className="px-8 py-4">
                          <div className="flex items-center gap-3">
                            <HiOutlineFolder className="text-cloudo-accent w-4 h-4 group-hover:scale-110 transition-transform" />
                            <span className="text-[11px] font-black text-cloudo-muted uppercase tracking-[0.3em] group-hover:text-cloudo-accent">
                              {folder.toUpperCase()}
                            </span>
                          </div>
                        </td>
                        <td className="px-8 py-4 text-right">
                          <span className="text-[10px] text-cloudo-muted/40 font-bold uppercase tracking-widest">
                            Folder
                          </span>
                        </td>
                      </tr>
                    ))}

                    {/* Files */}
                    {currentLevelItems.files.map((rb) => (
                      <tr
                        key={rb}
                        className="group hover:bg-cloudo-accent/[0.02] transition-colors relative border-l-2 border-l-transparent hover:border-l-cloudo-accent/40"
                      >
                        <td className="px-12 py-6">
                          <div className="flex items-center gap-3">
                            <button
                              onClick={() => fetchRunbookContent(rb)}
                              className="p-1.5 bg-cloudo-accent/10 border border-cloudo-border group-hover:border-cloudo-accent/20 hover:bg-cloudo-accent/20 transition-all cursor-pointer"
                            >
                              <HiOutlineTerminal className="text-cloudo-accent w-4 h-4" />
                            </button>
                            <span
                              className="text-sm font-black text-cloudo-text tracking-[0.1em] uppercase group-hover:text-cloudo-accent transition-colors cursor-pointer"
                              onClick={() => fetchRunbookContent(rb)}
                            >
                              {searchQuery
                                ? rb
                                : rb.includes("/")
                                  ? rb.split("/").pop()
                                  : rb}
                            </span>
                          </div>
                        </td>
                        <td className="px-8 py-6 text-right">
                          <button
                            onClick={() => fetchRunbookContent(rb)}
                            className="px-4 py-2 bg-cloudo-accent/10 border border-cloudo-border text-[10px] font-black uppercase tracking-widest text-cloudo-accent hover:bg-cloudo-accent hover:text-cloudo-dark transition-all"
                          >
                            View Source
                          </button>
                        </td>
                      </tr>
                    ))}
                  </>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* Viewer Modal */}
      {isModalOpen && (
        <div
          className="fixed inset-0 bg-cloudo-dark/95 backdrop-blur-md flex items-center justify-center z-[70] p-4"
          onClick={() => setIsModalOpen(false)}
        >
          <div
            className="bg-cloudo-panel border border-cloudo-border shadow-2xl w-full max-w-4xl max-h-[80vh] flex flex-col animate-in zoom-in-95 duration-200 relative"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="px-8 py-4 border-b border-cloudo-border flex justify-between items-center bg-cloudo-accent/5">
              <div className="flex items-center gap-3">
                <HiOutlineTerminal className="text-cloudo-accent w-4 h-4" />
                <h3 className="text-[10px] font-black uppercase tracking-[0.3em] text-cloudo-text">
                  Runbook Source: {selectedRunbook}
                </h3>
                <button
                  onClick={() => setCodeSourceSelector("parsed")}
                  className={`px-3 py-1 text-[10px] cursor-pointer font-black uppercase tracking-widest transition-all flex items-center gap-2 ${
                    codeSourceSelector === "parsed"
                      ? "bg-cloudo-accent text-cloudo-dark"
                      : "text-cloudo-muted hover:text-cloudo-text"
                  }`}
                >
                  <HiMiniComputerDesktop className="w-3 h-3" /> Parsed Code
                </button>
                <button
                  onClick={() => setCodeSourceSelector("source")}
                  className={`px-3 py-1 text-[10px] cursor-pointer font-black uppercase tracking-widest transition-all flex items-center gap-2 ${
                    codeSourceSelector === "source"
                      ? "bg-cloudo-accent text-cloudo-dark"
                      : "text-cloudo-muted hover:text-cloudo-text"
                  }`}
                >
                  <HiCode className="w-3 h-3" /> Raw Code
                </button>
              </div>
              <button
                onClick={() => setIsModalOpen(false)}
                className="p-1.5 hover:bg-cloudo-err hover:text-cloudo-text border border-cloudo-border text-cloudo-muted transition-colors"
              >
                <HiOutlineX className="w-4 h-4" />
              </button>
            </div>

            {/* ── content area ── */}
            <div className="flex-1 overflow-auto p-6 input-editor font-mono text-xs bg-black/40 space-y-2">
              {fetchingContent ? (
                <div className="flex items-center justify-center h-64 text-cloudo-accent animate-pulse uppercase tracking-widest font-black">
                  Retrieving Source from Git...
                </div>
              ) : codeSourceSelector === "parsed" ? (
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
              ) : (
                <pre className="text-cloudo-text/90 whitespace-pre-wrap break-all leading-relaxed">
                  {runbookContent || "No content available."}
                </pre>
              )}
            </div>

            <div className="px-8 py-3 border-t border-cloudo-border bg-cloudo-panel flex justify-between items-center">
              <span className="text-[9px] text-cloudo-muted uppercase font-bold tracking-widest opacity-60">
                System Isolated Viewer // READ_ONLY
              </span>
              <div className="flex gap-4">
                <button
                  onClick={() => {
                    if (runbookContent) {
                      navigator.clipboard.writeText(runbookContent);
                      addNotification("success", "Source copied to clipboard");
                    }
                  }}
                  disabled={!runbookContent || fetchingContent}
                  className="flex items-center gap-2 text-[9px] font-black uppercase tracking-widest text-cloudo-accent hover:text-white transition-colors disabled:opacity-30"
                >
                  <HiOutlineClipboardCopy className="w-3.5 h-3.5" /> Copy Code
                </button>
                <button
                  onClick={() => {
                    if (runbookContent && selectedRunbook) {
                      downloadRunbook(selectedRunbook, runbookContent);
                      addNotification(
                        "success",
                        "Source exported successfully",
                      );
                    }
                  }}
                  disabled={!runbookContent || fetchingContent}
                  className="flex items-center gap-2 text-[9px] font-black uppercase tracking-widest text-cloudo-accent hover:text-white transition-colors disabled:opacity-30"
                >
                  <HiOutlineDownload className="w-3.5 h-3.5" /> Export Code
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
