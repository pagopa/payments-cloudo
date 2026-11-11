"use client";

import { useState, useEffect, useRef, ChangeEvent } from "react";
import {
  HiOutlineTerminal,
  HiOutlineLightBulb,
  HiOutlineBookOpen,
  HiOutlineDocumentText,
  HiOutlineVariable,
  HiOutlineCube,
  HiOutlinePlay,
  HiOutlineX,
  HiOutlineDatabase,
  HiOutlineClipboardList,
  HiOutlineInformationCircle,
  HiOutlineExclamationCircle,
  HiOutlineCheckCircle,
} from "react-icons/hi";
import {
  HiOutlineCodeBracket,
  HiOutlineRocketLaunch,
  HiOutlineDocumentArrowUp,
  HiOutlineArrowsRightLeft,
  HiOutlineArrowTopRightOnSquare,
  HiOutlineArrowDownTray,
} from "react-icons/hi2";
import { cloudoFetch } from "@/lib/api";
import {
  IconLabel,
  FormField,
  CollapsibleSection,
  ResizeHandle,
} from "./components";

const EXAMPLE_CATEGORIES = [
  {
    path: "templates",
    label: "Templates",
    count: 4,
    desc: "Punti di partenza per alert handler e runbook manuali, in bash e Python.",
  },
  {
    path: "aks",
    label: "AKS",
    count: 4,
    desc: "Diagnostica, restart, scaling e retry di job su Kubernetes.",
  },
  {
    path: "azure",
    label: "Azure",
    count: 4,
    desc: "App Gateway, metriche risorse, node pool, segreti Key Vault in scadenza.",
  },
  {
    path: "database",
    label: "Database",
    count: 2,
    desc: "Health check Postgres e terminazione di query bloccate.",
  },
  {
    path: "messaging",
    label: "Messaging",
    count: 2,
    desc: "Profondità code Service Bus e requeue di messaggi in errore.",
  },
  {
    path: "application",
    label: "Application",
    count: 4,
    desc: "Health check HTTP, scadenza TLS, restart App Service, purge cache.",
  },
];

const PAYLOAD_PRESETS: {
  id: string;
  label: string;
  desc: string;
  payload: unknown;
  args: string;
}[] = [
  {
    id: "aks-alert",
    label: "Alert AKS",
    desc: "Azure Monitor alert con label Kubernetes: popola AKS_*.",
    payload: {
      data: {
        essentials: {
          alertRule: "test-alert-rule",
          severity: "Sev4",
          monitorCondition: "Fired",
          alertTargetIDs: [
            "/subscriptions/00000000-0000-0000-0000-000000000001/resourcegroups/mock-RG/providers/microsoft.operationalinsights/workspaces/mock-workspace",
          ],
        },
        alertContext: {
          labels: {
            alertname: "KubeHpaMaxedOut",
            cluster: "mock-aks-cluster",
            horizontalpodautoscaler: "mock-hpa-name",
            instance: "ama-metrics-ksm.kube-system.svc.cluster.local:8080",
            job: "kube-state-metrics",
            deployment: "mock-deployment",
            namespace: "mock-namespace",
            resourcename: "mock-aks-resource",
            resourcegroup: "mock-aks-rg",
          },
        },
      },
    },
    args: "--verbose --timeout 30",
  },
  {
    id: "azure-alert",
    label: "Alert Azure (no AKS)",
    desc: "Alert su una risorsa Azure qualsiasi: nessuna label Kubernetes, quindi AKS_* resta vuoto.",
    payload: {
      data: {
        essentials: {
          alertRule: "keyvault-secret-expiring",
          severity: "Sev2",
          monitorCondition: "Fired",
          alertTargetIDs: [
            "/subscriptions/00000000-0000-0000-0000-000000000001/resourcegroups/mock-RG/providers/Microsoft.KeyVault/vaults/mock-keyvault",
          ],
        },
        alertContext: {},
      },
    },
    args: "",
  },
  {
    id: "manual",
    label: "Manuale / Schedule",
    desc: "Esecuzione manuale o da schedule: nessun alert in ingresso, solo run_args.",
    payload: {},
    args: "--mode=dry-run --target=prod",
  },
  {
    id: "elastic-aks",
    label: "Elastic → AKS",
    desc: 'source: "elastic" con payload.type "aks": popola risorsa e AKS_* dagli attributes.',
    payload: {
      source: "elastic",
      payload: {
        type: "aks",
        attributes: {
          namespace: "mock-namespace",
          cluster_name: "mock-aks-cluster",
          cluster_rg_name: "mock-aks-rg",
          deployment: "mock-deployment",
          hpa: "mock-hpa-name",
          job: "mock-job",
          severity: "Sev2",
        },
      },
    },
    args: "",
  },
  {
    id: "cloudo-source",
    label: 'Source "cloudo"',
    desc: "Il payload resta dentro resource_info ma serializzato come stringa JSON: serve un json.loads in più nel runbook.",
    payload: {
      source: "cloudo",
      payload: { foo: "bar" },
    },
    args: "",
  },
  {
    id: "custom-source",
    label: "Source custom",
    desc: 'Qualsiasi source non registrato (qui "manual") lascia il payload come oggetto JSON pulito: il modo più semplice per dati custom.',
    payload: {
      source: "manual",
      payload: {
        myVar: "var1",
      },
    },
    args: "",
  },
];

const ENV_VAR_GROUPS: {
  title: string;
  vars: { name: string; desc: string }[];
}[] = [
  {
    title: "Risorsa Azure",
    vars: [
      {
        name: "AZURE_SUBSCRIPTION_ID",
        desc: "Azure subscription ID (eg. 1111-wdqdq-23131....).",
      },
      { name: "RESOURCE_ID", desc: "Azure resource ID (/subscriptions/...)." },
      { name: "RESOURCE_RG", desc: "Resource group della risorsa." },
      { name: "RESOURCE_NAME", desc: "Nome finale della risorsa." },
      { name: "MONITOR_CONDITION", desc: "Stato dell'alert (Fired/Resolved)." },
    ],
  },
  {
    title: "Kubernetes (AKS)",
    vars: [
      { name: "AKS_NAMESPACE", desc: "Namespace Kubernetes (se presente)." },
      { name: "AKS_POD", desc: "Nome del Pod (se presente)." },
      { name: "AKS_DEPLOYMENT", desc: "Nome del Deployment (se presente)." },
      { name: "AKS_JOB", desc: "Nome del Job (se presente)." },
      { name: "AKS_HPA", desc: "Horizontal Pod Autoscaler (se presente)." },
      {
        name: "KUBECONFIG",
        desc: "Presente solo se l'AKS login è andato a buon fine.",
      },
    ],
  },
  {
    title: "Esecuzione",
    vars: [
      {
        name: "CLOUDO_PAYLOAD",
        desc: "resource_info completo come JSON (include anche severity e schema_id).",
      },
      { name: "CLOUDO_EXEC_ID", desc: "ID univoco di questa esecuzione." },
      { name: "CLOUDO_NAME", desc: "Nome assegnato all'esecuzione." },
      { name: "CLOUDO_RUNBOOK", desc: "Nome del runbook in esecuzione." },
      { name: "CLOUDO_WORKER", desc: "Worker che ha eseguito lo script." },
      {
        name: "CLOUDO_ONCALL",
        desc: "Il runbook è gestito nel flusso di oncall (true/false).",
      },
    ],
  },
  {
    title: "Ambiente",
    vars: [
      {
        name: "CLOUDO_ENVIRONMENT",
        desc: "Environment di riferimento ClouDO (dev, uat, prod).",
      },
      {
        name: "CLOUDO_ENVIRONMENT_SHORT",
        desc: "Environment abbreviato (d, u, p).",
      },
    ],
  },
];

