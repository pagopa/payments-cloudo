"use client";

import { useState, useEffect } from "react";
import { cloudoFetch } from "@/lib/api";
import { useRouter } from "next/navigation";
import {
  HiOutlineSparkles,
  HiOutlineSave,
  HiOutlineRefresh,
  HiOutlineCheckCircle,
  HiOutlineExclamationCircle,
  HiOutlineInformationCircle,
  HiOutlineX,
  HiOutlineChip,
  HiOutlineKey,
  HiOutlineTerminal,
} from "react-icons/hi";

interface AgentSettings {
  AI_AGENT_ENABLED: string;
  AGENT_LLM_PROVIDER: string;
  AGENT_LLM_MODEL: string;
  AGENT_LLM_TIMEOUT_SECONDS: string;
  AGENT_MAX_LOG_CHARS: string;
  OPENAI_API_KEY: string;
  AZURE_OPENAI_API_KEY: string;
  AZURE_OPENAI_ENDPOINT: string;
  AZURE_OPENAI_API_VERSION: string;
  COPILOT_GITHUB_TOKEN: string;
  COPILOT_MODEL: string;
  JSM_API_KEY_DEFAULT: string;
}

const DEFAULT_SETTINGS: AgentSettings = {
  AI_AGENT_ENABLED: "false",
  AGENT_LLM_PROVIDER: "openai",
  AGENT_LLM_MODEL: "gpt-4o-mini",
  AGENT_LLM_TIMEOUT_SECONDS: "30",
  AGENT_MAX_LOG_CHARS: "8000",
  OPENAI_API_KEY: "",
  AZURE_OPENAI_API_KEY: "",
  AZURE_OPENAI_ENDPOINT: "",
  AZURE_OPENAI_API_VERSION: "2024-08-01-preview",
  COPILOT_GITHUB_TOKEN: "",
  COPILOT_MODEL: "",
  JSM_API_KEY_DEFAULT: "",
};

interface Notification {
  id: string;
  type: "success" | "error";
  message: string;
}

