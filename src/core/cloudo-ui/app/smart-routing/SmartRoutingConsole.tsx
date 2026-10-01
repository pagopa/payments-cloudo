"use client";

import { useEffect, useMemo, useState } from "react";
import { cloudoFetch } from "@/lib/api";
import {
  HiOutlineAdjustments,
  HiOutlineCheckCircle,
  HiOutlineChevronRight,
  HiOutlinePlus,
  HiOutlineSave,
  HiOutlineTrash,
  HiOutlineUsers,
  HiOutlineX,
} from "react-icons/hi";
import { MdOutlineRouter } from "react-icons/md";

type ActionType = "slack" | "jsm";

interface Action {
  type: ActionType;
  team?: string;
  channel?: string;
}

interface Rule {
  team?: string;
  when: {
    statusIn?: string[];
    severityMin?: string;
    severityMax?: string;
    resourceGroup?: string;
    namespace?: string;
    schemaName?: string;
    isAlert?: string;
    any?: string;
  };
  then: Action[];
}

interface TeamConfig {
  slack?: { channel: string; token?: string };
  jsm?: { team: string; apiKey?: string };
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

interface ManagedTeam {
  id: string;
  name: string;
  enabled: boolean;
  shared?: boolean;
}

const statuses = [
  "failed",
  "error",
  "routed",
  "timeout",
  "succeeded",
  "scheduled",
  "skipped",
  "running",
];

const emptyRule = (team: string): Rule => ({
  team: team === "shared" ? undefined : team,
  when: { statusIn: ["failed", "error"] },
  then: [{ type: "slack", team: team === "shared" ? "default" : team }],
});

function emptyConfig(): RoutingConfig {
  return {
    version: 2,
    defaults: {
      jsm: { team: "default", apiKey: "" },
      slack: { channel: "#cloudo-default", token: "" },
    },
    teams: {},
    rules: [],
  };
}

export function SmartRoutingConsole() {
  const [currentUser, setCurrentUser] = useState<{
    role: string;
    team?: string;
  } | null>(null);
  const [config, setConfig] = useState<RoutingConfig>(emptyConfig);
  const [managedTeams, setManagedTeams] = useState<ManagedTeam[]>([]);
  const [selectedTeam, setSelectedTeam] = useState("default");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [selectedRule, setSelectedRule] = useState<number | null>(null);
  const isAdmin = currentUser?.role === "ADMIN";
  const currentUserTeam = currentUser?.team || "default";
  const canEdit =
    isAdmin ||
    (currentUser?.role === "OPERATOR" && currentUserTeam !== "default");
  const canEditScope =
    canEdit &&
    selectedTeam !== "all" &&
    (isAdmin || selectedTeam === currentUserTeam);

  const teamOptions = useMemo(
    () =>
      Array.from(new Set(["default", ...managedTeams.map((team) => team.id)])),
    [managedTeams],
  );
  const visibleRules = useMemo(
    () =>
      config.rules
        .map((rule, index) => ({ rule, index }))
        .filter(
          ({ rule }) => selectedTeam === "all" || rule.team === selectedTeam,
        ),
    [config.rules, selectedTeam],
  );
  const selectedConfig =
    selectedTeam === "all"
      ? {}
      : selectedTeam === "default"
        ? config.defaults
        : config.teams[selectedTeam] || {};

  useEffect(() => {
    const load = async () => {
      try {
        const [settingsResponse, teamsResponse] = await Promise.all([
          cloudoFetch("/settings"),
          cloudoFetch("/teams?for=routing"),
        ]);
        const settings = settingsResponse.ok
          ? await settingsResponse.json()
          : {};
        const teams = teamsResponse.ok ? await teamsResponse.json() : [];
        const storedUser = localStorage.getItem("cloudo_user");
        const user = storedUser ? JSON.parse(storedUser) : null;
        setCurrentUser(user);
        setSelectedTeam(
          user?.role === "ADMIN"
            ? user?.team || "default"
            : user?.team || "default",
        );
        const parsed = settings.ROUTING_RULES
          ? (JSON.parse(settings.ROUTING_RULES) as RoutingConfig)
          : emptyConfig();
        parsed.defaults = parsed.defaults || emptyConfig().defaults;
        parsed.teams = parsed.teams || {};
        parsed.rules = Array.isArray(parsed.rules) ? parsed.rules : [];
        parsed.defaults.slack = {
          ...(parsed.defaults.slack || { channel: "" }),
          token: settings.SLACK_TOKEN_DEFAULT || "",
          channel:
            settings.SLACK_CHANNEL_DEFAULT ||
            settings.SLACK_CHANNEL ||
            parsed.defaults.slack?.channel ||
            "",
        };
        parsed.defaults.jsm = {
          ...(parsed.defaults.jsm || { team: "" }),
          apiKey: settings.JSM_API_KEY_DEFAULT || "",
          team: settings.JSM_TEAM_DEFAULT || parsed.defaults.jsm?.team || "",
        };
        Object.entries(parsed.teams).forEach(([teamName, teamConfig]) => {
          const key = teamName.toUpperCase().replace(/-/g, "_");
          parsed.teams[teamName] = {
            ...teamConfig,
            slack: {
              ...(teamConfig.slack || { channel: "" }),
              token:
                settings[`SLACK_TOKEN_${key}`] || teamConfig.slack?.token || "",
              channel:
                teamConfig.slack?.channel ||
                settings[`SLACK_CHANNEL_${key}`] ||
                "",
            },
            jsm: {
              ...(teamConfig.jsm || { team: "" }),
              apiKey:
                settings[`JSM_API_KEY_${key}`] || teamConfig.jsm?.apiKey || "",
              team: settings[`JSM_TEAM_${key}`] || teamConfig.jsm?.team || "",
            },
          };
        });
        setConfig(parsed);
        setManagedTeams(
          Array.isArray(teams)
            ? teams.filter((team: ManagedTeam) => team.enabled)
            : [],
        );
      } catch {
        setNotice("Unable to load smart-routing configuration");
      } finally {
        setLoading(false);
      }
    };
    void load();
  }, []);

  const updateSelectedConfig = (patch: Partial<TeamConfig>) => {
    if (!canEditScope) return;
    if (selectedTeam === "shared" || selectedTeam === "default") {
      setConfig((current) => ({
        ...current,
        defaults: {
          ...current.defaults,
          ...patch,
        } as RoutingConfig["defaults"],
      }));
      return;
    }
    setConfig((current) => ({
      ...current,
      teams: {
        ...current.teams,
        [selectedTeam]: { ...(current.teams[selectedTeam] || {}), ...patch },
      },
    }));
  };

  const updateRule = (index: number, patch: Partial<Rule>) => {
    setConfig((current) => {
      const rules = [...current.rules];
      rules[index] = { ...rules[index], ...patch };
      return { ...current, rules };
    });
  };

  const addRule = () => {
    if (!canEditScope) return;
    setSelectedRule(config.rules.length);
    setConfig((current) => ({
      ...current,
      rules: [...current.rules, emptyRule(selectedTeam)],
    }));
  };

  const removeRule = (index: number) => {
    setSelectedRule(null);
    setConfig((current) => ({
      ...current,
      rules: current.rules.filter((_, ruleIndex) => ruleIndex !== index),
    }));
  };

  const addAction = (index: number) => {
    const rule = config.rules[index];
    updateRule(index, {
      then: [...rule.then, { type: "slack", team: selectedTeam }],
    });
  };

  const updateAction = (
    ruleIndex: number,
    actionIndex: number,
    patch: Partial<Action>,
  ) => {
    const actions = [...config.rules[ruleIndex].then];
    actions[actionIndex] = { ...actions[actionIndex], ...patch };
    updateRule(ruleIndex, { then: actions });
  };

  const removeAction = (ruleIndex: number, actionIndex: number) => {
    updateRule(ruleIndex, {
      then: config.rules[ruleIndex].then.filter(
        (_, index) => index !== actionIndex,
      ),
    });
  };

  const save = async () => {
    if (!canEdit) return;
    setSaving(true);
    try {
      const sanitizedConfig = JSON.parse(
        JSON.stringify(config),
      ) as RoutingConfig;
      delete sanitizedConfig.defaults.slack.token;
      delete sanitizedConfig.defaults.jsm.apiKey;
      Object.values(sanitizedConfig.teams).forEach((team) => {
        delete team.slack?.token;
        delete team.jsm?.apiKey;
      });
      const settingsPayload: Record<string, string> = {};
      if (config.defaults.slack?.token) {
        settingsPayload.SLACK_TOKEN_DEFAULT = config.defaults.slack.token;
      }
      if (config.defaults.jsm?.apiKey) {
        settingsPayload.JSM_API_KEY_DEFAULT = config.defaults.jsm.apiKey;
      }
      if (config.defaults.slack?.channel !== undefined) {
        settingsPayload.SLACK_CHANNEL_DEFAULT = config.defaults.slack.channel;
      }
      Object.entries(config.teams).forEach(([teamName, team]) => {
        const key = teamName.toUpperCase().replace(/-/g, "_");
        // Keep the SLACK_CHANNEL_<TEAM> setting aligned, or it would shadow the edit on reload.
        if (team.slack?.channel !== undefined)
          settingsPayload[`SLACK_CHANNEL_${key}`] = team.slack.channel;
        if (team.slack?.token)
          settingsPayload[`SLACK_TOKEN_${key}`] = team.slack.token;
        if (team.jsm?.apiKey)
          settingsPayload[`JSM_API_KEY_${key}`] = team.jsm.apiKey;
      });
      const response = await cloudoFetch("/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...settingsPayload,
          ROUTING_RULES: JSON.stringify(sanitizedConfig),
        }),
      });
      setNotice(
        response.ok ? "Smart-routing configuration saved" : "Save failed",
      );
    } catch {
      setNotice("Save failed");
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center bg-cloudo-dark text-cloudo-muted font-mono">
        LOADING_ROUTING_CONSOLE...
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full bg-cloudo-dark text-cloudo-text font-mono">
      <header className="flex items-center justify-between gap-6 px-8 py-5 border-b border-cloudo-border bg-cloudo-panel">
        <div className="flex items-center gap-4">
          <div className="p-2 border border-cloudo-accent/30 bg-cloudo-accent/10">
            <MdOutlineRouter className="w-6 h-6 text-cloudo-accent" />
          </div>
          <div>
            <h1 className="text-sm font-black uppercase tracking-[0.25em]">
              Notification Routing
            </h1>
            <p className="text-[10px] text-cloudo-muted uppercase tracking-[0.3em]">
              Team policy // multi-match fan-out
            </p>
          </div>
        </div>
        {canEdit && (
          <button
            onClick={save}
            disabled={saving}
            className="btn btn-primary h-10 px-5 flex items-center gap-2"
          >
            <HiOutlineSave className="w-4 h-4" />
            {saving ? "Saving..." : "Commit"}
          </button>
        )}
      </header>

      {notice && (
        <div className="px-8 py-3 border-b border-cloudo-accent/20 text-[10px] uppercase tracking-widest text-cloudo-accent">
          {notice}
        </div>
      )}

      <main className="flex-1 overflow-auto p-6 lg:p-8">
        <div className="grid grid-cols-1 xl:grid-cols-[240px_minmax(0,1fr)_280px] gap-6 max-w-[1500px] mx-auto">
          <aside className="border border-cloudo-border bg-cloudo-panel h-fit">
            <div className="p-4 border-b border-cloudo-border flex items-center gap-2">
              <HiOutlineUsers className="text-cloudo-accent" />
              <span className="text-[10px] font-black uppercase tracking-widest">
                Routing Scope
              </span>
            </div>
            <div className="p-2 space-y-1">
              {isAdmin && (
                <button
                  onClick={() => setSelectedTeam("all")}
                  className={`w-full flex items-center justify-between px-3 py-3 text-left text-[10px] uppercase tracking-widest border ${
                    selectedTeam === "all"
                      ? "border-cloudo-accent text-cloudo-accent bg-cloudo-accent/10"
                      : "border-transparent text-cloudo-muted hover:text-cloudo-text"
                  }`}
                >
                  All teams <HiOutlineChevronRight />
                </button>
              )}
              <button
                onClick={() => setSelectedTeam(currentUserTeam)}
                className={`w-full flex items-center justify-between px-3 py-3 text-left text-[10px] uppercase tracking-widest border ${
                  selectedTeam === currentUserTeam
                    ? "border-cloudo-accent text-cloudo-accent bg-cloudo-accent/10"
                    : "border-transparent text-cloudo-muted hover:text-cloudo-text"
                }`}
              >
                My team: {currentUserTeam} <HiOutlineChevronRight />
              </button>
              {isAdmin && currentUserTeam !== "default" && (
                <button
                  onClick={() => setSelectedTeam("default")}
                  className={`w-full flex items-center justify-between px-3 py-3 text-left text-[10px] uppercase tracking-widest border ${
                    selectedTeam === "default"
                      ? "border-cloudo-accent text-cloudo-accent bg-cloudo-accent/10"
                      : "border-transparent text-cloudo-muted hover:text-cloudo-text"
                  }`}
                >
                  Default (shared) <HiOutlineChevronRight />
                </button>
              )}
              {isAdmin &&
                managedTeams
                  .filter((team) => !team.shared && team.id !== "default")
                  .map((team) => (
                    <button
                      key={team.id}
                      onClick={() => setSelectedTeam(team.id)}
                      className={`w-full flex items-center justify-between px-3 py-3 text-left text-[10px] uppercase tracking-widest border ${
                        selectedTeam === team.id
                          ? "border-cloudo-accent text-cloudo-accent bg-cloudo-accent/10"
                          : "border-transparent text-cloudo-muted hover:text-cloudo-text"
                      }`}
                    >
                      {team.name || team.id}
                      <HiOutlineChevronRight />
                    </button>
                  ))}
            </div>
          </aside>

          <section className="space-y-5">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-[10px] text-cloudo-muted uppercase tracking-widest">
                  Active scope
                </p>
                <h2 className="text-lg font-black uppercase tracking-widest">
                  {selectedTeam === "all" ? "All teams" : selectedTeam}
                </h2>
              </div>
              {canEditScope && (
                <button
                  onClick={addRule}
                  className="btn btn-ghost h-9 px-3 flex items-center gap-2 text-[10px] uppercase"
                >
                  <HiOutlinePlus /> Rule
                </button>
              )}
            </div>
            <div className="border border-cloudo-border bg-cloudo-panel p-5 grid grid-cols-1 md:grid-cols-2 gap-4">
              <label className="text-[10px] uppercase tracking-widest text-cloudo-muted min-w-0">
                Slack channel
                <input
                  disabled={!canEditScope}
                  value={selectedConfig.slack?.channel || ""}
                  onChange={(event) =>
                    updateSelectedConfig({
                      slack: {
                        ...(selectedConfig.slack || { channel: "" }),
                        channel: event.target.value,
                      },
                    })
                  }
                  className="input w-full h-11 mt-2 disabled:opacity-50"
                  placeholder="#team-alerts"
                />
              </label>
              <label className="text-[10px] uppercase tracking-widest text-cloudo-muted min-w-0">
                Slack token
                <input
                  disabled={!canEditScope}
                  type="password"
                  value={selectedConfig.slack?.token || ""}
                  onChange={(event) =>
                    updateSelectedConfig({
                      slack: {
                        ...(selectedConfig.slack || { channel: "" }),
                        token: event.target.value,
                      },
                    })
                  }
                  className="input w-full h-11 mt-2 disabled:opacity-50"
                  placeholder="xoxb-..."
                />
              </label>
              <label className="text-[10px] uppercase tracking-widest text-cloudo-muted min-w-0">
                JSM API key
                <input
                  disabled={!canEditScope}
                  type="password"
                  value={selectedConfig.jsm?.apiKey || ""}
                  onChange={(event) =>
                    updateSelectedConfig({
                      jsm: {
                        ...(selectedConfig.jsm || { team: "" }),
                        apiKey: event.target.value,
                      },
                    })
                  }
                  className="input w-full h-11 mt-2 disabled:opacity-50"
                  placeholder="api-key"
                />
              </label>
            </div>
            <div className="space-y-4">
              {visibleRules.map(({ rule, index }) => (
                <RuleEditorV2
                  key={index}
                  rule={rule}
                  index={index}
                  selected={selectedRule === index}
                  onToggle={() =>
                    setSelectedRule((current) =>
                      current === index ? null : index,
                    )
                  }
                  teamOptions={teamOptions}
                  lockTeam={!isAdmin}
                  readOnly={!canEditScope}
                  onChange={(patch) => updateRule(index, patch)}
                  onAddAction={() => addAction(index)}
                  onUpdateAction={(actionIndex, patch) =>
                    updateAction(index, actionIndex, patch)
                  }
                  onRemoveAction={(actionIndex) =>
                    removeAction(index, actionIndex)
                  }
                  onRemove={() => removeRule(index)}
                />
              ))}
              {visibleRules.length === 0 && (
                <div className="border border-dashed border-cloudo-border py-16 text-center text-[10px] text-cloudo-muted uppercase tracking-widest">
                  No rules for this scope
                </div>
              )}
            </div>
          </section>

          <aside className="space-y-4 h-fit">
            <div className="border border-cloudo-border bg-cloudo-panel p-5">
              <div className="flex items-center gap-2 text-cloudo-accent mb-4">
                <HiOutlineAdjustments />
                <span className="text-[10px] font-black uppercase tracking-widest">
                  Resolution model
                </span>
              </div>
              <p className="text-xs text-cloudo-muted leading-relaxed">
                Every matching rule is evaluated. All distinct Slack and JSM
                actions are sent, so one event can notify multiple teams.
              </p>
            </div>
            <div className="border border-cloudo-border bg-cloudo-panel p-5">
              <div className="flex items-center gap-2 text-cloudo-ok mb-4">
                <HiOutlineCheckCircle />
                <span className="text-[10px] font-black uppercase tracking-widest">
                  Current routing
                </span>
              </div>
              <div className="text-3xl font-black text-cloudo-text">
                {visibleRules.length}
              </div>
              <p className="text-[10px] text-cloudo-muted uppercase tracking-widest mt-1">
                rules in scope
              </p>
            </div>
          </aside>
        </div>
      </main>
    </div>
  );
}

