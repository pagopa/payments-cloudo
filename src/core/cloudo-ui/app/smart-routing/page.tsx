"use client";

import { useState, useEffect } from "react";
import { cloudoFetch } from "@/lib/api";
import { useRouter } from "next/navigation";
import {
  HiOutlineShieldCheck,
  HiOutlineSave,
  HiOutlinePlus,
  HiOutlineTrash,
  HiOutlineArrowNarrowRight,
  HiOutlineChevronUp,
  HiOutlineChevronDown,
  HiOutlineX,
  HiOutlineUserGroup,
  HiOutlineAdjustments,
  HiOutlineTerminal,
  HiOutlineCheckCircle,
  HiOutlineExclamationCircle,
  HiCheck,
} from "react-icons/hi";
import { MdOutlineRouter } from "react-icons/md";
import { SmartRoutingConsole } from "./SmartRoutingConsole";

interface Rule {
  team?: string;
  when: {
    isAlert?: string;
    statusIn?: string[];
    severityMin?: string;
    severityMax?: string;
    resourceGroup?: string;
    namespace?: string;
    schemaName?: string;
    subscriptionId?: string;
    any?: string;
  };
  then: {
    type: "slack" | "jsm";
    team?: string;
    channel?: string;
    statusIn?: string[];
  }[];
}

interface TeamConfig {
  slack?: { channel: string; token?: string };
  jsm?: { team: string; apiKey?: string };
}

interface Notification {
  id: string;
  type: "success" | "error";
  message: string;
}

interface RoutingConfig {
  version: number;
  defaults: {
    jsm: { team: string; apiKey?: string };
    slack: { channel: string; token?: string };
  };
  teams: Record<string, TeamConfig>;
  rules: Rule[];
}

const POSSIBLE_STATUSES = [
  "succeeded",
  "failed",
  "error",
  "timeout",
  "routed",
  "scheduled",
  "skipped",
  "running",
];

const StatusSelector = ({
  selected,
  onChange,
}: {
  selected: string[];
  onChange: (val: string[]) => void;
}) => {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="w-full bg-cloudo-dark border border-cloudo-border px-3 py-1.5 text-xs outline-none flex items-center justify-between min-h-[32px]"
      >
        <div className="flex flex-wrap gap-1">
          {selected.length > 0 ? (
            selected.map((s) => (
              <span
                key={s}
                className="px-1.5 py-0.5 bg-cloudo-accent/20 text-cloudo-accent text-[9px] font-bold uppercase"
              >
                {s}
              </span>
            ))
          ) : (
            <span className="text-cloudo-muted italic">Select statuses...</span>
          )}
        </div>
        <HiOutlineChevronDown
          className={`w-3 h-3 text-cloudo-muted transition-transform ${
            isOpen ? "rotate-180" : ""
          }`}
        />
      </button>

      {isOpen && (
        <>
          <div
            className="fixed inset-0 z-[60]"
            onClick={() => setIsOpen(false)}
          />
          <div className="absolute top-full left-0 right-0 mt-1 bg-cloudo-panel border border-cloudo-border shadow-xl z-[70] max-h-48 overflow-y-auto animate-in fade-in slide-in-from-top-1 duration-200">
            {POSSIBLE_STATUSES.map((status) => (
              <label
                key={status}
                className="flex items-center gap-3 px-3 py-2 hover:bg-white/5 cursor-pointer group"
              >
                <div className="relative flex items-center justify-center shrink-0">
                  <input
                    type="checkbox"
                    checked={selected.includes(status)}
                    onChange={(e) => {
                      if (e.target.checked) {
                        onChange([...selected, status]);
                      } else {
                        onChange(selected.filter((s) => s !== status));
                      }
                    }}
                    className="peer appearance-none w-4 h-4 border border-cloudo-border bg-cloudo-panel checked:bg-cloudo-accent checked:border-cloudo-accent transition-all cursor-pointer"
                  />
                  <HiCheck className="w-3 h-3 text-cloudo-dark absolute opacity-0 peer-checked:opacity-100 transition-opacity pointer-events-none" />
                </div>
                <span
                  className={`text-[10px] font-bold uppercase tracking-wider transition-colors ${
                    selected.includes(status)
                      ? "text-cloudo-accent"
                      : "text-cloudo-muted group-hover:text-cloudo-text"
                  }`}
                >
                  {status}
                </span>
              </label>
            ))}
          </div>
        </>
      )}
    </div>
  );
};

