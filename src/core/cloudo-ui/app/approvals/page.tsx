"use client";

import { useState, useEffect, useMemo, useCallback, Suspense } from "react";
import { cloudoFetch } from "@/lib/api";
import { useSearchParams } from "next/navigation";
import {
  HiOutlineShieldCheck,
  HiOutlineTerminal,
  HiOutlineCheck,
  HiOutlineX,
  HiOutlineRefresh,
  HiOutlineClock,
  HiOutlineUser,
  HiOutlineFingerPrint,
  HiOutlineSearch,
  HiOutlineServer,
  HiOutlineExclamationCircle,
  HiOutlineCheckCircle,
  HiOutlineShare,
  HiOutlineArrowsExpand,
} from "react-icons/hi";

interface Notification {
  id: string;
  type: "success" | "error";
  message: string;
}

interface PendingApproval {
  ExecId: string;
  Name: string;
  Runbook: string;
  RequestedAt: string;
  Status: string;
  Log?: string;
  Run_Args?: string;
  Worker?: string;
  OnCall?: string;
  Initiator?: string;
  ResourceInfo?: string;
}

export default function ApprovalsPage() {
  return (
    <Suspense
      fallback={
        <div className="flex h-full items-center justify-center">
          <HiOutlineRefresh className="animate-spin w-8 h-8 text-cloudo-warn" />
        </div>
      }
    >
      <ApprovalsPageContent />
    </Suspense>
  );
}