const HANDBOOK_SECTIONS = [
  { id: "simulator", label: "Simulator" },
  { id: "manuale", label: "Manuale" },
  { id: "parsers", label: "Parser" },
  { id: "env-vars", label: "Variabili" },
  { id: "deployment", label: "Deployment" },
];

const SOURCE_PARSERS: { source: string; result: string; example: string }[] = [
  {
    source: '(nessuno) / "azure_monitor"',
    result:
      "Si aspetta un alert Azure Monitor (data.essentials/alertContext). Altrimenti tutti i campi restano vuoti e payload è sempre {}.",
    example:
      '{"data": {"essentials": {"severity": "Sev2", "monitorCondition": "Fired"}, "alertContext": {}}}',
  },
  {
    source: '"elastic"',
    result: "Solo per alert Elastic→AKS in un formato specifico.",
    example:
      '{"source": "elastic", "payload": {"type": "aks", "attributes": {"namespace": "ns", "cluster_name": "my-cluster", "cluster_rg_name": "my-rg"}}}',
  },
  {
    source: '"cloudo"',
    result:
      "Payload preservato ma come stringa JSON — serve un json.loads in più nel runbook.",
    example: '{"source": "cloudo", "payload": {"foo": "bar"}}',
  },
  {
    source: "qualsiasi altro nome",
    result:
      "Payload preservato come oggetto JSON pulito: il modo più semplice per dati custom.",
    example: '{"source": "example", "payload": {"myVar": "..."}}',
  },
];