function LegacySmartRoutingPage() {
  const router = useRouter();
  const [config, setConfig] = useState<RoutingConfig>({
    version: 1,
    defaults: {
      jsm: { team: "default", apiKey: "" },
      slack: { channel: "#cloudo-default", token: "" },
    },
    teams: {},
    rules: [],
  });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [expandedRules, setExpandedRules] = useState<number[]>([]);
  const [teamModal, setTeamModal] = useState<{
    isOpen: boolean;
    teamName?: string;
  } | null>(null);
  const [ruleModal, setRuleModal] = useState<{
    isOpen: boolean;
    ruleIndex?: number;
  } | null>(null);
  const [teamToDelete, setTeamToDelete] = useState<string | null>(null);
  const [availableTeams, setAvailableTeams] = useState<string[]>(["default"]);

  const addNotification = (type: "success" | "error", message: string) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    setNotifications((prev) => [...prev, { id, type, message }]);
    setTimeout(() => {
      setNotifications((prev) => prev.filter((n) => n.id !== id));
    }, 4000);
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
        fetchConfig();
        cloudoFetch("/teams")
          .then((res) => (res.ok ? res.json() : []))
          .then((teams) => {
            if (Array.isArray(teams)) {
              setAvailableTeams(
                teams.map((team: { id: string }) => team.id).filter(Boolean),
              );
            }
          })
          .catch(() => setAvailableTeams(["default"]));
      } catch {
        router.push("/login");
      }
    } else {
      router.push("/login");
    }
  }, [router]);

  const fetchConfig = async () => {
    setLoading(true);
    try {
      const res = await cloudoFetch(`/settings`);
      if (res.ok) {
        const settings = await res.json();
        if (settings.ROUTING_RULES) {
          try {
            const parsed: RoutingConfig = JSON.parse(settings.ROUTING_RULES);

            // Migrate legacy opsgenie key to jsm
            const legacy = (parsed.defaults as Record<string, unknown>)[
              "opsgenie"
            ] as { team?: string; apiKey?: string } | undefined;
            if (legacy && !parsed.defaults.jsm) {
              parsed.defaults.jsm = {
                team: legacy.team || "",
                apiKey: legacy.apiKey,
              };
            }
            parsed.defaults.jsm = parsed.defaults.jsm || { team: "" };

            // Enrich defaults with credentials and channels from settings
            if (
              settings.SLACK_TOKEN_DEFAULT !== undefined &&
              settings.SLACK_TOKEN_DEFAULT !== null
            ) {
              parsed.defaults.slack = {
                ...(parsed.defaults.slack || { channel: "" }),
                token: settings.SLACK_TOKEN_DEFAULT,
              };
            }
            if (
              settings.SLACK_CHANNEL_DEFAULT !== undefined &&
              settings.SLACK_CHANNEL_DEFAULT !== null
            ) {
              parsed.defaults.slack = {
                ...(parsed.defaults.slack || { channel: "" }),
                channel: settings.SLACK_CHANNEL_DEFAULT,
              };
            } else if (
              settings.SLACK_CHANNEL !== undefined &&
              settings.SLACK_CHANNEL !== null
            ) {
              parsed.defaults.slack = {
                ...(parsed.defaults.slack || { channel: "" }),
                channel: settings.SLACK_CHANNEL,
              };
            }

            if (
              settings.JSM_API_KEY_DEFAULT !== undefined &&
              settings.JSM_API_KEY_DEFAULT !== null
            ) {
              parsed.defaults.jsm = {
                ...(parsed.defaults.jsm || { team: "" }),
                apiKey: settings.JSM_API_KEY_DEFAULT,
              };
            }
            if (
              settings.JSM_TEAM_DEFAULT !== undefined &&
              settings.JSM_TEAM_DEFAULT !== null
            ) {
              parsed.defaults.jsm = {
                ...(parsed.defaults.jsm || { team: "" }),
                team: settings.JSM_TEAM_DEFAULT,
              };
            }

            // Migrate legacy opsgenie team configs to jsm
            if (parsed.teams) {
              for (const teamName of Object.keys(parsed.teams)) {
                const teamLegacy = (
                  parsed.teams[teamName] as Record<string, unknown>
                )["opsgenie"] as { team?: string; apiKey?: string } | undefined;
                if (teamLegacy && !parsed.teams[teamName].jsm) {
                  parsed.teams[teamName].jsm = {
                    team: teamLegacy.team || "",
                    apiKey: teamLegacy.apiKey,
                  };
                }
              }
            }

            // Enrich teams with credentials and channels from settings
            if (parsed.teams) {
              for (const teamName of Object.keys(parsed.teams)) {
                const teamUpper = teamName.toUpperCase().replace(/-/g, "_");
                const slackTokenKey = `SLACK_TOKEN_${teamUpper}`;
                const slackChannelKey = `SLACK_CHANNEL_${teamUpper}`;
                const jsmApiKeyKey = `JSM_API_KEY_${teamUpper}`;
                const jsmTeamKey = `JSM_TEAM_${teamUpper}`;

                if (
                  settings[slackTokenKey] !== undefined &&
                  settings[slackTokenKey] !== null
                ) {
                  parsed.teams[teamName].slack = {
                    ...(parsed.teams[teamName].slack || { channel: "" }),
                    token: settings[slackTokenKey],
                  };
                }
                if (
                  settings[slackChannelKey] !== undefined &&
                  settings[slackChannelKey] !== null
                ) {
                  parsed.teams[teamName].slack = {
                    ...(parsed.teams[teamName].slack || { channel: "" }),
                    channel: settings[slackChannelKey],
                  };
                }
                if (
                  settings[jsmApiKeyKey] !== undefined &&
                  settings[jsmApiKeyKey] !== null
                ) {
                  parsed.teams[teamName].jsm = {
                    ...(parsed.teams[teamName].jsm || { team: "" }),
                    apiKey: settings[jsmApiKeyKey],
                  };
                }
                if (
                  settings[jsmTeamKey] !== undefined &&
                  settings[jsmTeamKey] !== null
                ) {
                  parsed.teams[teamName].jsm = {
                    ...(parsed.teams[teamName].jsm || { team: "" }),
                    team: settings[jsmTeamKey],
                  };
                }
              }
            }

            setConfig(parsed);
          } catch (e) {
            console.error("Failed to parse ROUTING_RULES", e);
          }
        }
      }
    } catch (err) {
      console.error("Failed to fetch routing config", err);
    } finally {
      setLoading(false);
    }
  };

  const saveConfig = async () => {
    setSaving(true);
    try {
      // Prepare settings payload
      const settingsPayload: Record<string, string> = {};

      // We want to avoid saving tokens inside the ROUTING_RULES JSON string
      // So we clone the config and strip tokens before stringifying
      const configToSave = JSON.parse(JSON.stringify(config));

      // Handle default credentials and channels
      if (configToSave.defaults.slack) {
        if (configToSave.defaults.slack.token) {
          settingsPayload.SLACK_TOKEN_DEFAULT =
            configToSave.defaults.slack.token;
          delete configToSave.defaults.slack.token;
        }
        if (configToSave.defaults.slack.channel) {
          settingsPayload.SLACK_CHANNEL_DEFAULT =
            configToSave.defaults.slack.channel;
          settingsPayload.SLACK_CHANNEL = configToSave.defaults.slack.channel;
        }
      }
      if (configToSave.defaults.jsm) {
        if (configToSave.defaults.jsm.apiKey) {
          settingsPayload.JSM_API_KEY_DEFAULT =
            configToSave.defaults.jsm.apiKey;
          delete configToSave.defaults.jsm.apiKey;
        }
        if (configToSave.defaults.jsm.team) {
          settingsPayload.JSM_TEAM_DEFAULT = configToSave.defaults.jsm.team;
        }
      }

      // Clean up empty fields in rules to avoid backend matching issues
      if (configToSave.rules && Array.isArray(configToSave.rules)) {
        configToSave.rules.forEach((rule: Rule) => {
          if (rule.when) {
            Object.keys(rule.when).forEach((key) => {
              const val = rule.when[key as keyof typeof rule.when];

              // Special handling for arrays: filter out empty strings (e.g., from trailing commas in UI)
              if (Array.isArray(val)) {
                const cleanedVal = val
                  .map((s: unknown) => (typeof s === "string" ? s.trim() : s))
                  .filter((s) => s !== "") as string[];
                (rule.when as Record<string, unknown>)[key] = cleanedVal;
              }

              if (
                val === "" ||
                val === null ||
                val === undefined ||
                (Array.isArray(val) && val.length === 0)
              ) {
                delete (rule.when as Record<string, unknown>)[key];
              }
            });
          }
        });
      }

      if (configToSave.teams) {
        for (const teamName of Object.keys(configToSave.teams)) {
          const team = configToSave.teams[teamName];
          const teamUpper = teamName.toUpperCase().replace(/-/g, "_");
          const slackTokenKey = `SLACK_TOKEN_${teamUpper}`;
          const slackChannelKey = `SLACK_CHANNEL_${teamUpper}`;
          const jsmApiKeyKey = `JSM_API_KEY_${teamUpper}`;
          const jsmTeamKey = `JSM_TEAM_${teamUpper}`;

          if (team.slack) {
            if (team.slack.token) {
              settingsPayload[slackTokenKey] = team.slack.token;
              delete team.slack.token;
            }
            if (team.slack.channel) {
              settingsPayload[slackChannelKey] = team.slack.channel;
            }
          }
          if (team.jsm) {
            if (team.jsm.apiKey) {
              settingsPayload[jsmApiKeyKey] = team.jsm.apiKey;
              delete team.jsm.apiKey;
            }
            if (team.jsm.team) {
              settingsPayload[jsmTeamKey] = team.jsm.team;
            }
          }
        }
      }

      settingsPayload.ROUTING_RULES = JSON.stringify(configToSave);

      const res = await cloudoFetch(`/settings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(settingsPayload),
      });
      if (res.ok) {
        addNotification(
          "success",
          "Configuration and credentials saved successfully",
        );
      } else {
        addNotification("error", "Failed to save configuration");
      }
    } catch {
      addNotification("error", "Error saving configuration");
    } finally {
      setSaving(false);
    }
  };

  const addAction = (ruleIndex: number) => {
    setConfig((prev) => {
      const newRules = [...prev.rules];
      const newThen = [...newRules[ruleIndex].then, { type: "slack" as const }];
      newRules[ruleIndex] = { ...newRules[ruleIndex], then: newThen };
      return { ...prev, rules: newRules };
    });
  };

  const updateAction = (
    ruleIndex: number,
    actionIndex: number,
    field: string,
    value: unknown,
  ) => {
    setConfig((prev) => {
      const newRules = [...prev.rules];
      const newThen = [...newRules[ruleIndex].then];
      newThen[actionIndex] = { ...newThen[actionIndex], [field]: value };
      newRules[ruleIndex] = { ...newRules[ruleIndex], then: newThen };
      return { ...prev, rules: newRules };
    });
  };

  const removeAction = (ruleIndex: number, actionIndex: number) => {
    setConfig((prev) => {
      const newRules = [...prev.rules];
      newRules[ruleIndex].then = newRules[ruleIndex].then.filter(
        (_, i) => i !== actionIndex,
      );
      return { ...prev, rules: newRules };
    });
  };

  const toggleRule = (index: number) => {
    setExpandedRules((prev) =>
      prev.includes(index) ? prev.filter((i) => i !== index) : [...prev, index],
    );
  };

  const addRule = () => {
    setRuleModal({ isOpen: true });
  };

  const saveRuleModal = (rule: Rule, index?: number) => {
    setConfig((prev) => {
      const newRules = [...prev.rules];
      if (index !== undefined) {
        newRules[index] = rule;
      } else {
        newRules.push(rule);
        // Expand the newly added rule
        setExpandedRules((prevExp) => [...prevExp, newRules.length - 1]);
      }
      return { ...prev, rules: newRules };
    });
    setRuleModal(null);
  };

  const removeRule = (index: number) => {
    setConfig((prev) => ({
      ...prev,
      rules: prev.rules.filter((_, i) => i !== index),
    }));
    addNotification(
      "success",
      `Rule #${index + 1} removed locally (COMMIT to save)`,
    );
  };

  const moveRuleUp = (index: number) => {
    if (index === 0) return;
    setConfig((prev) => {
      const newRules = [...prev.rules];
      [newRules[index - 1], newRules[index]] = [
        newRules[index],
        newRules[index - 1],
      ];
      return { ...prev, rules: newRules };
    });
    setExpandedRules((prev) =>
      prev.map((i) => {
        if (i === index) return index - 1;
        if (i === index - 1) return index;
        return i;
      }),
    );
  };

  const moveRuleDown = (index: number) => {
    if (index === config.rules.length - 1) return;
    setConfig((prev) => {
      const newRules = [...prev.rules];
      [newRules[index + 1], newRules[index]] = [
        newRules[index + 1],
        newRules[index],
      ];
      return { ...prev, rules: newRules };
    });
    setExpandedRules((prev) =>
      prev.map((i) => {
        if (i === index) return index + 1;
        if (i === index + 1) return index;
        return i;
      }),
    );
  };

  const updateRuleWhen = (ruleIndex: number, field: string, value: unknown) => {
    setConfig((prev) => {
      const newRules = [...prev.rules];
      newRules[ruleIndex] = {
        ...newRules[ruleIndex],
        when: { ...newRules[ruleIndex].when, [field]: value },
      };
      return { ...prev, rules: newRules };
    });
  };

  const addTeam = () => {
    setTeamModal({ isOpen: true });
  };

  const saveTeamModal = (name: string, data: TeamConfig) => {
    if (!name.trim()) return;
    setConfig((prev) => ({
      ...prev,
      teams: {
        ...prev.teams,
        [name.trim()]: data,
      },
    }));
    setTeamModal(null);
  };

  const removeTeam = (teamName: string) => {
    setConfig((prev) => {
      const newTeams = { ...prev.teams };
      delete newTeams[teamName];
      return { ...prev, teams: newTeams };
    });
    setTeamToDelete(null);
    addNotification(
      "success",
      `Team "${teamName}" removed locally (COMMIT to save)`,
    );
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full bg-cloudo-dark text-cloudo-muted font-mono">
        LOADING_ROUTING_ENGINE...
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full bg-cloudo-dark text-cloudo-text font-mono">
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
              onClick={() =>
                setNotifications((prev) =>
                  prev.filter((notif) => notif.id !== n.id),
                )
              }
              className="p-1 hover:bg-white/5 transition-colors opacity-40 hover:opacity-100"
            >
              <HiOutlineX className="w-3.5 h-3.5" />
            </button>
          </div>
        ))}
      </div>

      {/* Header */}
      <div className="flex items-center justify-between px-8 py-6 border-b border-cloudo-border bg-cloudo-panel sticky top-0 z-20">
        <div className="flex items-center gap-4">
          <div className="p-2 bg-cloudo-accent/10 border border-cloudo-accent/20">
            <MdOutlineRouter className="text-cloudo-accent w-6 h-6" />
          </div>
          <div>
            <h1 className="text-lg font-black tracking-[0.2em] uppercase">
              Smart Routing
            </h1>
            <p className="text-[10px] text-cloudo-muted font-bold uppercase tracking-[0.3em] opacity-70">
              Rule-based Notification Engine // Configuration
            </p>
          </div>
        </div>
        <button
          onClick={saveConfig}
          disabled={saving}
          className="flex items-center gap-2 px-6 py-2 bg-cloudo-accent text-cloudo-dark font-black uppercase text-xs tracking-widest hover:bg-white transition-all disabled:opacity-50"
        >
          <HiOutlineSave className="w-4 h-4" />
          {saving ? "SAVING..." : "COMMIT_CHANGES"}
        </button>
      </div>

      <div className="flex-1 overflow-auto p-8 max-w-5xl mx-auto w-full space-y-10">
        {/* Defaults Section */}
        <section className="space-y-4">
          <div className="flex items-center gap-3">
            <div className="w-1.5 h-4 bg-cloudo-accent" />
            <h2 className="text-sm font-black uppercase tracking-[0.3em]">
              Global Defaults
            </h2>
          </div>
          <div className="grid grid-cols-2 gap-6 bg-cloudo-panel border border-cloudo-border p-6">
            <div className="space-y-4">
              <div className="space-y-2">
                <label className="text-[10px] font-black text-cloudo-muted uppercase tracking-widest">
                  Default Slack Channel
                </label>
                <input
                  type="text"
                  value={config.defaults.slack.channel}
                  onChange={(e) =>
                    setConfig((prev) => ({
                      ...prev,
                      defaults: {
                        ...prev.defaults,
                        slack: {
                          ...prev.defaults.slack,
                          channel: e.target.value,
                        },
                      },
                    }))
                  }
                  className="w-full bg-cloudo-dark border border-cloudo-border px-4 py-2 text-sm focus:border-cloudo-accent outline-none transition-colors"
                />
              </div>
              <div className="space-y-2">
                <label className="text-[10px] font-black text-cloudo-muted uppercase tracking-widest">
                  Default Slack Token
                </label>
                <input
                  type="password"
                  placeholder="SLACK_TOKEN_DEFAULT"
                  value={config.defaults.slack.token || ""}
                  onChange={(e) =>
                    setConfig((prev) => ({
                      ...prev,
                      defaults: {
                        ...prev.defaults,
                        slack: {
                          ...prev.defaults.slack,
                          token: e.target.value,
                        },
                      },
                    }))
                  }
                  className="w-full bg-cloudo-dark border border-cloudo-border px-4 py-2 text-sm focus:border-cloudo-accent outline-none transition-colors"
                />
              </div>
            </div>
            <div className="space-y-4">
              <div className="space-y-2">
                <label className="text-[10px] font-black text-cloudo-muted uppercase tracking-widest">
                  Default JSM Team
                </label>
                <input
                  type="text"
                  value={config.defaults.jsm.team}
                  onChange={(e) =>
                    setConfig((prev) => ({
                      ...prev,
                      defaults: {
                        ...prev.defaults,
                        jsm: {
                          ...prev.defaults.jsm,
                          team: e.target.value,
                        },
                      },
                    }))
                  }
                  className="w-full bg-cloudo-dark border border-cloudo-border px-4 py-2 text-sm focus:border-cloudo-accent outline-none transition-colors"
                />
              </div>
              <div className="space-y-2">
                <label className="text-[10px] font-black text-cloudo-muted uppercase tracking-widest">
                  Default JSM API Key
                </label>
                <input
                  type="password"
                  placeholder="JSM_API_KEY_DEFAULT"
                  value={config.defaults.jsm.apiKey || ""}
                  onChange={(e) =>
                    setConfig((prev) => ({
                      ...prev,
                      defaults: {
                        ...prev.defaults,
                        jsm: {
                          ...prev.defaults.jsm,
                          apiKey: e.target.value,
                        },
                      },
                    }))
                  }
                  className="w-full bg-cloudo-dark border border-cloudo-border px-4 py-2 text-sm focus:border-cloudo-accent outline-none transition-colors"
                />
              </div>
            </div>
          </div>
        </section>

        {/* Teams Section */}
        <section className="space-y-6">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-1.5 h-4 bg-cloudo-accent" />
              <h2 className="text-sm font-black uppercase tracking-[0.3em]">
                Team Definitions
              </h2>
            </div>
            <button
              onClick={addTeam}
              className="flex items-center gap-2 px-4 py-2 border border-cloudo-accent text-cloudo-accent text-[10px] font-black uppercase tracking-widest hover:bg-cloudo-accent hover:text-cloudo-dark transition-all"
            >
              <HiOutlinePlus /> Add Team
            </button>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {Object.keys(config.teams).length === 0 && (
              <div className="md:col-span-2 py-10 border border-dashed border-cloudo-border text-center text-[10px] uppercase font-bold text-cloudo-muted">
                No teams defined. Rules can reference these for easy updates.
              </div>
            )}
            {Object.entries(config.teams).map(([name, teamCfg]) => (
              <div
                key={name}
                className="bg-cloudo-panel border border-cloudo-border p-5 space-y-4 relative group cursor-pointer hover:border-cloudo-accent/30 transition-all"
                onClick={() => setTeamModal({ isOpen: true, teamName: name })}
              >
                <div
                  className="absolute top-4 right-4 flex items-center gap-2"
                  onClick={(e) => e.stopPropagation()}
                >
                  <button
                    onClick={() => setTeamToDelete(name)}
                    className="text-cloudo-muted hover:text-cloudo-err transition-colors"
                  >
                    <HiOutlineTrash className="w-3.5 h-3.5" />
                  </button>
                </div>
                <div className="flex items-center gap-2 mb-2">
                  <div className="p-1.5 bg-cloudo-accent/10 border border-cloudo-accent/20">
                    <HiOutlineUserGroup className="text-cloudo-accent w-3.5 h-3.5" />
                  </div>
                  <span className="text-xs font-black text-cloudo-text uppercase tracking-widest">
                    {name}
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-4 opacity-70">
                  <div className="space-y-1">
                    <p className="text-[8px] font-black text-cloudo-muted uppercase tracking-widest">
                      Slack
                    </p>
                    <p className="text-[10px] font-bold text-cloudo-text truncate">
                      {teamCfg.slack?.channel || "NOT_SET"}
                    </p>
                  </div>
                  <div className="space-y-1">
                    <p className="text-[8px] font-black text-cloudo-muted uppercase tracking-widest">
                      JSM
                    </p>
                    <p className="text-[10px] font-bold text-cloudo-text truncate">
                      {teamCfg.jsm?.team || "NOT_SET"}
                    </p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* Rules Section */}
        <section className="space-y-6">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-1.5 h-4 bg-cloudo-accent" />
              <h2 className="text-sm font-black uppercase tracking-[0.3em]">
                Routing Rules
              </h2>
            </div>
            <button
              onClick={addRule}
              className="flex items-center gap-2 px-4 py-2 border border-cloudo-accent text-cloudo-accent text-[10px] font-black uppercase tracking-widest hover:bg-cloudo-accent hover:text-cloudo-dark transition-all"
            >
              <HiOutlinePlus /> Add Rule
            </button>
          </div>

          <div className="space-y-4">
            {config.rules.map((rule, rIdx) => {
              const isExpanded = expandedRules.includes(rIdx);
              const whenKeys = Object.keys(rule.when).filter(
                (k) => rule.when[k as keyof typeof rule.when],
              );

              return (
                <div
                  key={rIdx}
                  className="bg-cloudo-panel border border-cloudo-border relative group"
                >
                  {/* Collapsed Header / Expand Toggle */}
                  <div
                    className="p-4 flex items-center justify-between cursor-pointer hover:bg-white/[0.02] transition-colors"
                    onClick={() => toggleRule(rIdx)}
                  >
                    <div className="flex items-center gap-4 min-w-0">
                      <div className="flex items-center gap-2 text-cloudo-accent font-black text-[10px] uppercase tracking-widest shrink-0">
                        <span className="opacity-40">#{rIdx + 1}</span>
                        {isExpanded ? (
                          <HiOutlineChevronUp className="w-3 h-3" />
                        ) : (
                          <HiOutlineChevronDown className="w-3 h-3" />
                        )}
                      </div>

                      <div className="flex flex-wrap gap-2 overflow-hidden">
                        {rule.when.any === "*" ? (
                          <span className="px-2 py-0.5 bg-cloudo-accent/10 border border-cloudo-accent/30 text-cloudo-accent text-[9px] font-black uppercase tracking-tighter">
                            CATCH_ALL: *
                          </span>
                        ) : whenKeys.length > 0 ? (
                          whenKeys.map((key) => (
                            <span
                              key={key}
                              className="px-2 py-0.5 bg-cloudo-border/30 border border-cloudo-border text-cloudo-muted text-[9px] font-bold uppercase tracking-tighter"
                            >
                              {key}:{" "}
                              {Array.isArray(
                                rule.when[key as keyof typeof rule.when],
                              )
                                ? (
                                    rule.when[
                                      key as keyof typeof rule.when
                                    ] as string[]
                                  ).join(",")
                                : rule.when[key as keyof typeof rule.when]}
                            </span>
                          ))
                        ) : (
                          <span className="text-[9px] text-cloudo-muted italic opacity-40">
                            No conditions defined
                          </span>
                        )}

                        {rule.team && (
                          <span className="px-2 py-0.5 bg-cloudo-warn/10 border border-cloudo-warn/30 text-cloudo-warn text-[9px] font-black uppercase tracking-tighter">
                            TEAM: {rule.team}
                          </span>
                        )}

                        <HiOutlineArrowNarrowRight className="w-3 h-3 text-cloudo-muted/40 mx-1" />

                        {rule.then.map((action, aIdx) => (
                          <span
                            key={aIdx}
                            className="px-2 py-0.5 bg-cloudo-ok/10 border border-cloudo-ok/30 text-cloudo-ok text-[9px] font-black uppercase tracking-tighter"
                          >
                            {action.type} {action.team && `→ ${action.team}`}
                          </span>
                        ))}
                      </div>
                    </div>

                    <div
                      className="flex items-center gap-2 shrink-0 ml-4"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <button
                        onClick={() =>
                          setRuleModal({ isOpen: true, ruleIndex: rIdx })
                        }
                        className="text-cloudo-muted hover:text-cloudo-accent transition-colors p-1"
                        title="Edit Rule"
                      >
                        <HiOutlineAdjustments className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => moveRuleUp(rIdx)}
                        disabled={rIdx === 0}
                        className="text-cloudo-muted hover:text-cloudo-accent transition-colors disabled:opacity-20 p-1"
                        title="Move Up"
                      >
                        <HiOutlineChevronUp className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => moveRuleDown(rIdx)}
                        disabled={rIdx === config.rules.length - 1}
                        className="text-cloudo-muted hover:text-cloudo-accent transition-colors disabled:opacity-20 p-1"
                        title="Move Down"
                      >
                        <HiOutlineChevronDown className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => removeRule(rIdx)}
                        className="text-cloudo-muted hover:text-cloudo-err transition-colors p-1"
                        title="Remove Rule"
                      >
                        <HiOutlineTrash className="w-4 h-4" />
                      </button>
                    </div>
                  </div>

                  {isExpanded && (
                    <div className="animate-in slide-in-from-top-2 duration-200">
                      <div className="p-6 border-t border-cloudo-border bg-white/[0.01]">
                        <p className="text-[10px] font-black text-cloudo-accent uppercase tracking-[0.2em] mb-4 flex items-center gap-2">
                          <HiOutlineShieldCheck className="w-4 h-4" /> Rule
                          Condition (WHEN)
                        </p>
                        <div className="grid grid-cols-3 gap-6">
                          <div className="space-y-2 col-span-1">
                            <label className="text-[9px] font-black text-cloudo-muted uppercase tracking-widest">
                              Status In
                            </label>
                            <StatusSelector
                              selected={rule.when.statusIn || []}
                              onChange={(list) =>
                                updateRuleWhen(rIdx, "statusIn", list)
                              }
                            />
                          </div>
                          <div className="space-y-2">
                            <label className="text-[9px] font-black text-cloudo-muted uppercase tracking-widest">
                              Resource Group / Prefix
                            </label>
                            <input
                              type="text"
                              placeholder="rg-name"
                              value={rule.when.resourceGroup || ""}
                              onChange={(e) =>
                                updateRuleWhen(
                                  rIdx,
                                  "resourceGroup",
                                  e.target.value,
                                )
                              }
                              className="w-full bg-cloudo-dark border border-cloudo-border px-3 py-1.5 text-xs outline-none"
                            />
                          </div>
                          <div className="space-y-2">
                            <label className="text-[9px] font-black text-cloudo-muted uppercase tracking-widest">
                              Severity Range
                            </label>
                            <div className="flex items-center gap-2">
                              <input
                                type="text"
                                placeholder="Min"
                                value={rule.when.severityMin || ""}
                                onChange={(e) =>
                                  updateRuleWhen(
                                    rIdx,
                                    "severityMin",
                                    e.target.value,
                                  )
                                }
                                className="w-full bg-cloudo-dark border border-cloudo-border px-3 py-1.5 text-xs outline-none"
                              />
                              <span className="text-cloudo-muted">-</span>
                              <input
                                type="text"
                                placeholder="Max"
                                value={rule.when.severityMax || ""}
                                onChange={(e) =>
                                  updateRuleWhen(
                                    rIdx,
                                    "severityMax",
                                    e.target.value,
                                  )
                                }
                                className="w-full bg-cloudo-dark border border-cloudo-border px-3 py-1.5 text-xs outline-none"
                              />
                            </div>
                          </div>

                          <div className="space-y-2">
                            <label className="text-[9px] font-black text-cloudo-muted uppercase tracking-widest">
                              Namespace
                            </label>
                            <input
                              type="text"
                              placeholder="kube-system"
                              value={rule.when.namespace || ""}
                              onChange={(e) =>
                                updateRuleWhen(
                                  rIdx,
                                  "namespace",
                                  e.target.value,
                                )
                              }
                              className="w-full bg-cloudo-dark border border-cloudo-border px-3 py-1.5 text-xs outline-none"
                            />
                          </div>
                          <div className="space-y-2">
                            <label className="text-[9px] font-black text-cloudo-muted uppercase tracking-widest">
                              Schema Name
                            </label>
                            <input
                              type="text"
                              placeholder="Example schema name"
                              value={rule.when.schemaName || ""}
                              onChange={(e) =>
                                updateRuleWhen(
                                  rIdx,
                                  "schemaName",
                                  e.target.value,
                                )
                              }
                              className="w-full bg-cloudo-dark border border-cloudo-border px-3 py-1.5 text-xs outline-none"
                            />
                          </div>
                          <div className="space-y-2">
                            <label className="text-[9px] font-black text-cloudo-muted uppercase tracking-widest">
                              Subscription ID
                            </label>
                            <input
                              type="text"
                              placeholder="0000..."
                              value={rule.when.subscriptionId || ""}
                              onChange={(e) =>
                                updateRuleWhen(
                                  rIdx,
                                  "subscriptionId",
                                  e.target.value,
                                )
                              }
                              className="w-full bg-cloudo-dark border border-cloudo-border px-3 py-1.5 text-xs outline-none"
                            />
                          </div>
                          <div className="space-y-2">
                            <label className="text-[9px] font-black text-cloudo-muted uppercase tracking-widest">
                              Wildcard Match
                            </label>
                            <input
                              type="text"
                              placeholder="* for any"
                              value={rule.when.any || ""}
                              onChange={(e) =>
                                updateRuleWhen(rIdx, "any", e.target.value)
                              }
                              className="w-full bg-cloudo-dark border border-cloudo-border px-3 py-1.5 text-xs outline-none"
                            />
                          </div>
                          <div className="space-y-2">
                            <label className="text-[9px] font-black text-cloudo-muted uppercase tracking-widest">
                              Is Alert
                            </label>
                            <select
                              value={rule.when.isAlert || ""}
                              onChange={(e) =>
                                updateRuleWhen(rIdx, "isAlert", e.target.value)
                              }
                              className="w-full bg-cloudo-dark border border-cloudo-border px-3 py-1.5 text-xs outline-none"
                            >
                              <option value="">Any</option>
                              <option value="true">True</option>
                              <option value="false">False</option>
                            </select>
                          </div>
                        </div>
                      </div>

                      <div className="p-6 space-y-4">
                        <p className="text-[10px] font-black text-cloudo-ok uppercase tracking-[0.2em] flex items-center gap-2">
                          <HiOutlineArrowNarrowRight className="w-4 h-4" />{" "}
                          Trigger Actions (THEN)
                        </p>

                        {rule.then.map((action, aIdx) => (
                          <div
                            key={aIdx}
                            className="flex items-end gap-4 p-4 border border-cloudo-border bg-cloudo-dark/30"
                          >
                            <div className="space-y-2 flex-1">
                              <label className="text-[9px] font-black text-cloudo-muted uppercase tracking-widest">
                                Type
                              </label>
                              <select
                                value={action.type}
                                onChange={(e) =>
                                  updateAction(
                                    rIdx,
                                    aIdx,
                                    "type",
                                    e.target.value,
                                  )
                                }
                                className="w-full bg-cloudo-dark border border-cloudo-border px-3 py-1.5 text-xs outline-none"
                              >
                                <option value="slack">SLACK</option>
                                <option value="jsm">JSM</option>
                              </select>
                            </div>
                            <div className="space-y-2 flex-1">
                              <label className="text-[9px] font-black text-cloudo-muted uppercase tracking-widest">
                                Linked Team
                              </label>
                              <select
                                value={action.team || ""}
                                onChange={(e) =>
                                  updateAction(
                                    rIdx,
                                    aIdx,
                                    "team",
                                    e.target.value,
                                  )
                                }
                                className="w-full bg-cloudo-dark border border-cloudo-border px-3 py-1.5 text-xs outline-none"
                              >
                                <option value="">None (Use Default)</option>
                                {Array.from(
                                  new Set([
                                    "default",
                                    ...availableTeams,
                                    ...Object.keys(config.teams),
                                  ]),
                                ).map((t) => (
                                  <option key={t} value={t}>
                                    {t}
                                  </option>
                                ))}
                              </select>
                            </div>
                            <div className="space-y-2 flex-1">
                              <label className="text-[9px] font-black text-cloudo-muted uppercase tracking-widest">
                                {action.type === "slack"
                                  ? "Override Channel"
                                  : "Override Team"}
                              </label>
                              <input
                                type="text"
                                placeholder="Optional override"
                                value={
                                  (action.type === "slack"
                                    ? action.channel
                                    : action.team) || ""
                                }
                                onChange={(e) =>
                                  updateAction(
                                    rIdx,
                                    aIdx,
                                    action.type === "slack"
                                      ? "channel"
                                      : "team",
                                    e.target.value,
                                  )
                                }
                                className="w-full bg-cloudo-dark border border-cloudo-border px-3 py-1.5 text-xs outline-none"
                              />
                            </div>
                            <button
                              onClick={() => removeAction(rIdx, aIdx)}
                              className="p-2 text-cloudo-muted hover:text-cloudo-err transition-colors"
                            >
                              <HiOutlineTrash className="w-4 h-4" />
                            </button>
                          </div>
                        ))}

                        <button
                          onClick={() => addAction(rIdx)}
                          className="w-full py-2 border border-dashed border-cloudo-border text-[9px] font-black text-cloudo-muted uppercase tracking-widest hover:border-cloudo-accent hover:text-cloudo-accent transition-all"
                        >
                          + Add Action
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </section>
      </div>

      {ruleModal?.isOpen && (
        <RuleModal
          ruleIndex={ruleModal.ruleIndex}
          config={config}
          teamOptions={Array.from(
            new Set([
              "default",
              ...availableTeams,
              ...Object.keys(config.teams),
            ]),
          )}
          onClose={() => setRuleModal(null)}
          onSave={saveRuleModal}
        />
      )}

      {teamModal?.isOpen && (
        <TeamModal
          teamName={teamModal.teamName}
          config={config}
          onClose={() => setTeamModal(null)}
          onSave={saveTeamModal}
        />
      )}

      {teamToDelete && (
        <div className="fixed inset-0 bg-cloudo-dark/95 backdrop-blur-md flex items-center justify-center z-[60] p-4">
          <div className="bg-cloudo-panel border border-cloudo-err/30 max-w-sm w-full p-10 text-center space-y-8 animate-in zoom-in-95 relative overflow-hidden">
            <div className="absolute top-0 left-0 w-full h-1 bg-cloudo-err/50" />

            <div className="w-14 h-14 bg-cloudo-err/10 border border-cloudo-err/20 flex items-center justify-center mx-auto text-cloudo-err">
              <HiOutlineTrash className="w-7 h-7" />
            </div>

            <div className="space-y-3">
              <h3 className="text-[11px] font-black text-cloudo-text uppercase tracking-[0.3em]">
                Destructive Action
              </h3>
              <p className="text-[9px] text-cloudo-muted uppercase font-bold leading-relaxed">
                Permanently delete team definition:
                <br />
                <span className="text-cloudo-err mt-2 block font-mono">
                  &quot;{teamToDelete}&quot;
                </span>
              </p>
            </div>

            <div className="flex flex-col gap-3 pt-4">
              <button
                onClick={() => removeTeam(teamToDelete)}
                className="w-full bg-cloudo-err text-cloudo-text py-4 text-[10px] font-black uppercase tracking-[0.3em] hover:bg-cloudo-err/90 transition-all"
              >
                Confirm Destruction
              </button>
              <button
                onClick={() => setTeamToDelete(null)}
                className="text-[9px] font-black text-cloudo-muted hover:text-cloudo-text uppercase tracking-widest py-2 transition-all"
              >
                Cancel Action
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default function SmartRoutingPage() {
  return <SmartRoutingConsole />;
}

function RuleModal({
  ruleIndex,
  config,
  teamOptions,
  onClose,
  onSave,
}: {
  ruleIndex?: number;
  config: RoutingConfig;
  teamOptions: string[];
  onClose: () => void;
  onSave: (rule: Rule, index?: number) => void;
}) {
  const [rule, setRule] = useState<Rule>(
    ruleIndex !== undefined
      ? JSON.parse(JSON.stringify(config.rules[ruleIndex]))
      : {
          team: "",
          when: { statusIn: ["failed", "error"] },
          then: [{ type: "slack" }],
        },
  );

  const updateRuleWhen = (field: string, value: unknown) => {
    setRule((prev) => ({
      ...prev,
      when: { ...prev.when, [field]: value },
    }));
  };

  const addAction = () => {
    setRule((prev) => ({
      ...prev,
      then: [...prev.then, { type: "slack" }],
    }));
  };

  const updateAction = (actionIndex: number, field: string, value: unknown) => {
    setRule((prev) => {
      const newThen = [...prev.then];
      newThen[actionIndex] = { ...newThen[actionIndex], [field]: value };
      return { ...prev, then: newThen };
    });
  };

  const removeAction = (actionIndex: number) => {
    setRule((prev) => ({
      ...prev,
      then: prev.then.filter((_, i) => i !== actionIndex),
    }));
  };

  return (
    <div
      className="fixed inset-0 bg-cloudo-dark/90 backdrop-blur-sm flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <div
        className="bg-cloudo-panel border border-cloudo-border shadow-2xl w-full max-w-3xl max-h-[90vh] flex flex-col animate-in zoom-in-95 duration-200"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-6 py-4 border-b border-cloudo-border flex justify-between items-center bg-cloudo-accent/5">
          <div className="flex items-center gap-3">
            <HiOutlineAdjustments className="text-cloudo-accent w-4 h-4" />
            <h3 className="text-[10px] font-black uppercase tracking-[0.3em] text-cloudo-text">
              {ruleIndex !== undefined
                ? `Edit Rule #${ruleIndex + 1}`
                : "Create New Routing Rule"}
            </h3>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 hover:bg-cloudo-err hover:text-cloudo-text border border-cloudo-border text-cloudo-muted transition-colors"
          >
            <HiOutlineX className="w-4 h-4" />
          </button>
        </div>

        <div className="p-6 overflow-y-auto space-y-8 custom-scrollbar">
          {/* WHEN section */}
          <div className="space-y-4">
            <p className="text-[10px] font-black text-cloudo-accent uppercase tracking-[0.2em] flex items-center gap-2">
              <HiOutlineShieldCheck className="w-4 h-4" /> Rule Condition (WHEN)
            </p>
            <div className="grid grid-cols-3 gap-6">
              <div className="space-y-2">
                <label className="text-[9px] font-black text-cloudo-muted uppercase tracking-widest">
                  Scope Team
                </label>
                <select
                  value={rule.team || ""}
                  onChange={(e) =>
                    setRule((prev) => ({
                      ...prev,
                      team: e.target.value || undefined,
                    }))
                  }
                  className="w-full bg-cloudo-dark border border-cloudo-border px-4 py-2 text-xs outline-none focus:border-cloudo-accent"
                >
                  <option value="">All teams</option>
                  {teamOptions.map((team) => (
                    <option key={team} value={team}>
                      {team}
                    </option>
                  ))}
                </select>
              </div>
              <div className="space-y-2 col-span-1">
                <label className="text-[9px] font-black text-cloudo-muted uppercase tracking-widest">
                  Status In
                </label>
                <StatusSelector
                  selected={rule.when.statusIn || []}
                  onChange={(list) => updateRuleWhen("statusIn", list)}
                />
              </div>
              <div className="space-y-2">
                <label className="text-[9px] font-black text-cloudo-muted uppercase tracking-widest">
                  Severity Min
                </label>
                <select
                  value={rule.when.severityMin || ""}
                  onChange={(e) =>
                    updateRuleWhen("severityMin", e.target.value)
                  }
                  className="w-full bg-cloudo-dark border border-cloudo-border px-4 py-2 text-xs outline-none focus:border-cloudo-accent"
                >
                  <option value="">Any</option>
                  <option value="Sev0">Sev0 (Critical)</option>
                  <option value="Sev1">Sev1 (Error)</option>
                  <option value="Sev2">Sev2 (Warning)</option>
                  <option value="Sev3">Sev3 (Info)</option>
                  <option value="Sev4">Sev4 (Verbose)</option>
                </select>
              </div>
              <div className="space-y-2">
                <label className="text-[9px] font-black text-cloudo-muted uppercase tracking-widest">
                  Severity Max
                </label>
                <select
                  value={rule.when.severityMax || ""}
                  onChange={(e) =>
                    updateRuleWhen("severityMax", e.target.value)
                  }
                  className="w-full bg-cloudo-dark border border-cloudo-border px-4 py-2 text-xs outline-none focus:border-cloudo-accent"
                >
                  <option value="">Any</option>
                  <option value="Sev0">Sev0 (Critical)</option>
                  <option value="Sev1">Sev1 (Error)</option>
                  <option value="Sev2">Sev2 (Warning)</option>
                  <option value="Sev3">Sev3 (Info)</option>
                  <option value="Sev4">Sev4 (Verbose)</option>
                </select>
              </div>
              <div className="space-y-2">
                <label className="text-[9px] font-black text-cloudo-muted uppercase tracking-widest">
                  Resource Group
                </label>
                <input
                  type="text"
                  placeholder="rg-name"
                  value={rule.when.resourceGroup || ""}
                  onChange={(e) =>
                    updateRuleWhen("resourceGroup", e.target.value)
                  }
                  className="w-full bg-cloudo-dark border border-cloudo-border px-4 py-2 text-xs outline-none focus:border-cloudo-accent"
                />
              </div>
              <div className="space-y-2">
                <label className="text-[9px] font-black text-cloudo-muted uppercase tracking-widest">
                  Namespace (K8s)
                </label>
                <input
                  type="text"
                  placeholder="kube-system"
                  value={rule.when.namespace || ""}
                  onChange={(e) => updateRuleWhen("namespace", e.target.value)}
                  className="w-full bg-cloudo-dark border border-cloudo-border px-4 py-2 text-xs outline-none focus:border-cloudo-accent"
                />
              </div>
              <div className="space-y-2">
                <label className="text-[9px] font-black text-cloudo-muted uppercase tracking-widest">
                  Alert Rule Name
                </label>
                <input
                  type="text"
                  placeholder="Costi-OverBudget"
                  value={rule.when.schemaName || ""}
                  onChange={(e) => updateRuleWhen("schemaName", e.target.value)}
                  className="w-full bg-cloudo-dark border border-cloudo-border px-4 py-2 text-xs outline-none focus:border-cloudo-accent"
                />
              </div>
              <div className="space-y-2">
                <label className="text-[9px] font-black text-cloudo-muted uppercase tracking-widest">
                  Subscription ID
                </label>
                <input
                  type="text"
                  placeholder="00000000-..."
                  value={rule.when.subscriptionId || ""}
                  onChange={(e) =>
                    updateRuleWhen("subscriptionId", e.target.value)
                  }
                  className="w-full bg-cloudo-dark border border-cloudo-border px-4 py-2 text-xs outline-none focus:border-cloudo-accent"
                />
              </div>
              <div className="space-y-2">
                <label className="text-[9px] font-black text-cloudo-muted uppercase tracking-widest">
                  Is Alert (bool)
                </label>
                <select
                  value={rule.when.isAlert || ""}
                  onChange={(e) => updateRuleWhen("isAlert", e.target.value)}
                  className="w-full bg-cloudo-dark border border-cloudo-border px-4 py-2 text-xs outline-none focus:border-cloudo-accent"
                >
                  <option value="">Any</option>
                  <option value="true">True</option>
                  <option value="false">False</option>
                </select>
              </div>
              <div className="space-y-2">
                <label className="text-[9px] font-black text-cloudo-muted uppercase tracking-widest">
                  Catch-all (Any: &quot;*&quot;)
                </label>
                <input
                  type="text"
                  placeholder="*"
                  value={rule.when.any || ""}
                  onChange={(e) => updateRuleWhen("any", e.target.value)}
                  className="w-full bg-cloudo-dark border border-cloudo-border px-4 py-2 text-xs outline-none focus:border-cloudo-accent"
                />
              </div>
            </div>
          </div>

          {/* THEN section */}
          <div className="space-y-4">
            <p className="text-[10px] font-black text-cloudo-ok uppercase tracking-[0.2em] flex items-center gap-2">
              <HiOutlineTerminal className="w-4 h-4" /> Execution Actions (THEN)
            </p>

            <div className="grid grid-cols-1 gap-4">
              {rule.then.map((action, aIdx) => (
                <div
                  key={aIdx}
                  className="bg-cloudo-dark/50 border border-cloudo-border p-4 relative group"
                >
                  <button
                    onClick={() => removeAction(aIdx)}
                    className="absolute top-4 right-4 text-cloudo-muted hover:text-cloudo-err transition-colors"
                  >
                    <HiOutlineTrash className="w-3.5 h-3.5" />
                  </button>
                  <div className="grid grid-cols-3 gap-6">
                    <div className="space-y-2">
                      <label className="text-[9px] font-black text-cloudo-muted uppercase tracking-widest">
                        Action Type
                      </label>
                      <select
                        value={action.type}
                        onChange={(e) =>
                          updateAction(aIdx, "type", e.target.value)
                        }
                        className="w-full bg-cloudo-dark border border-cloudo-border px-3 py-1.5 text-xs outline-none focus:border-cloudo-accent"
                      >
                        <option value="slack">Slack</option>
                        <option value="jsm">JSM</option>
                      </select>
                    </div>
                    <div className="space-y-2">
                      <label className="text-[9px] font-black text-cloudo-muted uppercase tracking-widest">
                        Target Team
                      </label>
                      <select
                        value={action.team || ""}
                        onChange={(e) =>
                          updateAction(aIdx, "team", e.target.value)
                        }
                        className="w-full bg-cloudo-dark border border-cloudo-border px-3 py-1.5 text-xs outline-none focus:border-cloudo-accent"
                      >
                        <option value="">Default</option>
                        {teamOptions.map((t) => (
                          <option key={t} value={t}>
                            {t}
                          </option>
                        ))}
                      </select>
                    </div>
                    {action.type === "slack" && (
                      <div className="space-y-2">
                        <label className="text-[9px] font-black text-cloudo-muted uppercase tracking-widest">
                          Channel Override
                        </label>
                        <input
                          type="text"
                          placeholder="#channel"
                          value={action.channel || ""}
                          onChange={(e) =>
                            updateAction(aIdx, "channel", e.target.value)
                          }
                          className="w-full bg-cloudo-dark border border-cloudo-border px-3 py-1.5 text-xs outline-none focus:border-cloudo-accent"
                        />
                      </div>
                    )}
                  </div>
                </div>
              ))}

              <button
                onClick={addAction}
                className="w-full py-2 border border-dashed border-cloudo-border text-[9px] font-black text-cloudo-muted uppercase tracking-widest hover:border-cloudo-accent hover:text-cloudo-accent transition-all"
              >
                + Add Action
              </button>
            </div>
          </div>
        </div>

        <div className="px-6 py-4 border-t border-cloudo-border flex justify-end gap-3 bg-cloudo-accent/5">
          <button
            onClick={onClose}
            className="px-4 py-2 text-[10px] font-black text-cloudo-muted uppercase tracking-widest hover:text-cloudo-text transition-colors"
          >
            Discard
          </button>
          <button
            onClick={() => onSave(rule, ruleIndex)}
            className="px-6 py-2 bg-cloudo-accent text-cloudo-dark text-[10px] font-black uppercase tracking-widest hover:bg-white transition-all"
          >
            Save Rule
          </button>
        </div>
      </div>
    </div>
  );
}

function TeamModal({
  teamName,
  config,
  onClose,
  onSave,
}: {
  teamName?: string;
  config: RoutingConfig;
  onClose: () => void;
  onSave: (name: string, data: TeamConfig) => void;
}) {
  const [name, setName] = useState(teamName || "");
  const [data, setData] = useState<TeamConfig>(
    teamName
      ? JSON.parse(JSON.stringify(config.teams[teamName]))
      : {
          slack: { channel: "" },
          jsm: { team: "" },
        },
  );

  return (
    <div
      className="fixed inset-0 bg-cloudo-dark/90 backdrop-blur-sm flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <div
        className="bg-cloudo-panel border border-cloudo-border shadow-2xl w-full max-w-lg animate-in zoom-in-95 duration-200"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-6 py-4 border-b border-cloudo-border flex justify-between items-center bg-cloudo-accent/5">
          <div className="flex items-center gap-3">
            <HiOutlineUserGroup className="text-cloudo-accent w-4 h-4" />
            <h3 className="text-[10px] font-black uppercase tracking-[0.3em] text-cloudo-text">
              {teamName ? `Edit Team: ${teamName}` : "Register New Team"}
            </h3>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 hover:bg-cloudo-err hover:text-cloudo-text border border-cloudo-border text-cloudo-muted transition-colors"
          >
            <HiOutlineX className="w-4 h-4" />
          </button>
        </div>

        <div className="p-6 space-y-6">
          <div className="space-y-2">
            <label className="text-[10px] font-black text-cloudo-muted uppercase tracking-widest">
              Team Identifier
            </label>
            <input
              type="text"
              disabled={!!teamName}
              placeholder="e.g. payments, platform"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full bg-cloudo-dark border border-cloudo-border px-4 py-2 text-sm focus:border-cloudo-accent outline-none transition-colors disabled:opacity-50"
            />
          </div>

          <div className="grid grid-cols-2 gap-6">
            <div className="space-y-4">
              <p className="text-[9px] font-black text-cloudo-accent uppercase tracking-widest border-b border-cloudo-accent/20 pb-1">
                Slack Integration
              </p>
              <div className="space-y-2">
                <label className="text-[8px] font-black text-cloudo-muted uppercase tracking-widest">
                  Channel
                </label>
                <input
                  type="text"
                  placeholder="#channel"
                  value={data.slack?.channel || ""}
                  onChange={(e) =>
                    setData({
                      ...data,
                      slack: {
                        ...(data.slack || { channel: "" }),
                        channel: e.target.value,
                      },
                    })
                  }
                  className="w-full bg-cloudo-dark border border-cloudo-border px-3 py-1.5 text-xs outline-none focus:border-cloudo-accent"
                />
              </div>
              <div className="space-y-2">
                <label className="text-[8px] font-black text-cloudo-muted uppercase tracking-widest">
                  Token (Optional)
                </label>
                <input
                  type="password"
                  placeholder="xoxb-..."
                  value={data.slack?.token || ""}
                  onChange={(e) =>
                    setData({
                      ...data,
                      slack: {
                        ...(data.slack || { channel: "" }),
                        token: e.target.value,
                      },
                    })
                  }
                  className="w-full bg-cloudo-dark border border-cloudo-border px-3 py-1.5 text-xs outline-none focus:border-cloudo-accent"
                />
              </div>
            </div>

            <div className="space-y-4">
              <p className="text-[9px] font-black text-cloudo-accent uppercase tracking-widest border-b border-cloudo-accent/20 pb-1">
                JSM Integration
              </p>
              <div className="space-y-2">
                <label className="text-[8px] font-black text-cloudo-muted uppercase tracking-widest">
                  Team Name
                </label>
                <input
                  type="text"
                  placeholder="team-name"
                  value={data.jsm?.team || ""}
                  onChange={(e) =>
                    setData({
                      ...data,
                      jsm: {
                        ...(data.jsm || { team: "" }),
                        team: e.target.value,
                      },
                    })
                  }
                  className="w-full bg-cloudo-dark border border-cloudo-border px-3 py-1.5 text-xs outline-none focus:border-cloudo-accent"
                />
              </div>
              <div className="space-y-2">
                <label className="text-[8px] font-black text-cloudo-muted uppercase tracking-widest">
                  API Key (Optional)
                </label>
                <input
                  type="password"
                  placeholder="jsm-api-key"
                  value={data.jsm?.apiKey || ""}
                  onChange={(e) =>
                    setData({
                      ...data,
                      jsm: {
                        ...(data.jsm || { team: "" }),
                        apiKey: e.target.value,
                      },
                    })
                  }
                  className="w-full bg-cloudo-dark border border-cloudo-border px-3 py-1.5 text-xs outline-none focus:border-cloudo-accent"
                />
              </div>
            </div>
          </div>
        </div>

        <div className="px-6 py-4 border-t border-cloudo-border flex justify-end gap-3 bg-cloudo-accent/5">
          <button
            onClick={onClose}
            className="px-4 py-2 text-[10px] font-black text-cloudo-muted uppercase tracking-widest hover:text-cloudo-text transition-colors"
          >
            Discard
          </button>
          <button
            onClick={() => onSave(name, data)}
            disabled={!name.trim()}
            className="px-6 py-2 bg-cloudo-accent text-cloudo-dark text-[10px] font-black uppercase tracking-widest hover:bg-white transition-all disabled:opacity-50"
          >
            Save Team
          </button>
        </div>
      </div>
    </div>
  );
}
