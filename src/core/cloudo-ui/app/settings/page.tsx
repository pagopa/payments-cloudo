"use client";

import { useState, useEffect } from "react";
import { cloudoFetch } from "@/lib/api";
import { useRouter } from "next/navigation";
import {
  HiOutlineCog,
  HiOutlineShieldCheck,
  HiOutlineSave,
  HiOutlineRefresh,
  HiOutlineCheckCircle,
  HiOutlineExclamationCircle,
  HiOutlineInformationCircle,
  HiOutlineDatabase,
  HiOutlineX,
} from "react-icons/hi";

interface Settings {
  [key: string]: string;
}

interface User {
  username: string | null;
  email: string | null;
  password: string | null;
  role: string | null;
}

interface Notification {
  id: string;
  type: "success" | "error";
  message: string;
}

export default function SettingsPage() {
  const router = useRouter();
  const [user, setUser] = useState<User>();
  const [settings, setSettings] = useState<Settings>({
    RUNBOOK_TIMEOUT_MIN: "30",
    LOG_RETENTION_DAYS: "90",
    NOTIFICATION_SLACK_WEBHOOK: "",
    NOTIFICATION_TEAMS_WEBHOOK: "",
    SYSTEM_MAINTENANCE_MODE: "false",
    AUDIT_ENABLED: "true",
  });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [notifications, setNotifications] = useState<Notification[]>([]);

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

  useEffect(() => {
    const userData = localStorage.getItem("cloudo_user");
    if (userData) {
      try {
        const parsedUser = JSON.parse(userData);
        if (parsedUser.role !== "ADMIN" && parsedUser.role !== "OPERATOR") {
          router.push("/profile");
          return;
        }
        setUser(parsedUser);
        fetchSettings();
      } catch {
        router.push("/login");
      }
    } else {
      router.push("/login");
    }
  }, [router]);

  const fetchSettings = async () => {
    setLoading(true);
    try {
      const res = await cloudoFetch(`/settings`);

      if (res.ok) {
        const data = await res.json();
        if (Object.keys(data).length > 0) {
          setSettings((prev) => ({ ...prev, ...data }));
        }
      }
    } catch {
      console.error("Failed to fetch settings");
    } finally {
      setLoading(false);
    }
  };

  const saveSettings = async () => {
    setSaving(true);
    try {
      const res = await cloudoFetch(`/settings`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(settings),
      });

      if (res.ok) {
        addNotification("success", "Global configuration updated successfully");
      } else {
        addNotification("error", "Failed to update settings");
      }
    } catch {
      addNotification("error", "Uplink failed");
    } finally {
      setSaving(false);
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
            <HiOutlineCog className="text-cloudo-accent w-5 h-5" />
          </div>
          <div>
            <h1 className="text-sm font-black tracking-[0.2em] text-cloudo-text uppercase">
              Account & System Settings
            </h1>
            <p className="text-[11px] text-cloudo-muted font-bold uppercase tracking-[0.3em] opacity-70">
              User Profile & Global Config // SETTINGS_GATE
            </p>
          </div>
        </div>

        <div className="flex items-center gap-4">
          <button
            onClick={() => {
              if (user?.role === "ADMIN") fetchSettings();
            }}
            className="btn btn-ghost h-10 px-4 flex items-center gap-2"
          >
            <HiOutlineRefresh
              className={`w-4 h-4 ${loading ? "animate-spin" : ""}`}
            />
            Sync
          </button>
          {user?.role === "ADMIN" && (
            <button
              onClick={saveSettings}
              disabled={saving}
              className="btn btn-primary h-10 px-6 flex items-center gap-2"
            >
              <HiOutlineSave className="w-4 h-4" />
              {saving ? "Saving..." : "Commit Global Changes"}
            </button>
          )}
          {user?.role !== "ADMIN" && (
            <div className="flex items-center gap-2 px-4 py-2 bg-cloudo-accent/5 border border-cloudo-accent/20 text-cloudo-muted text-[10px] font-black uppercase tracking-widest">
              <HiOutlineInformationCircle className="w-4 h-4 text-cloudo-accent" />
              READ_ONLY_ACCESS
            </div>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-auto p-8">
        <div className="max-w-4xl mx-auto space-y-12">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-8 pt-4">
            {/* Execution Policy Section */}
            <div className="space-y-6">
              <div className="flex items-center gap-3">
                <div className="w-1.5 h-4 bg-cloudo-accent" />
                <h2 className="text-sm font-black uppercase tracking-[0.4em] text-cloudo-text">
                  Execution Policy
                </h2>
              </div>

              <div className="bg-cloudo-panel border border-cloudo-border p-6 space-y-6">
                <div className="space-y-2">
                  <label className="text-[11px] font-black uppercase tracking-widest text-cloudo-muted ml-1 block">
                    Runbook Timeout (Minutes)
                  </label>
                  <input
                    type="number"
                    className="input h-11 w-full disabled:opacity-50 disabled:cursor-not-allowed"
                    value={settings.RUNBOOK_TIMEOUT_MIN}
                    disabled={user?.role !== "ADMIN"}
                    onChange={(e) =>
                      setSettings({
                        ...settings,
                        RUNBOOK_TIMEOUT_MIN: e.target.value,
                      })
                    }
                  />
                  <p className="text-[10px] text-cloudo-muted/70 uppercase tracking-tight ml-1">
                    Kills processes exceeding this threshold
                  </p>
                </div>

                <div className="space-y-2">
                  <label className="text-[11px] font-black uppercase tracking-widest text-cloudo-muted ml-1 block">
                    Log Retention (Days)
                  </label>
                  <input
                    type="number"
                    className="input h-11 w-full disabled:opacity-50 disabled:cursor-not-allowed"
                    value={settings.LOG_RETENTION_DAYS}
                    disabled={user?.role !== "ADMIN"}
                    onChange={(e) =>
                      setSettings({
                        ...settings,
                        LOG_RETENTION_DAYS: e.target.value,
                      })
                    }
                  />
                  <p className="text-[10px] text-cloudo-muted/70 uppercase tracking-tight ml-1">
                    Automatic pruning of old telemetry data
                  </p>
                </div>

                <div
                  className="flex items-center justify-between p-4 bg-cloudo-accent/10 border border-cloudo-border group hover:border-cloudo-accent/40 transition-all cursor-pointer"
                  onClick={() =>
                    setSettings({
                      ...settings,
                      SYSTEM_MAINTENANCE_MODE:
                        settings.SYSTEM_MAINTENANCE_MODE === "true"
                          ? "false"
                          : "true",
                    })
                  }
                >
                  <div className="space-y-1">
                    <p className="text-sm font-black text-cloudo-text uppercase tracking-widest">
                      Maintenance Mode
                    </p>
                    <p className="text-[11px] text-cloudo-muted uppercase font-bold opacity-70">
                      Lock all executions
                    </p>
                  </div>
                  <div
                    className={`w-5 h-5 border flex items-center justify-center transition-all ${
                      settings.SYSTEM_MAINTENANCE_MODE === "true"
                        ? "bg-cloudo-err border-cloudo-err text-cloudo-text"
                        : "border-cloudo-border"
                    }`}
                  >
                    {settings.SYSTEM_MAINTENANCE_MODE === "true" && (
                      <HiOutlineShieldCheck className="w-4 h-4" />
                    )}
                  </div>
                </div>
              </div>
            </div>

            {/* Notification Channels Section */}
            <div className="space-y-6">
              <div className="flex items-center gap-3">
                <div className="w-1.5 h-4 bg-cloudo-warn" />
                <h2 className="text-sm font-black uppercase tracking-[0.4em] text-cloudo-text">
                  Security & Alerts
                </h2>
              </div>

              <div className="bg-cloudo-panel border border-cloudo-border p-6 space-y-6">
                {/*
                <div className="space-y-2">
                  <div className="flex items-center gap-2 mb-1">
                    <HiOutlineBell className="text-cloudo-warn w-4 h-4" />
                    <label className="text-[11px] font-black uppercase tracking-widest text-cloudo-muted block">Slack Integration Hook</label>
                  </div>
                  <input
                    type="password"
                    className="input h-11 text-sm w-full"
                    placeholder="https://hooks.slack.com/services/..."
                    value={settings.NOTIFICATION_SLACK_WEBHOOK}
                    onChange={e => setSettings({...settings, NOTIFICATION_SLACK_WEBHOOK: e.target.value})}
                  />
                </div>
                  */}

                {/*
                <div className="space-y-2">
                  <div className="flex items-center gap-2 mb-1">
                    <HiOutlineBell className="text-cloudo-accent w-4 h-4" />
                    <label className="text-[11px] font-black uppercase tracking-widest text-cloudo-muted block">Teams Webhook Endpoint</label>
                  </div>
                  <input
                    type="password"
                    className="input h-11 text-sm w-full"
                    placeholder="https://cloudo.webhook.office.com/..."
                    value={settings.NOTIFICATION_TEAMS_WEBHOOK}
                    onChange={e => setSettings({...settings, NOTIFICATION_TEAMS_WEBHOOK: e.target.value})}
                  />
                </div>

                  */}

                <div
                  className="flex items-center justify-between p-4 bg-cloudo-accent/10 border border-cloudo-border group hover:border-cloudo-accent/40 transition-all cursor-pointer"
                  onClick={() =>
                    setSettings({
                      ...settings,
                      AUDIT_ENABLED:
                        settings.AUDIT_ENABLED === "true" ? "false" : "true",
                    })
                  }
                >
                  <div className="space-y-1">
                    <p className="text-sm font-black text-cloudo-text uppercase tracking-widest">
                      Audit Engine
                    </p>
                    <p className="text-[11px] text-cloudo-muted uppercase font-bold opacity-70">
                      Log all operator actions
                    </p>
                  </div>
                  <div
                    className={`w-5 h-5 border flex items-center justify-center transition-all ${
                      settings.AUDIT_ENABLED === "true"
                        ? "bg-cloudo-accent border-cloudo-accent text-cloudo-dark"
                        : "border-cloudo-border"
                    }`}
                  >
                    {settings.AUDIT_ENABLED === "true" && (
                      <HiOutlineCheckCircle className="w-4 h-4" />
                    )}
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* System Information Panel */}
          <div className="bg-cloudo-accent/5 border border-cloudo-accent/20 p-8 relative overflow-hidden group">
            <div className="absolute top-0 right-0 p-4 opacity-40 group-hover:opacity-60 transition-opacity">
              <HiOutlineDatabase className="w-24 h-24" />
            </div>
            <div className="relative z-10 flex gap-6 items-start">
              <HiOutlineInformationCircle className="text-cloudo-accent w-6 h-6 shrink-0 mt-1" />
              <div className="space-y-4">
                <h3 className="text-[11px] font-black text-cloudo-text uppercase tracking-widest">
                  Operator Note
                </h3>
                <p className="text-[10px] text-cloudo-muted uppercase font-bold leading-relaxed max-w-2xl">
                  These parameters affect the global behavior of the ClouDO
                  engine. Changes are applied in real-time to all future runbook
                  executions and audit streams. Verify security hooks before
                  committing.
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