function StatusMultiSelect({
  selected,
  onChange,
  disabled,
}: {
  selected: string[];
  onChange: (value: string[]) => void;
  disabled: boolean;
}) {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <div className="relative">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setIsOpen((open) => !open)}
        className="input min-w-0 h-11 mt-1 flex items-center justify-between gap-2 text-left disabled:opacity-50"
      >
        <span className="truncate normal-case">
          {selected.length > 0 ? selected.join(", ") : "Any status"}
        </span>
        <HiOutlineChevronRight
          className={`w-3 h-3 shrink-0 transition-transform ${
            isOpen ? "rotate-90" : ""
          }`}
        />
      </button>
      {isOpen && !disabled && (
        <>
          <div
            className="fixed inset-0 z-10"
            onClick={() => setIsOpen(false)}
          />
          <div className="absolute left-0 right-0 mt-1 z-20 bg-cloudo-panel border border-cloudo-border shadow-xl max-h-48 overflow-y-auto">
            {statuses.map((status) => (
              <label
                key={status}
                className="flex items-center gap-2 px-3 py-2 text-xs normal-case text-cloudo-text hover:bg-cloudo-accent/10 cursor-pointer"
              >
                <input
                  type="checkbox"
                  checked={selected.includes(status)}
                  onChange={(event) =>
                    onChange(
                      event.target.checked
                        ? [...selected, status]
                        : selected.filter((value) => value !== status),
                    )
                  }
                />
                {status}
              </label>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function summarizeRule(rule: Rule) {
  const when = rule.when;
  const conditions: string[] = [];
  if (when.any === "*") conditions.push("Every execution");
  else {
    conditions.push(
      when.statusIn && when.statusIn.length > 0
        ? `Status: ${when.statusIn.join(", ")}`
        : "Any status",
    );
    if (when.severityMin || when.severityMax) {
      conditions.push(
        `Severity ${when.severityMin || "any"} → ${when.severityMax || "any"}`,
      );
    }
    if (when.resourceGroup) conditions.push(`RG: ${when.resourceGroup}`);
    if (when.namespace) conditions.push(`NS: ${when.namespace}`);
    if (when.schemaName) conditions.push(`Schema: ${when.schemaName}`);
    if (when.isAlert) conditions.push(`Alert: ${when.isAlert}`);
    if (when.any) conditions.push(`Match: ${when.any}`);
  }
  const targets = rule.then.map((action) =>
    action.type === "slack"
      ? `Slack · ${action.team || "default"}${
          action.channel ? ` ${action.channel}` : ""
        }`
      : `JSM · ${action.team || "default"}`,
  );
  return { conditions, targets };
}

function RuleEditorV2({
  rule,
  index,
  selected,
  onToggle,
  teamOptions,
  lockTeam,
  readOnly,
  onChange,
  onAddAction,
  onUpdateAction,
  onRemoveAction,
  onRemove,
}: {
  rule: Rule;
  index: number;
  selected: boolean;
  onToggle: () => void;
  teamOptions: string[];
  lockTeam: boolean;
  readOnly: boolean;
  onChange: (patch: Partial<Rule>) => void;
  onAddAction: () => void;
  onUpdateAction: (index: number, patch: Partial<Action>) => void;
  onRemoveAction: (index: number) => void;
  onRemove: () => void;
}) {
  // Dropdowns need overflow-visible, but only once the expand animation is over.
  const [settledOpen, setSettledOpen] = useState(selected);
  const updateWhen = (field: keyof Rule["when"], value: string | string[]) =>
    onChange({ when: { ...rule.when, [field]: value } });
  const inputClass = "input w-full min-w-0 h-11 mt-1 disabled:opacity-50";
  const summary = summarizeRule(rule);

  return (
    <article
      onClick={() => {
        if (!selected) onToggle();
      }}
      className={`border bg-cloudo-panel p-5 transition-colors duration-300 ${
        readOnly ? "opacity-80" : ""
      } ${
        selected
          ? "border-cloudo-accent/40"
          : "border-cloudo-border cursor-pointer hover:border-cloudo-accent/30"
      }`}
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3 min-w-0">
          <span className="text-[10px] text-cloudo-muted shrink-0">
            RULE {index + 1}
          </span>
          {selected ? (
            <select
              disabled={readOnly || lockTeam}
              value={rule.team || ""}
              onChange={(event) =>
                onChange({ team: event.target.value || undefined })
              }
              className="bg-cloudo-dark border border-cloudo-border px-2 py-1 text-[10px] uppercase disabled:opacity-50 min-w-0 max-w-[160px]"
            >
              <option value="">All teams</option>
              {teamOptions.map((team) => (
                <option key={team} value={team}>
                  {team}
                </option>
              ))}
            </select>
          ) : (
            <span className="text-[10px] uppercase tracking-widest text-cloudo-accent truncate">
              {rule.team || "All teams"}
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          {!readOnly && selected && (
            <button
              onClick={onRemove}
              className="text-cloudo-muted hover:text-cloudo-err"
            >
              <HiOutlineTrash />
            </button>
          )}
          <button
            type="button"
            aria-expanded={selected}
            aria-label={selected ? "Collapse rule" : "Expand rule"}
            onClick={(event) => {
              event.stopPropagation();
              onToggle();
            }}
            className="text-cloudo-muted hover:text-cloudo-text"
          >
            <HiOutlineChevronRight
              className={`w-4 h-4 transition-transform duration-300 ${
                selected ? "rotate-90" : ""
              }`}
            />
          </button>
        </div>
      </div>

      <div
        aria-hidden={selected}
        className={`grid transition-[grid-template-rows,opacity,margin] duration-300 ease-out ${
          selected
            ? "grid-rows-[0fr] opacity-0 mt-0"
            : "grid-rows-[1fr] opacity-100 mt-4"
        }`}
      >
        <div className="overflow-hidden min-h-0 space-y-3">
          <div className="flex flex-wrap gap-2">
            {summary.conditions.map((label) => (
              <span
                key={label}
                className="px-2 py-1 border border-cloudo-border bg-cloudo-dark text-[10px] normal-case text-cloudo-text"
              >
                {label}
              </span>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <HiOutlineChevronRight className="w-3 h-3 text-cloudo-muted shrink-0" />
            {summary.targets.length === 0 ? (
              <span className="text-[10px] uppercase tracking-widest text-cloudo-muted">
                No targets
              </span>
            ) : (
              summary.targets.map((label, i) => (
                <span
                  key={`${label}-${i}`}
                  className="px-2 py-1 border border-cloudo-accent/30 bg-cloudo-accent/10 text-[10px] normal-case text-cloudo-accent"
                >
                  {label}
                </span>
              ))
            )}
          </div>
        </div>
      </div>

      <div
        aria-hidden={!selected}
        onTransitionEnd={(event) => {
          if (
            event.target === event.currentTarget &&
            event.propertyName === "grid-template-rows"
          ) {
            setSettledOpen(selected);
          }
        }}
        className={`grid transition-[grid-template-rows,opacity,margin,visibility] duration-300 ease-out ${
          selected
            ? "grid-rows-[1fr] opacity-100 visible mt-5"
            : "grid-rows-[0fr] opacity-0 invisible mt-0"
        }`}
      >
        <div
          className={`min-h-0 ${
            selected && settledOpen ? "overflow-visible" : "overflow-hidden"
          }`}
        >
          <div className="space-y-5">
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
              <div className="text-[10px] uppercase tracking-widest text-cloudo-muted min-w-0">
                Status in
                <StatusMultiSelect
                  disabled={readOnly}
                  selected={rule.when.statusIn || []}
                  onChange={(value) => updateWhen("statusIn", value)}
                />
              </div>
              <label className="text-[10px] uppercase tracking-widest text-cloudo-muted min-w-0">
                Severity min
                <select
                  disabled={readOnly}
                  value={rule.when.severityMin || ""}
                  onChange={(event) =>
                    updateWhen("severityMin", event.target.value)
                  }
                  className={inputClass}
                >
                  <option value="">Any</option>
                  {["Sev0", "Sev1", "Sev2", "Sev3", "Sev4"].map((value) => (
                    <option key={value}>{value}</option>
                  ))}
                </select>
              </label>
              <label className="text-[10px] uppercase tracking-widest text-cloudo-muted min-w-0">
                Severity max
                <select
                  disabled={readOnly}
                  value={rule.when.severityMax || ""}
                  onChange={(event) =>
                    updateWhen("severityMax", event.target.value)
                  }
                  className={inputClass}
                >
                  <option value="">Any</option>
                  {["Sev0", "Sev1", "Sev2", "Sev3", "Sev4"].map((value) => (
                    <option key={value}>{value}</option>
                  ))}
                </select>
              </label>
              <label className="text-[10px] uppercase tracking-widest text-cloudo-muted min-w-0">
                Resource group
                <input
                  disabled={readOnly}
                  value={rule.when.resourceGroup || ""}
                  onChange={(event) =>
                    updateWhen("resourceGroup", event.target.value)
                  }
                  className={inputClass}
                />
              </label>
              <label className="text-[10px] uppercase tracking-widest text-cloudo-muted min-w-0">
                Namespace
                <input
                  disabled={readOnly}
                  value={rule.when.namespace || ""}
                  onChange={(event) =>
                    updateWhen("namespace", event.target.value)
                  }
                  className={inputClass}
                />
              </label>
              <label className="text-[10px] uppercase tracking-widest text-cloudo-muted min-w-0">
                Schema name
                <input
                  disabled={readOnly}
                  value={rule.when.schemaName || ""}
                  onChange={(event) =>
                    updateWhen("schemaName", event.target.value)
                  }
                  className={inputClass}
                />
              </label>
              <label className="text-[10px] uppercase tracking-widest text-cloudo-muted min-w-0">
                Is alert
                <select
                  disabled={readOnly}
                  value={rule.when.isAlert || ""}
                  onChange={(event) =>
                    updateWhen("isAlert", event.target.value)
                  }
                  className={inputClass}
                >
                  <option value="">Any</option>
                  <option value="true">True</option>
                  <option value="false">False</option>
                </select>
              </label>
              <label className="text-[10px] uppercase tracking-widest text-cloudo-muted min-w-0 xl:col-span-3">
                Wildcard
                <input
                  disabled={readOnly}
                  value={rule.when.any || ""}
                  onChange={(event) => updateWhen("any", event.target.value)}
                  className={inputClass}
                  placeholder="*"
                />
                <p className="text-[9px] normal-case tracking-normal text-cloudo-muted/70 mt-1 leading-snug">
                  Set to <span className="font-mono text-cloudo-accent">*</span>{" "}
                  to match every execution, bypassing all other filters above.
                  Leave empty to apply the filters normally.
                </p>
              </label>
            </div>

            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-[10px] uppercase tracking-widest text-cloudo-muted">
                  Notification targets
                </span>
                {!readOnly && (
                  <button
                    onClick={onAddAction}
                    className="text-[10px] text-cloudo-accent uppercase tracking-widest"
                  >
                    + Add target
                  </button>
                )}
              </div>
              {rule.then.map((action, actionIndex) => (
                <div
                  key={actionIndex}
                  className="grid grid-cols-1 md:grid-cols-[110px_minmax(0,1fr)_minmax(0,1fr)_auto] gap-2 items-center"
                >
                  <select
                    disabled={readOnly}
                    value={action.type}
                    onChange={(event) =>
                      onUpdateAction(actionIndex, {
                        type: event.target.value as ActionType,
                      })
                    }
                    className="input min-w-0 h-10 text-xs disabled:opacity-50"
                  >
                    <option value="slack">Slack</option>
                    <option value="jsm">JSM</option>
                  </select>
                  <select
                    disabled={readOnly}
                    value={action.team || "default"}
                    onChange={(event) =>
                      onUpdateAction(actionIndex, { team: event.target.value })
                    }
                    className="input min-w-0 h-10 text-xs disabled:opacity-50"
                  >
                    {teamOptions.map((team) => (
                      <option key={team} value={team}>
                        {team}
                      </option>
                    ))}
                  </select>
                  {action.type === "slack" ? (
                    <input
                      disabled={readOnly}
                      value={action.channel || ""}
                      onChange={(event) =>
                        onUpdateAction(actionIndex, {
                          channel: event.target.value,
                        })
                      }
                      className="input min-w-0 h-10 text-xs disabled:opacity-50"
                      placeholder="#channel override"
                    />
                  ) : (
                    <span className="text-[10px] text-cloudo-muted uppercase truncate">
                      Team JSM policy
                    </span>
                  )}
                  {!readOnly && (
                    <button
                      onClick={() => onRemoveAction(actionIndex)}
                      className="p-2 text-cloudo-muted hover:text-cloudo-err shrink-0"
                    >
                      <HiOutlineX />
                    </button>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </article>
  );
}