export default function AiAgentSettingsPage() {
  const router = useRouter();
  const [settings, setSettings] = useState<AgentSettings>(DEFAULT_SETTINGS);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [notifications, setNotifications] = useState<Notification[]>([]);

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
    const userData = localStorage.getItem("cloudo_user");
    if (userData) {
      try {
        const parsedUser = JSON.parse(userData);
        if (parsedUser.role !== "ADMIN") {
          router.push("/profile");
          return;
        }
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
        setSettings((prev) => ({
          ...prev,
          ...Object.fromEntries(
            Object.keys(DEFAULT_SETTINGS)
              .filter((key) => data[key] !== undefined && data[key] !== null)
              .map((key) => [key, data[key]]),
          ),
        }));
      }
    } catch {
      console.error("Failed to fetch AI agent settings");
    } finally {
      setLoading(false);
    }
  };

  const saveSettings = async () => {
    setSaving(true);
    try {
      const res = await cloudoFetch(`/settings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(settings),
      });

      if (res.ok) {
        addNotification(
          "success",
          "AI Agent configuration updated successfully",
        );
      } else {
        addNotification("error", "Failed to update AI Agent configuration");
      }
    } catch {
      addNotification("error", "Uplink failed");
    } finally {
      setSaving(false);
    }
  };

  const isAzure = settings.AGENT_LLM_PROVIDER === "azure_openai";
  const isCopilot = settings.AGENT_LLM_PROVIDER === "copilot_sdk";
  const isEnabled = settings.AI_AGENT_ENABLED === "true";

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
            <HiOutlineSparkles className="text-cloudo-accent w-5 h-5" />
          </div>
          <div>
            <h1 className="text-sm font-black tracking-[0.2em] text-cloudo-text uppercase">
              AI Agent Configuration
            </h1>
            <p className="text-[11px] text-cloudo-muted font-bold uppercase tracking-[0.3em] opacity-70">
              LLM Triage & JSM Notes // AI_AGENT_GATE
            </p>
          </div>
        </div>

        <div className="flex items-center gap-4">
          <button
            onClick={fetchSettings}
            className="btn btn-ghost h-10 px-4 flex items-center gap-2"
          >
            <HiOutlineRefresh
              className={`w-4 h-4 ${loading ? "animate-spin" : ""}`}
            />
            Sync
          </button>
          <button
            onClick={saveSettings}
            disabled={saving}
            className="btn btn-primary h-10 px-6 flex items-center gap-2"
          >
            <HiOutlineSave className="w-4 h-4" />
            {saving ? "Saving..." : "Commit Changes"}
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-auto p-8">
        <div className="max-w-4xl mx-auto space-y-12">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-8 pt-4">
            {/* Agent Activation */}
            <div className="space-y-6">
              <div className="flex items-center gap-3">
                <div className="w-1.5 h-4 bg-cloudo-accent" />
                <h2 className="text-sm font-black uppercase tracking-[0.4em] text-cloudo-text">
                  Agent Activation
                </h2>
              </div>

              <div className="bg-cloudo-panel border border-cloudo-border p-6 space-y-6">
                <div
                  className="flex items-center justify-between p-4 bg-cloudo-accent/10 border border-cloudo-border group hover:border-cloudo-accent/40 transition-all cursor-pointer"
                  onClick={() =>
                    setSettings({
                      ...settings,
                      AI_AGENT_ENABLED: isEnabled ? "false" : "true",
                    })
                  }
                >
                  <div className="space-y-1">
                    <p className="text-sm font-black text-cloudo-text uppercase tracking-widest">
                      Enable AI Triage
                    </p>
                    <p className="text-[11px] text-cloudo-muted uppercase font-bold opacity-70">
                      Analyze failed/errored runbook executions
                    </p>
                  </div>
                  <div
                    className={`w-5 h-5 border flex items-center justify-center transition-all ${
                      isEnabled
                        ? "bg-cloudo-accent border-cloudo-accent text-cloudo-dark"
                        : "border-cloudo-border"
                    }`}
                  >
                    {isEnabled && <HiOutlineCheckCircle className="w-4 h-4" />}
                  </div>
                </div>

                <div className="space-y-2">
                  <div className="flex items-center gap-2 mb-1">
                    <HiOutlineChip className="text-cloudo-muted w-4 h-4" />
                    <label className="text-[11px] font-black uppercase tracking-widest text-cloudo-muted block">
                      LLM Provider
                    </label>
                  </div>
                  <select
                    className="input h-11 w-full"
                    value={settings.AGENT_LLM_PROVIDER}
                    onChange={(e) =>
                      setSettings({
                        ...settings,
                        AGENT_LLM_PROVIDER: e.target.value,
                      })
                    }
                  >
                    <option value="openai">OpenAI</option>
                    <option value="azure_openai">Azure OpenAI</option>
                    <option value="copilot_sdk">GitHub Copilot</option>
                  </select>
                </div>

                <div className="space-y-2">
                  <label className="text-[11px] font-black uppercase tracking-widest text-cloudo-muted ml-1 block">
                    Model / Deployment Name
                  </label>
                  <input
                    type="text"
                    className="input h-11 w-full"
                    placeholder="gpt-4o-mini"
                    value={settings.AGENT_LLM_MODEL}
                    onChange={(e) =>
                      setSettings({
                        ...settings,
                        AGENT_LLM_MODEL: e.target.value,
                      })
                    }
                    disabled={isCopilot}
                  />
                  {isCopilot && (
                    <p className="text-[10px] text-cloudo-muted/70 uppercase tracking-tight ml-1">
                      Not used by GitHub Copilot — set the Copilot Model field
                      below instead
                    </p>
                  )}
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <label className="text-[11px] font-black uppercase tracking-widest text-cloudo-muted ml-1 block">
                      Timeout (Seconds)
                    </label>
                    <input
                      type="number"
                      className="input h-11 w-full"
                      value={settings.AGENT_LLM_TIMEOUT_SECONDS}
                      onChange={(e) =>
                        setSettings({
                          ...settings,
                          AGENT_LLM_TIMEOUT_SECONDS: e.target.value,
                        })
                      }
                    />
                  </div>
                  <div className="space-y-2">
                    <label className="text-[11px] font-black uppercase tracking-widest text-cloudo-muted ml-1 block">
                      Max Log Chars
                    </label>
                    <input
                      type="number"
                      className="input h-11 w-full"
                      value={settings.AGENT_MAX_LOG_CHARS}
                      onChange={(e) =>
                        setSettings({
                          ...settings,
                          AGENT_MAX_LOG_CHARS: e.target.value,
                        })
                      }
                    />
                  </div>
                </div>
              </div>
            </div>

            {/* Credentials Section */}
            <div className="space-y-6">
              <div className="flex items-center gap-3">
                <div className="w-1.5 h-4 bg-cloudo-warn" />
                <h2 className="text-sm font-black uppercase tracking-[0.4em] text-cloudo-text">
                  Credentials
                </h2>
              </div>

              <div className="bg-cloudo-panel border border-cloudo-border p-6 space-y-6">
                {!isAzure && !isCopilot && (
                  <div className="space-y-2">
                    <div className="flex items-center gap-2 mb-1">
                      <HiOutlineKey className="text-cloudo-warn w-4 h-4" />
                      <label className="text-[11px] font-black uppercase tracking-widest text-cloudo-muted block">
                        OpenAI API Key
                      </label>
                    </div>
                    <input
                      type="password"
                      className="input h-11 text-sm w-full"
                      placeholder="OPENAI_API_KEY"
                      value={settings.OPENAI_API_KEY}
                      onChange={(e) =>
                        setSettings({
                          ...settings,
                          OPENAI_API_KEY: e.target.value,
                        })
                      }
                    />
                  </div>
                )}

                {isAzure && (
                  <>
                    <div className="space-y-2">
                      <div className="flex items-center gap-2 mb-1">
                        <HiOutlineKey className="text-cloudo-warn w-4 h-4" />
                        <label className="text-[11px] font-black uppercase tracking-widest text-cloudo-muted block">
                          Azure OpenAI API Key
                        </label>
                      </div>
                      <input
                        type="password"
                        className="input h-11 text-sm w-full"
                        placeholder="AZURE_OPENAI_API_KEY"
                        value={settings.AZURE_OPENAI_API_KEY}
                        onChange={(e) =>
                          setSettings({
                            ...settings,
                            AZURE_OPENAI_API_KEY: e.target.value,
                          })
                        }
                      />
                    </div>
                    <div className="space-y-2">
                      <label className="text-[11px] font-black uppercase tracking-widest text-cloudo-muted ml-1 block">
                        Azure OpenAI Endpoint
                      </label>
                      <input
                        type="text"
                        className="input h-11 w-full"
                        placeholder="https://<resource>.openai.azure.com"
                        value={settings.AZURE_OPENAI_ENDPOINT}
                        onChange={(e) =>
                          setSettings({
                            ...settings,
                            AZURE_OPENAI_ENDPOINT: e.target.value,
                          })
                        }
                      />
                    </div>
                    <div className="space-y-2">
                      <label className="text-[11px] font-black uppercase tracking-widest text-cloudo-muted ml-1 block">
                        API Version
                      </label>
                      <input
                        type="text"
                        className="input h-11 w-full"
                        placeholder="2024-08-01-preview"
                        value={settings.AZURE_OPENAI_API_VERSION}
                        onChange={(e) =>
                          setSettings({
                            ...settings,
                            AZURE_OPENAI_API_VERSION: e.target.value,
                          })
                        }
                      />
                    </div>
                  </>
                )}

                {isCopilot && (
                  <>
                    <div className="space-y-2">
                      <div className="flex items-center gap-2 mb-1">
                        <HiOutlineKey className="text-cloudo-warn w-4 h-4" />
                        <label className="text-[11px] font-black uppercase tracking-widest text-cloudo-muted block">
                          Copilot GitHub Token
                        </label>
                      </div>
                      <input
                        type="password"
                        className="input h-11 text-sm w-full"
                        placeholder="COPILOT_GITHUB_TOKEN"
                        value={settings.COPILOT_GITHUB_TOKEN}
                        onChange={(e) =>
                          setSettings({
                            ...settings,
                            COPILOT_GITHUB_TOKEN: e.target.value,
                          })
                        }
                      />
                      <p className="text-[10px] text-cloudo-muted/70 uppercase tracking-tight ml-1">
                        Must be a fine-grained PAT with the &quot;Copilot
                        Requests&quot; permission — classic ghp_ tokens are
                        rejected
                      </p>
                    </div>
                    <div className="space-y-2">
                      <label className="text-[11px] font-black uppercase tracking-widest text-cloudo-muted ml-1 block">
                        Copilot Model (Optional)
                      </label>
                      <input
                        type="text"
                        className="input h-11 w-full"
                        placeholder="Leave empty for Copilot's default"
                        value={settings.COPILOT_MODEL}
                        onChange={(e) =>
                          setSettings({
                            ...settings,
                            COPILOT_MODEL: e.target.value,
                          })
                        }
                      />
                    </div>
                  </>
                )}

                <div className="space-y-2">
                  <div className="flex items-center gap-2 mb-1">
                    <HiOutlineKey className="text-cloudo-accent w-4 h-4" />
                    <label className="text-[11px] font-black uppercase tracking-widest text-cloudo-muted block">
                      JSM API Key (Default)
                    </label>
                  </div>
                  <input
                    type="password"
                    className="input h-11 text-sm w-full"
                    placeholder="JSM_API_KEY_DEFAULT"
                    value={settings.JSM_API_KEY_DEFAULT}
                    onChange={(e) =>
                      setSettings({
                        ...settings,
                        JSM_API_KEY_DEFAULT: e.target.value,
                      })
                    }
                  />
                  <p className="text-[10px] text-cloudo-muted/70 uppercase tracking-tight ml-1">
                    Shared with Smart Routing defaults — used to post triage
                    notes on JSM Ops alerts
                  </p>
                </div>
              </div>
            </div>
          </div>

          {/* Pipeline Information Panel */}
          <div className="bg-cloudo-accent/5 border border-cloudo-accent/20 p-8 relative overflow-hidden group">
            <div className="absolute top-0 right-0 p-4 opacity-40 group-hover:opacity-60 transition-opacity">
              <HiOutlineTerminal className="w-24 h-24" />
            </div>
            <div className="relative z-10 flex gap-6 items-start">
              <HiOutlineInformationCircle className="text-cloudo-accent w-6 h-6 shrink-0 mt-1" />
              <div className="space-y-4">
                <h3 className="text-[11px] font-black text-cloudo-text uppercase tracking-widest">
                  Operator Note
                </h3>
                <p className="text-[10px] text-cloudo-muted uppercase font-bold leading-relaxed max-w-2xl">
                  When enabled, every failed or errored runbook execution is
                  forwarded to the ClouDO Agent service, which calls the
                  configured LLM to produce a triage summary (probable root
                  cause, recommended actions) and posts it as an asynchronous
                  note on the matching JSM Ops alert — the on-call engineer gets
                  context without any manual step. Changes here are picked up by
                  the orchestrator and the agent within about a minute, no
                  redeploy required.
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