export default function StudioPage() {
  const [notifications, setNotifications] = useState<
    { id: string; type: "success" | "error"; message: string }[]
  >([]);
  const [isTestModalOpen, setIsTestModalOpen] = useState(false);
  const [isFeatureDevEnabled, setIsFeatureDevEnabled] = useState(false);
  const [modalWidth, setModalWidth] = useState(1200); // Default width in pixels
  const [modalHeight, setModalHeight] = useState(700); // Default height in pixels

  const handleTestSuccess = (msg: string) => {
    addNotification("success", msg);
    setIsTestModalOpen(false);
  };

  // Payload Simulator State
  const [activePresetId, setActivePresetId] = useState(PAYLOAD_PRESETS[0].id);
  const [payloadInput, setPayloadInput] = useState(
    JSON.stringify(PAYLOAD_PRESETS[0].payload, null, 2),
  );
  const [runArgsInput, setRunArgsInput] = useState(PAYLOAD_PRESETS[0].args);
  const [parsedEnv, setParsedEnv] = useState<Record<string, string>>({});

  const applyPreset = (presetId: string) => {
    const preset = PAYLOAD_PRESETS.find((p) => p.id === presetId);
    if (!preset) return;
    setActivePresetId(presetId);
    setPayloadInput(JSON.stringify(preset.payload, null, 2));
    setRunArgsInput(preset.args);
  };

  // Fetch feature flag for dev test run
  useEffect(() => {
    const checkFeatureFlag = async () => {
      try {
        const response = await cloudoFetch("/api/features");
        if (response.ok) {
          const data = await response.json();
          setIsFeatureDevEnabled(data.FEATURE_DEV === true);
        }
      } catch (error) {
        console.error("Failed to fetch feature flag:", error);
      }
    };
    checkFeatureFlag();
  }, []);

  const createResizeHandler =
    (onResize: (dx: number, dy: number) => void) =>
    (e: React.MouseEvent<HTMLDivElement>) => {
      e.preventDefault();
      const startX = e.clientX;
      const startY = e.clientY;

      const handleMouseMove = (moveEvent: MouseEvent) => {
        const dx = moveEvent.clientX - startX;
        const dy = moveEvent.clientY - startY;
        onResize(dx, dy);
      };

      const handleMouseUp = () => {
        document.removeEventListener("mousemove", handleMouseMove);
        document.removeEventListener("mouseup", handleMouseUp);
      };

      document.addEventListener("mousemove", handleMouseMove);
      document.addEventListener("mouseup", handleMouseUp);
    };

  useEffect(() => {
    try {
      const parsed = JSON.parse(payloadInput);

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const getLower = (obj: any, key: string): any => {
        if (!obj || typeof obj !== "object") return undefined;
        const lowerKey = key.toLowerCase();
        const foundKey = Object.keys(obj).find(
          (k) => k.toLowerCase() === lowerKey,
        );
        return foundKey ? obj[foundKey] : undefined;
      };

      const sourceRaw = getLower(parsed, "source");
      const source =
        typeof sourceRaw === "string" && sourceRaw.trim() !== ""
          ? sourceRaw.toLowerCase()
          : "azure_monitor";

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      let resourceInfo: Record<string, any>;

      if (source === "azure_monitor") {
        const data = getLower(parsed, "data") || {};
        const essentials = getLower(data, "essentials") || {};
        const ctx = getLower(data, "alertcontext") || {};
        const labels = getLower(ctx, "labels") || {};
        const annotations = getLower(ctx, "annotations") || {};

        // Resolve Resource ID
        let resource_id: string | null = null;
        const candidates: string[] = [];

        const alertTargetIds = getLower(essentials, "alerttargetids");
        if (Array.isArray(alertTargetIds)) {
          candidates.push(
            ...alertTargetIds.filter((x) => typeof x === "string"),
          );
        }

        const mrid = getLower(labels, "microsoft.resourceid");
        if (typeof mrid === "string") candidates.push(mrid);

        const rid = getLower(ctx, "resourceid");
        if (typeof rid === "string") candidates.push(rid);

        resource_id =
          candidates.find((x) => x.startsWith("/subscriptions/")) || null;

        // Resolve RG and Name
        let resource_group: string | null = null;
        let resource_name: string | null = null;

        if (resource_id) {
          const parts = resource_id.replace(/^\/+|\/+$/g, "").split("/");
          const parts_l = parts.map((p) => p.toLowerCase());
          const rg_index = parts_l.indexOf("resourcegroups");
          if (rg_index !== -1 && parts[rg_index + 1]) {
            resource_group = parts[rg_index + 1];
          }
          resource_name = parts[parts.length - 1] || null;
        } else {
          const config_items = getLower(essentials, "configurationitems");
          if (Array.isArray(config_items) && config_items.length > 0) {
            resource_name = config_items[0];
          }
          resource_name =
            resource_name ||
            getLower(ctx, "resourcename") ||
            getLower(labels, "resourcename") ||
            null;
          resource_group =
            getLower(ctx, "resourcegroup") ||
            getLower(labels, "resourcegroup") ||
            null;
          resource_id =
            getLower(ctx, "resourceid") ||
            getLower(labels, "resourceid") ||
            null;
        }

        // Kubernetes fields
        const namespace =
          getLower(labels, "namespace") ||
          getLower(labels, "kubernetes_namespace") ||
          getLower(annotations, "namespace") ||
          getLower(annotations, "kubernetes_namespace") ||
          null;

        const pod =
          getLower(labels, "pod") ||
          getLower(labels, "kubernetes_pod_name") ||
          getLower(annotations, "pod") ||
          getLower(annotations, "kubernetes_pod_name") ||
          null;

        const deployment =
          getLower(labels, "deployment") ||
          getLower(labels, "kubernetes_deployment") ||
          getLower(annotations, "deployment") ||
          getLower(annotations, "kubernetes_deployment") ||
          null;

        const hpa =
          getLower(labels, "horizontalpodautoscaler") ||
          getLower(labels, "kubernetes_horizontalpodautoscaler") ||
          getLower(annotations, "horizontalpodautoscaler") ||
          getLower(annotations, "kubernetes_horizontalpodautoscaler") ||
          null;

        let job =
          getLower(labels, "kubernetes_job_name") ||
          getLower(annotations, "kubernetes_job_name") ||
          getLower(labels, "job_name") ||
          getLower(annotations, "job_name") ||
          null;

        if (!job) {
          const cand = getLower(labels, "job") || getLower(annotations, "job");
          if (cand && cand !== "kube-state-metrics") {
            job = cand;
          }
        }

        resourceInfo = {
          _raw: JSON.stringify(parsed),
          schema_id: null,
          monitor_condition: getLower(essentials, "monitorcondition") || "Fired",
          severity: getLower(essentials, "severity") || "Sev4",
          resource_name,
          resource_rg: resource_group,
          resource_id,
          aks_namespace: namespace,
          aks_pod: pod,
          aks_deployment: deployment,
          aks_job: job,
          aks_horizontalpodautoscaler: hpa,
          team: null,
          // Hardcoded empty for this source, regardless of input: the real
          // AzureMonitorParser never preserves the raw body as payload.
          payload: {},
        };
      } else {
        // GenericSourceParser (elastic, cloudo, or any other custom name):
        // expects the envelope {"source": "...", "payload": {...}}.
        const rule = getLower(parsed, "rule") || "";
        let severity = getLower(parsed, "severity") || "Sev4";
        let monitorCondition =
          getLower(parsed, "monitorcondition") ||
          getLower(parsed, "monitor_condition") ||
          "Fired";

        const payloadFieldRaw = getLower(parsed, "payload");
        const innerPayload =
          payloadFieldRaw &&
          typeof payloadFieldRaw === "object" &&
          !Array.isArray(payloadFieldRaw)
            ? payloadFieldRaw
            : {};

        let resource_name: string | null = null;
        let resource_group: string | null = null;
        let namespace: string | null = null;
        let deployment: string | null = null;
        let hpa: string | null = null;
        let job: string | null = null;
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        let payloadOut: any = innerPayload;

        if (source === "elastic") {
          const attrs = getLower(innerPayload, "attributes") || {};
          const payloadSeverity =
            getLower(attrs, "severity") || getLower(innerPayload, "severity");
          if (payloadSeverity) severity = payloadSeverity;
          const payloadMonitorCondition =
            getLower(attrs, "monitorcondition") ||
            getLower(attrs, "monitor_condition") ||
            getLower(innerPayload, "monitorcondition") ||
            getLower(innerPayload, "monitor_condition");
          if (payloadMonitorCondition)
            monitorCondition = payloadMonitorCondition;

          if (getLower(innerPayload, "type") === "aks") {
            namespace = getLower(attrs, "namespace") || null;
            resource_name = getLower(attrs, "cluster_name") || null;
            resource_group = getLower(attrs, "cluster_rg_name") || null;
            deployment = getLower(attrs, "deployment") || null;
            hpa = getLower(attrs, "hpa") || null;
            job = getLower(attrs, "job") || null;
          }
          // payload stays the original object for this source.
        } else if (source === "cloudo") {
          // CloudoParser serializes payload to a JSON string instead of
          // keeping it as an object — the one source that double-encodes.
          payloadOut = JSON.stringify(innerPayload);
        }
        // Any other source name: generic fallback, no field extraction,
        // payload stays a plain object (innerPayload).

        resourceInfo = {
          _raw: JSON.stringify(parsed),
          schema_id: rule ? [rule] : [],
          monitor_condition: monitorCondition,
          severity,
          resource_name,
          resource_rg: resource_group,
          resource_id: null,
          aks_namespace: namespace,
          aks_pod: null,
          aks_deployment: deployment,
          aks_job: job,
          aks_horizontalpodautoscaler: hpa,
          team: null,
          payload: payloadOut,
        };
      }

      const env: Record<string, string> = {};
      if (resourceInfo.resource_id) env["RESOURCE_ID"] = String(resourceInfo.resource_id);
      if (resourceInfo.resource_rg) env["RESOURCE_RG"] = String(resourceInfo.resource_rg);
      if (resourceInfo.resource_name) env["RESOURCE_NAME"] = String(resourceInfo.resource_name);
      if (resourceInfo.monitor_condition) env["MONITOR_CONDITION"] = String(resourceInfo.monitor_condition);
      if (resourceInfo.aks_namespace) env["AKS_NAMESPACE"] = String(resourceInfo.aks_namespace);
      if (resourceInfo.aks_pod) env["AKS_POD"] = String(resourceInfo.aks_pod);
      if (resourceInfo.aks_deployment) env["AKS_DEPLOYMENT"] = String(resourceInfo.aks_deployment);
      if (resourceInfo.aks_job) env["AKS_JOB"] = String(resourceInfo.aks_job);
      if (resourceInfo.aks_horizontalpodautoscaler) env["AKS_HPA"] = String(resourceInfo.aks_horizontalpodautoscaler);

      // Severity and schema/alert IDs are not separate env vars: they only
      // appear as fields inside CLOUDO_PAYLOAD (resource_info JSON).
      env["CLOUDO_PAYLOAD"] = JSON.stringify(resourceInfo);
      if (JSON.stringify(parsedEnv) !== JSON.stringify(env)) {
        // eslint-disable-next-line react-hooks/set-state-in-effect
        setParsedEnv(env);
      }
    } catch {}
  }, [payloadInput, parsedEnv]);

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

  const handleExportEnv = () => {
    const lines = Object.entries(parsedEnv).map(
      ([key, value]) => `${key}="${String(value).replace(/"/g, '\\"')}"`,
    );
    const element = document.createElement("a");
    const file = new Blob([lines.join("\n") + "\n"], { type: "text/plain" });
    element.href = URL.createObjectURL(file);
    element.download = "runbook.env";
    document.body.appendChild(element);
    element.click();
    addNotification("success", "Variabili esportate come runbook.env");
  };

  return (
    <div className="flex flex-col h-full bg-cloudo-dark text-cloudo-text font-mono selection:bg-cloudo-accent/30">
      {/* Header Bar */}
      <div className="flex items-center justify-between px-8 py-5 border-b border-cloudo-border bg-cloudo-panel sticky top-0 z-20">
        <div className="flex items-center gap-4 shrink-0">
          <div className="p-2 bg-cloudo-accent/5 border border-cloudo-accent/20 shrink-0">
            <HiOutlineBookOpen className="text-cloudo-accent w-5 h-5" />
          </div>
          <div>
            <h1 className="text-sm font-black tracking-[0.2em] text-cloudo-text uppercase">
              Developer Runbook Studio
            </h1>
            <p className="text-[11px] text-cloudo-muted font-bold uppercase tracking-[0.3em] opacity-70">
              Technical Documentation // HANDBOOK_MODE_ACTIVE
            </p>
          </div>
        </div>
        <div className="flex items-center gap-6">
          {isFeatureDevEnabled && (
            <div className="relative group">
              <button
                onClick={() => setIsTestModalOpen(true)}
                className="btn btn-primary h-10 px-4 flex items-center gap-2 group"
              >
                <HiOutlineRocketLaunch className="w-4 h-4 transition-transform" />
                Test Runbook
              </button>
            </div>
          )}
        </div>
      </div>

      <div className="flex-1 flex overflow-hidden">
        {/* Left Sidebar: Examples & Tips */}
        <div className="w-80 border-r border-cloudo-border bg-cloudo-accent/5 overflow-y-auto p-6 space-y-6 shrink-0">
          <div className="flex items-center gap-2 mb-3">
            <HiOutlineDocumentText className="text-cloudo-accent w-4 h-4" />
            <h2 className="text-[11px] font-black uppercase tracking-[0.2em] text-cloudo-text">
              Examples &amp; Templates
            </h2>
          </div>
          <p className="text-[11px] text-cloudo-muted leading-relaxed">
            Template vuoti e runbook reali, mantenuti nel repository: parti da
            un esempio invece che da zero.
          </p>
          <a
            href="https://github.com/pagopa/payments-cloudo/tree/main/examples/runbooks"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center justify-between gap-2 p-3 bg-cloudo-panel border border-cloudo-accent/30 hover:border-cloudo-accent transition-colors group"
          >
            <span className="text-[11px] font-black text-cloudo-accent uppercase tracking-widest">
              examples/runbooks
            </span>
            <HiOutlineArrowTopRightOnSquare className="w-3.5 h-3.5 text-cloudo-accent shrink-0 group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-transform" />
          </a>
          <div className="space-y-2">
            {EXAMPLE_CATEGORIES.map((cat) => (
              <a
                key={cat.path}
                href={`https://github.com/pagopa/payments-cloudo/tree/main/examples/runbooks/${cat.path}`}
                target="_blank"
                rel="noopener noreferrer"
                className="block p-3 bg-cloudo-panel border border-cloudo-border hover:border-cloudo-muted/70 transition-all"
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="text-[11px] font-black text-cloudo-text uppercase tracking-widest">
                    {cat.label}
                  </span>
                  <span className="text-[10px] text-cloudo-muted opacity-60">
                    {cat.count} file
                  </span>
                </div>
                <p className="text-[11px] text-cloudo-muted leading-relaxed opacity-60">
                  {cat.desc}
                </p>
              </a>
            ))}
          </div>
          <p className="text-[11px] text-cloudo-muted leading-relaxed italic">
            Per provarne uno subito, copia il contenuto nel pannello{" "}
            <span className="text-cloudo-accent">Test Runbook</span> qui sopra:
            gira su un worker senza bisogno di merge. Se e solo se è attivo il
            pannello da feature Flags, se disabilitato farlo abilitare o tornare
            in <span className="text-cloudo-accent">ambiente di sviluppo.</span>
          </p>

          <div className="pt-8 space-y-4">
            <div className="flex items-center gap-2 mb-3">
              <HiOutlineLightBulb className="text-cloudo-accent w-4 h-4" />
              <h2 className="text-[11px] font-black uppercase tracking-[0.2em] text-cloudo-text">
                Quick Tips
              </h2>
            </div>
            <div className="space-y-4">
              <TipItem text="Gestisci esplicitamente gli exit code per permettere al Worker di capire l'esito." />
              <TipItem text="I parametri vengono passati sia via CLOUDO_PAYLOAD che variabili d'ambiente singole." />
              <TipItem text="Rendi il runbook sicuro da eseguire due volte: un alert può ripartire più volte sullo stesso stato." />
              <TipItem text="In caso di allarmi inerenti ad AKS, ClouDO effettua il login nel namespace coinvolto con un token valido 10 minuti; verifica sempre che KUBECONFIG sia presente prima di usare kubectl." />
              <TipItem text="Non loggare mai secret o token: le variabili con TOKEN, SECRET, KEY, PASS o AUTH nel nome arrivano comunque mascherate come ******." />
              <TipItem text="Assegna schemi allo stesso gruppo per evitare che runbook in conflitto tra loro possano girare in parallelo." />
            </div>
          </div>
        </div>

        {/* Center: Handbook Content */}
        <div className="flex-1 overflow-y-auto bg-cloudo-dark custom-scrollbar">
          <div className="max-w-4xl mx-auto p-12 space-y-12">
            <nav className="sticky top-0 z-10 -mt-12 -mx-12 px-12 py-3 mb-4 bg-cloudo-dark/95 backdrop-blur-sm border-b border-cloudo-border flex gap-6">
              {HANDBOOK_SECTIONS.map((s) => (
                <a
                  key={s.id}
                  href={`#${s.id}`}
                  className="text-[10px] font-black uppercase tracking-widest text-cloudo-muted hover:text-cloudo-accent transition-colors"
                >
                  {s.label}
                </a>
              ))}
            </nav>

            <section id="simulator" className="space-y-6 scroll-mt-16">
              <div className="flex items-center gap-3 border-b border-cloudo-border pb-4">
                <HiOutlinePlay className="text-cloudo-accent w-6 h-6" />
                <h2 className="text-xl font-black uppercase tracking-widest text-cloudo-text">
                  Execution & Payload Simulator
                </h2>
              </div>
              <p className="text-sm text-cloudo-muted leading-relaxed">
                Utilizza questa area per simulare come ClouDO interpreterà il
                tuo payload e come verrà composto il comando di esecuzione
                finale. Il parsing segue lo stesso routing per{" "}
                <code className="text-cloudo-accent">source</code> descritto
                più sotto in &quot;Alert Source &amp; Payload Parsing&quot;:
                prova i preset per vedere come cambia ogni parser.
              </p>

              <div className="space-y-2">
                <label className="text-[10px] font-black uppercase tracking-widest text-cloudo-muted">
                  Preset
                </label>
                <div className="flex flex-wrap gap-2">
                  {PAYLOAD_PRESETS.map((preset) => (
                    <button
                      key={preset.id}
                      type="button"
                      onClick={() => applyPreset(preset.id)}
                      title={preset.desc}
                      className={`text-[10px] font-bold uppercase tracking-widest px-3 py-2 border transition-colors ${
                        activePresetId === preset.id
                          ? "bg-cloudo-accent/10 border-cloudo-accent text-cloudo-accent"
                          : "bg-cloudo-panel border-cloudo-border text-cloudo-muted hover:border-cloudo-muted/70"
                      }`}
                    >
                      {preset.label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="grid grid-cols-2 gap-8">
                <div className="space-y-4">
                  <div className="flex items-center justify-between">
                    <label className="text-[11px] font-black uppercase tracking-widest text-cloudo-text flex items-center gap-2 block">
                      <HiOutlineDatabase className="text-cloudo-accent" /> Input
                      Payload (JSON)
                    </label>
                  </div>
                  <textarea
                    value={payloadInput}
                    onChange={(e) => setPayloadInput(e.target.value)}
                    className="w-full h-48 bg-cloudo-panel border border-cloudo-border p-4 text-xs font-mono text-cloudo-text focus:border-cloudo-accent outline-none custom-scrollbar resize-none"
                    placeholder='{ "key": "value" }'
                  />
                </div>

                <div className="space-y-4">
                  <label className="text-[11px] font-black uppercase tracking-widest text-cloudo-text flex items-center gap-2 block">
                    <HiOutlineClipboardList className="text-cloudo-accent" />{" "}
                    Run Arguments
                  </label>
                  <input
                    type="text"
                    value={runArgsInput}
                    onChange={(e) => setRunArgsInput(e.target.value)}
                    className="w-full bg-cloudo-panel border border-cloudo-border p-4 text-xs font-mono text-cloudo-text focus:border-cloudo-accent outline-none"
                    placeholder="--arg1 val1 --arg2"
                  />

                  <div className="p-4 bg-cloudo-accent/5 border border-cloudo-accent/20 rounded-sm space-y-4">
                    <div>
                      <span className="text-[10px] font-black uppercase tracking-widest text-cloudo-accent block mb-2">
                        Python Execution:
                      </span>
                      <code className="text-xs text-cloudo-text break-all">
                        python3 runbook.py {runArgsInput}
                      </code>
                    </div>
                    <div>
                      <span className="text-[10px] font-black uppercase tracking-widest text-cloudo-accent block mb-2">
                        Bash Execution:
                      </span>
                      <code className="text-xs text-cloudo-text break-all">
                        ./runbook.sh {runArgsInput}
                      </code>
                    </div>
                  </div>
                </div>
              </div>

              <div className="space-y-4">
                <div className="flex items-center justify-between">
                  <label className="text-[11px] font-black uppercase tracking-widest text-cloudo-text flex items-center gap-2 block">
                    <HiOutlineVariable className="text-cloudo-accent" /> ClouDO
                    Parsed Environment Variables
                  </label>
                  <button
                    type="button"
                    onClick={handleExportEnv}
                    className="text-[10px] uppercase font-bold text-cloudo-muted hover:text-cloudo-accent flex items-center gap-1 transition-colors"
                  >
                    <HiOutlineArrowDownTray className="w-3 h-3" /> Export .env
                  </button>
                </div>
                <div className="grid grid-cols-2 gap-4 bg-cloudo-dark/50 border border-cloudo-border p-6 rounded-sm">
                  {Object.entries(parsedEnv).map(
                    ([key, value]) =>
                      key !== "CLOUDO_PAYLOAD" && (
                        <div
                          key={key}
                          className="flex flex-col gap-1 border-b border-cloudo-border/10 pb-2 overflow-hidden"
                        >
                          <span className="text-[10px] font-black text-cloudo-accent uppercase truncate">
                            {key}
                          </span>
                          <span
                            className="text-xs text-cloudo-muted font-mono"
                            title={value}
                          >
                            {value}
                          </span>
                        </div>
                      ),
                  )}
                  <div className="col-span-2 mt-2 pt-2 border-t border-cloudo-border/20">
                    <span className="text-[10px] font-black text-cloudo-accent uppercase block mb-1">
                      CLOUDO_PAYLOAD
                    </span>
                    <code className="text-[10px] text-cloudo-muted break-all opacity-60">
                      {parsedEnv["CLOUDO_PAYLOAD"]}
                    </code>
                  </div>
                </div>
              </div>
            </section>

            <section id="manuale" className="space-y-6 scroll-mt-16">
              <div className="flex items-center gap-3 border-b border-cloudo-border pb-4">
                <HiOutlineInformationCircle className="text-cloudo-accent w-6 h-6" />
                <h2 className="text-xl font-black uppercase tracking-widest text-cloudo-text">
                  Manuale Sviluppatore Runbook
                </h2>
              </div>
              <p className="text-sm text-cloudo-muted leading-relaxed">
                Benvenuto nella guida tecnica di ClouDO. Qui sotto trovi il
                riferimento rapido su cosa riceve il tuo script; per la guida
                completa (comportamento del Worker, AKS/Azure login, regole di
                sicurezza e checklist per scrivere un buon runbook) fai
                riferimento al README di{" "}
                <a
                  href="https://github.com/pagopa/payments-cloudo/tree/main/examples/runbooks"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-cloudo-accent hover:underline inline-flex items-center gap-1"
                >
                  examples/runbooks
                  <HiOutlineArrowTopRightOnSquare className="w-3 h-3" />
                </a>
                , che è la fonte di verità e viene mantenuto insieme al Worker.
              </p>
            </section>

            <section id="parsers" className="space-y-4 scroll-mt-16">
              <div className="flex items-center gap-2 text-cloudo-text/80">
                <span className="text-cloudo-accent">
                  <HiOutlineCodeBracket />
                </span>
                <span className="text-[11px] font-black uppercase tracking-widest">
                  Alert Source &amp; Payload Parsing
                </span>
              </div>
              <p className="text-[13px] text-cloudo-muted leading-relaxed opacity-90">
                Il campo <code className="text-cloudo-accent">source</code>{" "}
                nel body decide come viene letto il payload. Senza di esso,
                ClouDO assume un alert Azure Monitor: se il tuo JSON non ha
                quella forma, i campi restano vuoti anche se è valido.
              </p>

              <div className="grid grid-cols-2 gap-4">
                <div className="p-4 bg-cloudo-accent/5 border border-cloudo-accent/20 rounded-sm space-y-2">
                  <span className="text-[10px] font-black uppercase tracking-widest text-cloudo-accent block">
                    Valore semplice
                  </span>
                  <p className="text-[13px] text-cloudo-muted leading-relaxed">
                    Usa{" "}
                    <span className="text-cloudo-text">Run Arguments</span>{" "}
                    qui sopra, non il payload: arriva sempre come argv.
                  </p>
                </div>
                <div className="p-4 bg-cloudo-accent/5 border border-cloudo-accent/20 rounded-sm space-y-2">
                  <span className="text-[10px] font-black uppercase tracking-widest text-cloudo-accent block">
                    Dati strutturati
                  </span>
                  <code className="block text-[11px] text-cloudo-accent bg-cloudo-dark/50 p-2 rounded-sm break-all">
                    {'{"source": "example", "payload": {"myVar": "..."}}'}
                  </code>
                  <p className="text-[13px] text-cloudo-muted leading-relaxed">
                    Resta un oggetto JSON pulito in{" "}
                    <code className="text-cloudo-text">
                      resource_info.payload
                    </code>
                    . Qualsiasi nome va bene tranne{" "}
                    <code className="text-cloudo-text">
                      {'"cloudo"'}
                    </code>
                    (vedi sotto).
                  </p>
                </div>
              </div>

              <div className="border border-cloudo-border rounded-sm overflow-hidden divide-y divide-cloudo-border">
                {SOURCE_PARSERS.map((p) => (
                  <div
                    key={p.source}
                    className="px-4 py-3 grid grid-cols-[180px_1fr] gap-4 items-start"
                  >
                    <code className="text-xs text-cloudo-accent font-black">
                      {p.source}
                    </code>
                    <div className="space-y-1.5">
                      <span className="text-[13px] text-cloudo-muted leading-relaxed block">
                        {p.result}
                      </span>
                      <code className="block text-[11px] text-cloudo-text/80 bg-cloudo-dark/50 p-2 rounded-sm break-all">
                        {p.example}
                      </code>
                    </div>
                  </div>
                ))}
              </div>
            </section>

            <div className="grid grid-cols-2 gap-8">
              <HandbookSection title="Runtime Context" icon={<HiOutlineCube />}>
                Il Worker inietta i parametri parsati dal JSON nelle variabili
                d&apos;ambiente come ad esempio{" "}
                <code className="text-cloudo-accent">RESOURCE_ID</code>. In
                Python:{" "}
                <code className="text-cloudo-accent">
                  os.environ.get(&apos;RESOURCE_ID&apos;)
                </code>
                .
              </HandbookSection>

              <HandbookSection
                title="Output Capture"
                icon={<HiOutlineTerminal />}
              >
                ClouDO cattura sia{" "}
                <code className="text-cloudo-text">stdout</code> che{" "}
                <code className="text-cloudo-text">stderr</code>. Usa{" "}
                <code className="text-cloudo-text">stderr</code> per la
                telemetria e <code className="text-cloudo-accent">stdout</code>{" "}
                per i dati finali.
              </HandbookSection>
            </div>

            <section id="env-vars" className="space-y-2 scroll-mt-16">
              <div className="flex items-center gap-2 text-cloudo-text/80">
                <span className="text-cloudo-accent">
                  <HiOutlineVariable />
                </span>
                <span className="text-[11px] font-black uppercase tracking-widest">
                  Common Environment Variables
                </span>
              </div>
              <div className="mt-2 border border-cloudo-border rounded-sm overflow-hidden divide-y divide-cloudo-border">
                {ENV_VAR_GROUPS.map((group) => (
                  <div key={group.title}>
                    <div className="px-4 py-2 bg-cloudo-accent/5 text-[10px] font-black uppercase tracking-widest text-cloudo-accent">
                      {group.title}
                    </div>
                    <div className="divide-y divide-cloudo-border/30">
                      {group.vars.map((v) => (
                        <div
                          key={v.name}
                          className="px-4 py-3 grid grid-cols-[200px_1fr] gap-4 items-start"
                        >
                          <code className="text-xs text-cloudo-accent font-black break-all">
                            {v.name}
                          </code>
                          <span className="text-[13px] text-cloudo-muted leading-relaxed">
                            {v.desc}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
              <p className="text-[12px] text-cloudo-muted/70 leading-relaxed mt-3 italic">
                Le variabili il cui nome contiene TOKEN, SECRET, KEY, PASS, PWD
                o AUTH arrivano sempre mascherate come ******, anche se le
                imposti tu stesso: per usare un segreto, leggilo da Key Vault a
                runtime.
              </p>
            </section>

            <HandbookSection
              id="deployment"
              title="Deployment Workflow"
              icon={<HiOutlineDocumentText />}
            >
              <p>Per rendere operativo il tuo runbook:</p>
              <ol className="list-decimal list-inside mt-4 space-y-2 text-cloudo-muted/80">
                <li>
                  Salva lo script nel path configurato sul Worker o repo di
                  riferimento (es.{" "}
                  <code className="text-cloudo-text">src/runbooks/</code>), o
                  parti da uno degli esempi mantenuti in{" "}
                  <code className="text-cloudo-text">examples/runbooks/</code>.
                </li>
                <li>
                  Assicurati che lo script abbia i permessi di esecuzione (
                  <code className="text-cloudo-text">chmod +x</code>).
                </li>
                <li>
                  Crea o aggiorna lo Schema nel Registry puntando al file
                  creato.
                </li>
                <li>
                  Il Worker scarica i runbook da GitHub al momento
                  dell&apos;esecuzione: una volta fatto merge sul branch
                  configurato è già live, senza bisogno di un deploy. Vale anche
                  al contrario: se sposti o rinomini il file, gli schemi che lo
                  referenziano iniziano a fallire.
                </li>
              </ol>
            </HandbookSection>
          </div>
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
      {isTestModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-cloudo-bg/80 backdrop-blur-sm p-4">
          <div
            className="relative bg-cloudo-panel border border-cloudo-border shadow-2xl overflow-hidden flex flex-col"
            style={{
              width: `${modalWidth}px`,
              height: `${modalHeight}px`,
              minWidth: "800px",
              minHeight: "500px",
            }}
          >
            {/* Modal Header */}
            <div className="px-8 py-4 border-b border-cloudo-border bg-cloudo-accent/5 flex justify-between items-center flex-shrink-0">
              <h2 className="text-xs font-black uppercase tracking-[0.3em] text-cloudo-accent">
                Runbook Test // Live_Test_Environment
              </h2>
              <button
                onClick={() => setIsTestModalOpen(false)}
                className="text-cloudo-muted hover:text-cloudo-text transition-colors"
              >
                ✕
              </button>
            </div>

            {/* Modal Content - scrollable */}
            <div className="flex-1 overflow-y-auto">
              <TestScriptForm
                onSuccess={handleTestSuccess}
                onCancel={() => setIsTestModalOpen(false)}
                onError={(err: string) => addNotification("error", err)}
                initialBody={payloadInput}
                initialArgs={runArgsInput}
              />
            </div>

            <ResizeHandle
              direction="e"
              onResize={createResizeHandler((dx) => {
                setModalWidth(Math.max(800, modalWidth + dx));
              })}
              title="Drag to resize width"
            />

            <ResizeHandle
              direction="s"
              onResize={createResizeHandler((_, dy) => {
                setModalHeight(Math.max(500, modalHeight + dy));
              })}
              title="Drag to resize height"
            />

            <ResizeHandle
              direction="se"
              onResize={createResizeHandler((dx, dy) => {
                setModalWidth(Math.max(800, modalWidth + dx));
                setModalHeight(Math.max(500, modalHeight + dy));
              })}
              title="Drag corner to resize both"
            />
          </div>
        </div>
      )}
    </div>
  );
}

function TipItem({ text }: { text: string }) {
  return (
    <li className="flex gap-2 text-[11px] text-cloudo-muted leading-relaxed italic border-l border-cloudo-border/40 pl-3">
      {text}
    </li>
  );
}

function HandbookSection({
  id,
  title,
  icon,
  children,
}: {
  id?: string;
  title: string;
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div id={id} className={`space-y-2 ${id ? "scroll-mt-16" : ""}`}>
      <div className="flex items-center gap-2 text-cloudo-text/80">
        <span className="text-cloudo-accent">{icon}</span>
        <span className="text-[11px] font-black uppercase tracking-widest">
          {title}
        </span>
      </div>
      <div className="text-[13px] text-cloudo-muted leading-relaxed opacity-90">
        {children}
      </div>
    </div>
  );
}

function TestScriptForm({
  onSuccess,
  onCancel,
  onError,
  initialBody,
  initialArgs,
}: {
  onSuccess: (msg: string) => void;
  onCancel: () => void;
  onError: (err: string) => void;
  initialBody: string;
  initialArgs: string;
}) {
  const STORAGE_KEY = "cloudo_test_form_data";
  const EMPTY_FORM = {
    label: "",
    script: "",
    args: "",
    body: "{}",
    capability: "",
    scriptType: "python",
  };

  const [formData, setFormData] = useState(() => {
    if (typeof window !== "undefined") {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored) {
        try {
          return { ...EMPTY_FORM, ...JSON.parse(stored) };
        } catch {
          // fall through to the seeded defaults below
        }
      }
    }
    return {
      ...EMPTY_FORM,
      body: initialBody,
      args: initialArgs,
    };
  });
  const [isBodyOpen, setIsBodyOpen] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [capabilities, setCapabilities] = useState<string[]>([]);
  const [loadingWorkers, setLoadingWorkers] = useState(true);
  const scriptInputRef = useRef<HTMLInputElement>(null);

  // Persist formData to localStorage whenever it changes
  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(formData));
  }, [formData]);

  // Fetch workers on mount
  useEffect(() => {
    const fetchWorkers = async () => {
      try {
        const response = await cloudoFetch("/api/workers");
        if (response.ok) {
          const data = await response.json();

          // Extract unique capabilities (PartitionKey)
          const uniqueCapabilities = Array.from(
            new Set(
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              (data || []).map((w: any) => w.PartitionKey).filter(Boolean),
            ),
          ) as string[];
          setCapabilities(uniqueCapabilities.sort());

          // Only set capability if not already set
          if (uniqueCapabilities.length > 0 && !formData.capability) {
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            setFormData((prev: any) => ({
              ...prev,
              capability: uniqueCapabilities[0],
            }));
          }
        }
      } catch {
        onError("Failed to fetch workers");
      } finally {
        setLoadingWorkers(false);
      }
    };
    fetchWorkers();
  }, [formData.capability, onError]);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const handleScriptUpload = (e: any) => {
    const file = e.target?.files?.[0];
    if (!file) return;

    // Auto-detect script type from file extension
    const fileName = file.name.toLowerCase();
    let detectedType = "python"; // default
    if (fileName.endsWith(".sh") || fileName.endsWith(".bash")) {
      detectedType = "shell";
    } else if (fileName.endsWith(".py") || fileName.endsWith(".python")) {
      detectedType = "python";
    }

    const reader = new FileReader();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    reader.onload = (event: any) => {
      const content = event.target?.result;
      setFormData({
        ...formData,
        label: file.name.replace(/\.[^.]+$/, ""),
        script: content,
        scriptType: detectedType,
      });
    };
    reader.readAsText(file);
  };

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const lineNumbersRef = useRef<HTMLDivElement>(null);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const handleScroll = (e: any) => {
    if (lineNumbersRef.current) {
      lineNumbersRef.current.scrollTop = e.target?.scrollTop ?? 0;
    }
  };

  const formatJson = () => {
    try {
      const formatted = JSON.stringify(JSON.parse(formData.body), null, 2);
      setFormData({ ...formData, body: formatted });
    } catch {
      onError("Invalid JSON format");
    }
  };

  const handleClear = () => {
    setFormData(EMPTY_FORM);
  };

  const handleSubmit = async () => {
    if (!formData.script.trim()) {
      onError("Script cannot be empty");
      return;
    }

    setSubmitting(true);
    try {
      if (!formData.capability) {
        onError("Please select a capability");
        setSubmitting(false);
        return;
      }

      let bodyPayload = {};
      try {
        bodyPayload = JSON.parse(formData.body);
      } catch {
        bodyPayload = {};
      }

      const payload = {
        label: formData.label,
        script: formData.script,
        args: formData.args,
        body: bodyPayload,
        capability: formData.capability,
        scriptType: formData.scriptType,
      };

      const response = await cloudoFetch("/api/dev/testrun", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        const errorData = await response
          .json()
          .catch(() => ({ error: "Unknown error" }));
        throw new Error(
          errorData.error ||
            errorData.message ||
            `Test run failed: ${response.statusText}`,
        );
      }

      const result = await response.json();
      onSuccess(`Test scheduled successfully (ID: ${result.exec_id})`);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Failed to schedule test");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form
      onSubmit={(e) => e.preventDefault()}
      className="p-8 h-full flex flex-col gap-5 w-full"
    >
      <div className="flex gap-6 flex-1 overflow-hidden">
        {/* LEFT COLUMN: SETTINGS */}
        <div className="w-80 flex flex-col gap-5 overflow-y-auto pr-2 flex-shrink-0">
          <FormField
            label="Test Name (optional)"
            icon={<HiOutlineTerminal className="w-3 h-3 text-cloudo-accent" />}
            value={formData.label}
            onChange={(e) =>
              setFormData({ ...formData, label: e.target.value })
            }
            placeholder="my-runbook"
          />

          <FormField
            label="Worker Capability"
            icon={<HiOutlineCube className="w-3 h-3 text-cloudo-accent" />}
            type="select"
            value={formData.capability}
            onChange={(e) =>
              setFormData({ ...formData, capability: e.target.value })
            }
            disabled={loadingWorkers}
            options={capabilities.map((cap) => ({ value: cap, label: cap }))}
          />

          <FormField
            label="Script Type"
            icon={<HiOutlineTerminal className="w-3 h-3 text-cloudo-accent" />}
            type="select"
            value={formData.scriptType}
            onChange={(
              e: ChangeEvent<
                HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement,
                Element
              >,
            ) => setFormData({ ...formData, scriptType: e.target.value })}
            options={[
              { value: "python", label: "Python (.py)" },
              { value: "shell", label: "Shell (.sh)" },
            ]}
          />

          <FormField
            label="Runtime Arguments"
            icon={<HiOutlineVariable className="w-3 h-3 text-cloudo-accent" />}
            value={formData.args}
            onChange={(e) => setFormData({ ...formData, args: e.target.value })}
            placeholder="--mode=dry-run --target=dev"
          />
          <p className="text-[9px] text-cloudo-muted opacity-60 -mt-1 ml-1">
            Precompilati dal Payload Simulator solo la prima volta; le tue
            modifiche qui restano salvate per i test successivi.
          </p>

          {/* COLLAPSIBLE JSON BODY */}
          <CollapsibleSection
            title="Input Payload (JSON)"
            icon={
              <HiOutlineCodeBracket
                className={`w-4 h-4 ${
                  isBodyOpen ? "text-cloudo-accent" : "text-cloudo-muted"
                }`}
              />
            }
            isOpen={isBodyOpen}
            onToggle={() => setIsBodyOpen(!isBodyOpen)}
            actions={
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  formatJson();
                }}
                className="text-[9px] font-bold text-cloudo-muted hover:text-cloudo-accent flex items-center gap-1"
              >
                <HiOutlineArrowsRightLeft className="w-3 h-3 rotate-90" />{" "}
                FORMAT_JSON
              </button>
            }
          >
            <textarea
              spellCheck={false}
              className="w-full h-32 p-3 font-mono text-[11px] bg-black/20 text-cloudo-accent outline-none border border-cloudo-border focus:border-cloudo-accent/30 resize-none"
              value={formData.body}
              onChange={(e) =>
                setFormData({ ...formData, body: e.target.value })
              }
            />
          </CollapsibleSection>
        </div>

        {/* RIGHT COLUMN: CODE EDITOR */}
        <div className="flex-1 flex flex-col gap-2 min-w-0">
          <div className="space-y-2 flex-1 flex flex-col">
            <div className="flex justify-between items-end ml-1">
              <IconLabel
                icon={
                  <HiOutlineTerminal className="w-3 h-3 text-cloudo-accent" />
                }
                text="Source Code Editor"
              />
              <button
                type="button"
                onClick={() => scriptInputRef.current?.click()}
                className="flex items-center gap-1 text-[9px] font-bold text-cloudo-accent hover:text-white transition-colors bg-cloudo-accent/5 px-2 py-0.5 border border-cloudo-accent/20"
              >
                <HiOutlineDocumentArrowUp className="w-3 h-3" /> IMPORT_FILE
              </button>
              <input
                type="file"
                ref={scriptInputRef}
                onChange={handleScriptUpload}
                className="hidden"
              />
            </div>

            <div className="relative group border border-cloudo-border bg-[#0a0a0b] focus-within:border-cloudo-accent/50 shadow-inner overflow-hidden flex-1 flex flex-col">
              {/* Line Numbers Simulation Overlay */}
              <div
                ref={lineNumbersRef}
                className="absolute left-0 top-0 bottom-0 w-10 bg-black/10 border-r border-cloudo-border/50 flex flex-col items-center pt-4 select-none pointer-events-none overflow-hidden"
              >
                {Array.from({
                  length: Math.max(formData.script.split("\n").length, 20),
                }).map((_, i) => (
                  <span
                    key={i}
                    className="text-[9px] font-mono text-cloudo-muted/60 h-6 flex items-center justify-center w-full shrink-0"
                  >
                    {i + 1}
                  </span>
                ))}
              </div>

              <textarea
                ref={textareaRef}
                required
                spellCheck={false}
                onScroll={handleScroll}
                className="w-full flex-1 p-4 pl-12 input-editor bg-transparent text-cloudo-text font-mono text-xs outline-none
                          leading-6 scrollbar-thin scrollbar-thumb-cloudo-accent/20 scrollbar-track-transparent"
                placeholder="#!/bin/bash&#10;# Scrivi qui lo script..."
                value={formData.script}
                onChange={(e) =>
                  setFormData({ ...formData, script: e.target.value })
                }
                style={{
                  tabSize: 4,
                  lineHeight: "1.5rem",
                  backgroundImage:
                    "linear-gradient(rgba(255,255,255,0.02) 1px, transparent 1px)",
                  backgroundSize: "100% 1.5rem",
                  backgroundAttachment: "local",
                }}
              />
            </div>
          </div>
        </div>
      </div>

      {/* ACTIONS */}
      <div className="flex gap-4 pt-4 border-t border-cloudo-border">
        <button
          type="button"
          onClick={onCancel}
          className="btn btn-ghost flex-1 h-11 text-[11px] font-bold uppercase tracking-[0.2em]"
        >
          Abort
        </button>
        <button
          type="button"
          onClick={handleClear}
          className="btn btn-ghost flex-1 h-11 text-[11px] font-bold uppercase tracking-[0.2em]"
        >
          Clear
        </button>
        <button
          type="submit"
          disabled={submitting}
          onClick={(e) => {
            e.preventDefault();
            handleSubmit();
          }}
          className="btn btn-primary flex-1 h-11 flex items-center justify-center gap-2 group"
        >
          <HiOutlinePlay className="w-4 h-4 group-hover:scale-125 transition-transform" />
          <span className="text-[11px] font-bold uppercase tracking-[0.2em]">
            Execute
          </span>
        </button>
      </div>
    </form>
  );
}