function ApprovalsPageContent() {
  const searchParams = useSearchParams();
  const [pendingList, setPendingList] = useState<PendingApproval[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedExec, setSelectedExec] = useState<PendingApproval | null>(
    null,
  );
  const [isExpanded, setIsExpanded] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [linkCopied, setLinkCopied] = useState(false);

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

  const copyShareLink = () => {
    if (!selectedExec) return;
    const url = new URL(window.location.href);
    url.searchParams.set("execId", selectedExec.ExecId);
    navigator.clipboard.writeText(url.toString());
    setLinkCopied(true);
    setTimeout(() => setLinkCopied(false), 2000);
  };

  const approvalLinks = useMemo(() => {
    if (!selectedExec?.Log) return null;
    try {
      const parsed = JSON.parse(selectedExec.Log);
      const approveUrl = parsed.approve || null;
      let decodedPayload: {
        worker?: string;
        severity?: string;
        monitorCondition?: string;
        routing_info?: unknown;
        resource_info?: unknown;
      } | null = null;

      if (approveUrl) {
        try {
          const url = new URL(approveUrl);
          const p = url.searchParams.get("p");
          if (p) {
            let base64 = p.replace(/-/g, "+").replace(/_/g, "/");

            while (base64.length % 4) {
              base64 += "=";
            }

            const binString = atob(base64);
            const jsonPayload = decodeURIComponent(
              Array.prototype.map
                .call(binString, (c) => {
                  return "%" + ("00" + c.charCodeAt(0).toString(16)).slice(-2);
                })
                .join(""),
            );

            decodedPayload = JSON.parse(jsonPayload);
            console.log(decodedPayload);
          }
        } catch (e) {
          console.warn("Payload decoding failed:", e);
        }
      }

      const resource_info =
        decodedPayload?.resource_info ||
        parsed.resource_info ||
        parsed.response?.resource_info ||
        {};

      return {
        approve: approveUrl,
        reject: parsed.reject || null,
        message: parsed.message || "",
        display_info: { ...resource_info },
        worker: decodedPayload?.worker || selectedExec.Worker || null,
        severity: decodedPayload?.severity || null,
        monitor: decodedPayload?.monitorCondition || null,
        run_args: selectedExec.Run_Args || null,
        oncall: selectedExec.OnCall || null,
        initiator: selectedExec.Initiator || null,
      };
    } catch {
      return null;
    }
  }, [selectedExec]);

  const handleAction = async (url: string | null) => {
    const userData = localStorage.getItem("cloudo_user");
    const currentUser = userData ? JSON.parse(userData) : null;

    if (!url) return;
    setIsProcessing(true);
    try {
      const res = await cloudoFetch(url, {
        method: "GET",
        headers: {
          "x-Approver": currentUser?.username || "",
          "x-cloudo-user": currentUser?.username || "",
        },
      });
      if (res.ok) {
        addNotification("success", "Operation executed successfully");
        await fetchPendingApprovals();
      } else {
        addNotification("error", `Action failed: ${res.statusText}`);
      }
    } catch (e) {
      console.error(e);
      addNotification("error", "Error communicating with orchestrator");
    } finally {
      setIsProcessing(false);
    }
  };

  const fetchPendingApprovals = useCallback(async () => {
    setLoading(true);
    try {
      const now = new Date();
      const oneHourAgo = new Date(now.getTime() - 60 * 60 * 1000);

      const today = new Date();
      const partitionKey = `${today.getFullYear()}${String(
        today.getMonth() + 1,
      ).padStart(2, "0")}${String(today.getDate()).padStart(2, "0")}`;

      const res = await cloudoFetch(`/logs/query?partitionKey=${partitionKey}`);
      const data = await res.json();
      const items = (data.items || []) as {
        ExecId: string;
        Status: string;
        RequestedAt: string;
        ApprovalRequired?: boolean;
        Name?: string;
        Runbook?: string;
        Log?: string;
        Worker?: string;
        OnCall?: string;
        Initiator?: string;
        ResourceInfo?: string;
      }[];

      const terminalIds = new Set(
        items
          .filter((e) => {
            const s = (e.Status || "").toLowerCase();
            return [
              "succeeded",
              "failed",
              "rejected",
              "error",
              "skipped",
            ].includes(s);
          })
          .map((e) => e.ExecId),
      );

      const pendingMap = new Map<string, PendingApproval>();

      const sortedItems = [...items].sort(
        (a, b) =>
          new Date(a.RequestedAt).getTime() - new Date(b.RequestedAt).getTime(),
      );

      sortedItems.forEach((e) => {
        const id = e.ExecId;
        const status = (e.Status || "").toLowerCase();
        const requestedAt = new Date(e.RequestedAt);

        if (
          requestedAt < oneHourAgo ||
          terminalIds.has(id) ||
          e.ApprovalRequired !== true
        ) {
          return;
        }

        if (status === "pending" || status === "accepted") {
          const enriched: PendingApproval = {
            ...(e as unknown as PendingApproval),
            ExecId: e.ExecId,
            Name: e.Name || "Unlabeled Request",
            Runbook: e.Runbook || "Unknown",
            RequestedAt: e.RequestedAt,
            Status: status === "accepted" ? "running" : "pending",
            ResourceInfo: e.ResourceInfo,
          };
          pendingMap.set(id, enriched);
        }
      });

      const finalPendingList = Array.from(pendingMap.values()).sort(
        (a, b) =>
          new Date(b.RequestedAt).getTime() - new Date(a.RequestedAt).getTime(),
      );

      setPendingList(finalPendingList);

      // Deep link selection
      const initialExecId = searchParams.get("execId");

      // Update selection with new data if it exists, otherwise clear it
      setSelectedExec((prev) => {
        const targetId = initialExecId || (prev ? prev.ExecId : null);
        if (!targetId) return null;
        return pendingMap.get(targetId) || null;
      });
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, [searchParams]);

  useEffect(() => {
    fetchPendingApprovals();
  }, [fetchPendingApprovals]);

  return (
    <div className="flex flex-col h-full bg-cloudo-dark text-cloudo-text font-mono selection:bg-cloudo-accent/30">
      {/* Header Bar - Solid Technical Style */}
      <div className="flex items-center justify-between px-8 py-4 border-b border-cloudo-border bg-cloudo-panel sticky top-0 z-20">
        <div className="flex items-center gap-4 shrink-0">
          <div className="p-2 bg-cloudo-warn/5 border border-cloudo-warn/20 shrink-0">
            <HiOutlineShieldCheck className="text-cloudo-warn w-4 h-4" />
          </div>
          <div>
            <h1 className="text-sm font-black tracking-[0.2em] text-cloudo-text uppercase">
              Governance Gate
            </h1>
            <p className="text-[11px] text-cloudo-muted font-bold uppercase tracking-[0.3em] opacity-70">
              Filtered Approval Queue
            </p>
          </div>
        </div>
        <div className="flex items-center gap-4">
          <div className="text-[11px] font-black uppercase tracking-widest text-cloudo-muted bg-cloudo-accent/10 px-3 py-1.5 border border-cloudo-border">
            {pendingList.length} Requests Pending Signature
          </div>
          <button onClick={fetchPendingApprovals} className="btn btn-primary">
            <HiOutlineRefresh
              className={`w-4 h-4 ${loading ? "animate-spin" : ""}`}
            />
            Refresh
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-auto p-8">
        <div className="max-w-350 mx-auto">
          {loading ? (
            <div className="py-24 text-center flex flex-col items-center gap-4">
              <div className="w-8 h-8 border-2 border-cloudo-warn/30 border-t-cloudo-warn rounded-full animate-spin" />
              <span className="text-[11px] font-black uppercase tracking-[0.3em] text-cloudo-muted">
                Verifying Registry Compliance...
              </span>
            </div>
          ) : pendingList.length === 0 ? (
            <div className="py-32 text-center border border-cloudo-border bg-cloudo-accent/5">
              <HiOutlineShieldCheck className="w-16 h-16 text-cloudo-muted/80 mx-auto mb-6" />
              <p className="text-sm font-black uppercase tracking-[0.4em] text-cloudo-muted">
                No authorization requests detected
              </p>
              <p className="text-[11px] text-cloudo-muted/70 uppercase mt-2 tracking-widest">
                System is currently compliant
              </p>
            </div>
          ) : (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-12">
              {/* Left Column: List */}
              <div className="space-y-4">
                <div className="flex items-center gap-3 mb-6">
                  <div className="w-1.5 h-4 bg-cloudo-warn" />
                  <h2 className="text-sm font-black uppercase tracking-[0.4em] text-cloudo-text">
                    Pending Requests
                  </h2>
                </div>
                <div className="space-y-3">
                  {pendingList.map((item) => (
                    <div
                      key={item.ExecId}
                      onClick={() => setSelectedExec(item)}
                      className={`p-4 border transition-all cursor-pointer group relative ${
                        selectedExec?.ExecId === item.ExecId
                          ? "bg-cloudo-warn/5 border-cloudo-warn/40"
                          : "bg-cloudo-panel border-cloudo-border hover:border-cloudo-muted/70"
                      }`}
                    >
                      <div className="flex justify-between items-start mb-3">
                        <div className="flex items-center gap-3">
                          <div
                            className={`w-2 h-2 rounded-full ${
                              item.Status === "running"
                                ? "bg-cloudo-accent animate-pulse ring-2 ring-cloudo-accent/30"
                                : "bg-cloudo-warn"
                            }`}
                          />
                          <h3 className="text-sm font-black text-cloudo-text uppercase tracking-widest truncate max-w-45">
                            {item.Name || "SYS_TASK"}
                          </h3>
                          {item.Status === "running" && (
                            <span className="text-[10px] font-black text-cloudo-accent uppercase tracking-widest animate-pulse">
                              Running
                            </span>
                          )}
                        </div>
                        <span className="text-[11px] font-mono text-cloudo-muted opacity-70 uppercase">
                          {new Date(item.RequestedAt).toLocaleTimeString([], {
                            hour: "2-digit",
                            minute: "2-digit",
                            hour12: false,
                          })}
                        </span>
                      </div>
                      <div className="flex items-center gap-2 text-[11px] font-mono text-cloudo-accent/60 uppercase tracking-widest">
                        <HiOutlineTerminal className="w-4 h-4" />
                        {item.Runbook}
                      </div>
                      {selectedExec?.ExecId === item.ExecId && (
                        <div className="absolute -left-px top-0 w-0.5 h-full bg-cloudo-warn" />
                      )}
                    </div>
                  ))}
                </div>
              </div>

              {/* Right Column: Detail Panel */}
              <div className="space-y-4">
                <div className="flex items-center gap-3 mb-6">
                  <div className="w-1.5 h-4 bg-cloudo-accent" />
                  <h2 className="text-sm font-black uppercase tracking-[0.4em] text-cloudo-text">
                    Resource Details
                  </h2>
                </div>
                {selectedExec ? (
                  <div
                    className={`bg-cloudo-panel border border-cloudo-border flex flex-col transition-all duration-500 ease-in-out overflow-hidden ${
                      isExpanded
                        ? "fixed inset-4 z-60 shadow-2xl animate-in zoom-in-95 fade-in duration-500 overflow-y-auto custom-scrollbar ring-1 ring-cloudo-warn/20"
                        : "sticky top-8 animate-in fade-in slide-in-from-right-4 duration-300"
                    }`}
                  >
                    <div className="p-6 border-b border-cloudo-border bg-cloudo-accent/5 flex justify-between items-center">
                      <div className="flex items-center gap-4">
                        <div className="w-12 h-12 bg-cloudo-warn/10 border border-cloudo-warn/20 flex items-center justify-center text-cloudo-warn">
                          <HiOutlineFingerPrint className="w-6 h-6" />
                        </div>
                        <div>
                          <h2 className="text-sm font-black text-cloudo-text uppercase tracking-[0.2em]">
                            {selectedExec.Name}
                          </h2>
                          <p className="text-[11px] font-mono text-cloudo-muted uppercase tracking-widest mt-1">
                            Request_ID: {selectedExec.ExecId}
                          </p>
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => setIsExpanded(!isExpanded)}
                          className="p-2 text-cloudo-muted hover:text-cloudo-warn border border-cloudo-border transition-colors group/expand"
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
                              : "bg-cloudo-warn/10 border-cloudo-warn/20 text-cloudo-warn hover:bg-cloudo-warn hover:text-cloudo-dark"
                          }`}
                        >
                          {linkCopied ? (
                            <>
                              <HiOutlineCheck className="w-3.5 h-3.5" />
                              <span>Link_Copied</span>
                            </>
                          ) : (
                            <>
                              <HiOutlineShare className="w-3.5 h-3.5" />
                              <span>Share_Request</span>
                            </>
                          )}
                        </button>
                        <button
                          onClick={() => {
                            setSelectedExec(null);
                            setIsExpanded(false);
                          }}
                          className="p-2 text-cloudo-muted hover:text-cloudo-text transition-colors border border-cloudo-border group/expand"
                        >
                          <HiOutlineX className="w-5 h-5 transition-transform duration-300 group-hover/expand:rotate-90" />
                        </button>
                      </div>
                    </div>

                    <div className="flex-1 overflow-auto p-8 space-y-8 custom-scrollbar bg-cloudo-dark/30">
                      {/* Section: Identity & Routing */}
                      <div className="space-y-4">
                        <div className="flex items-center gap-2">
                          <div className="w-1 h-3 bg-cloudo-warn" />
                          <h3 className="text-[10px] font-black uppercase tracking-[0.2em] text-cloudo-muted">
                            Request Identity & Routing
                          </h3>
                        </div>
                        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3">
                          <DetailItem
                            label="Initiator"
                            value={
                              selectedExec.Initiator ||
                              selectedExec.OnCall ||
                              "AUTO_TRIGGER"
                            }
                            icon={<HiOutlineUser />}
                          />
                          <DetailItem
                            label="Node"
                            value={selectedExec.Worker || "DYNAMIC"}
                            icon={<HiOutlineServer className="w-4 h-4" />}
                          />
                          <DetailItem
                            label="Runbook"
                            value={selectedExec.Runbook}
                            icon={<HiOutlineTerminal />}
                          />
                          <DetailItem
                            label="Requested At"
                            className="md:col-span-2"
                            value={new Date(
                              selectedExec.RequestedAt,
                            ).toLocaleString([], {
                              day: "2-digit",
                              month: "2-digit",
                              year: "numeric",
                              hour: "2-digit",
                              minute: "2-digit",
                              second: "2-digit",
                              hour12: false,
                            })}
                            icon={<HiOutlineClock />}
                          />
                        </div>
                      </div>

                      {/* Section: Execution Context */}
                      <div className="space-y-4">
                        <div className="flex items-center gap-2">
                          <div className="w-1 h-3 bg-cloudo-accent" />
                          <h3 className="text-[10px] font-black uppercase tracking-[0.2em] text-cloudo-muted">
                            Execution Context
                          </h3>
                        </div>
                        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3">
                          <DetailItem
                            label="Priority"
                            value={approvalLinks?.severity || "NORMAL"}
                            icon={<HiOutlineClock />}
                          />
                          <DetailItem
                            label="Condition"
                            value={approvalLinks?.monitor || "DIRECT"}
                            icon={<HiOutlineSearch />}
                          />
                        </div>

                        {approvalLinks?.message && (
                          <div className="bg-cloudo-ok/5 border border-cloudo-ok/20 p-4 font-mono text-xs text-cloudo-ok/90 leading-relaxed break-all">
                            {approvalLinks.message}
                          </div>
                        )}
                      </div>

                      {/* Section: Runtime Arguments */}
                      <div className="space-y-4">
                        <div className="flex items-center gap-2">
                          <div className="w-1 h-3 bg-cloudo-accent" />
                          <h3 className="text-[10px] font-black uppercase tracking-[0.2em] text-cloudo-muted">
                            Runtime Arguments
                          </h3>
                        </div>
                        <div className="bg-cloudo-dark/60 border border-cloudo-border p-4 font-mono text-xs text-cloudo-accent/80 whitespace-pre-wrap break-all leading-relaxed">
                          {selectedExec.Run_Args || "NO_ARGS_PROVIDED"}
                        </div>
                      </div>

                      {/* Section: Compliance Manifest */}
                      {(() => {
                        let info: Record<string, unknown> = {};
                        if (selectedExec.ResourceInfo) {
                          try {
                            info = JSON.parse(selectedExec.ResourceInfo);
                          } catch (e) {
                            console.warn("Failed to parse ResourceInfo:", e);
                          }
                        }

                        // Fallback to display_info from log parsing if ResourceInfo is empty
                        const display_info =
                          Object.keys(info).length > 0
                            ? info
                            : (approvalLinks?.display_info as Record<
                                string,
                                unknown
                              >) || {};

                        if (Object.keys(display_info).length > 0) {
                          return (
                            <div className="space-y-4">
                              <div className="flex items-center gap-2">
                                <div className="w-1 h-3 bg-cloudo-warn" />
                                <h3 className="text-[10px] font-black uppercase tracking-[0.2em] text-cloudo-muted">
                                  Compliance Manifest & Resource Info
                                </h3>
                              </div>
                              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                                {Object.entries(display_info)
                                  .filter(
                                    ([__unused, v]) =>
                                      v !== null &&
                                      v !== undefined &&
                                      String(v).trim() !== "",
                                  )
                                  .map(([k, v]) => {
                                    const isRaw = k === "_raw";
                                    let displayValue = String(v);

                                    if (isRaw) {
                                      try {
                                        const parsed =
                                          typeof v === "string"
                                            ? JSON.parse(v)
                                            : v;
                                        displayValue = JSON.stringify(
                                          parsed,
                                          null,
                                          2,
                                        );
                                      } catch {
                                        // fallback to string
                                      }
                                    }

                                    return (
                                      <div
                                        key={k}
                                        className={`bg-cloudo-accent/10 border border-cloudo-border p-3 flex flex-col group gap-2 ${
                                          isRaw ? "md:col-span-2" : ""
                                        }`}
                                      >
                                        <span className="text-[10px] font-black text-cloudo-muted uppercase tracking-widest shrink-0">
                                          {k}
                                        </span>
                                        <span
                                          className={`text-xs font-mono text-cloudo-text group-hover:text-cloudo-accent transition-colors break-all ${
                                            isRaw
                                              ? "whitespace-pre-wrap text-left"
                                              : "text-right"
                                          }`}
                                        >
                                          {displayValue}
                                        </span>
                                      </div>
                                    );
                                  })}
                              </div>
                            </div>
                          );
                        }
                        return null;
                      })()}

                      {/* Action Bar */}
                      <div className="grid grid-cols-2 gap-4 pt-8 border-t border-cloudo-border">
                        <button
                          onClick={() => handleAction(approvalLinks?.reject)}
                          disabled={isProcessing || !approvalLinks?.reject}
                          className="flex items-center justify-center gap-2 bg-cloudo-err/10 hover:bg-cloudo-err hover:text-cloudo-text text-cloudo-err border border-cloudo-err/30 py-4 text-[11px] font-black uppercase tracking-[0.3em] transition-all disabled:opacity-60"
                        >
                          <HiOutlineX className="w-5 h-5" />
                          Reject Request
                        </button>
                        <button
                          onClick={() => handleAction(approvalLinks?.approve)}
                          disabled={isProcessing || !approvalLinks?.approve}
                          className="flex items-center justify-center gap-2 bg-cloudo-warn hover:bg-cloudo-warn/90 text-cloudo-dark py-4 text-[11px] font-black uppercase tracking-[0.3em] transition-all disabled:opacity-60"
                        >
                          <HiOutlineCheck className="w-5 h-5" />
                          Sign and Authorize
                        </button>
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="h-[400px] flex flex-col items-center justify-center border border-cloudo-border/10 bg-cloudo-dark/10 text-cloudo-muted/70">
                    <HiOutlineShieldCheck className="w-16 h-16 mb-4 opacity-40" />
                    <span className="text-sm font-black uppercase tracking-[0.4em]">
                      Select request to audit
                    </span>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

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
      className={`bg-cloudo-warn/5 border border-cloudo-border p-3 space-y-2 overflow-hidden ${className}`}
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
