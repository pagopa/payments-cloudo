"use client";

import { useState, useEffect, useRef, ChangeEvent } from "react";
import {
  HiOutlineCode,
  HiOutlineTerminal,
  HiOutlineClipboardCopy,
  HiOutlineCheck,
  HiOutlineDownload,
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
} from "react-icons/hi2";
import { cloudoFetch } from "@/lib/api";
import {
  IconLabel,
  SectionHeader,
  FormField,
  CollapsibleSection,
  IconBox,
  InfoGrid,
  ResizeHandle,
  TruncatedText,
} from "./components";

const TEMPLATES = [
  {
    id: "py-alert",
    name: "Python Alert Handler",
    lang: "python",
    description:
      "Ottimizzato per gestire allarmi da Azure Monitor o sistemi esterni.",
    code: `#!/usr/bin/env python3
"""
ClouDO Python Template: Alert Handler
Questo script processa i payload JSON inviati dai sistemi di monitoraggio.
"""
import json
import logging
import os
import sys

# Configurazione logging su stderr per visibilità in console ClouDO
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def main():
    # ClouDO passa il payload via variabile d'ambiente CLOUDO_PAYLOAD
    try:
        input_data = os.environ.get('CLOUDO_PAYLOAD', '{}')
        payload = json.loads(input_data)
    except Exception as e:
        logging.error(f"Failed to parse input: {e}")
        sys.exit(1)

    logging.info("--- CLOUDO OPERATIONAL STREAM START ---")

    # Esempio: Estrazione dati da un allarme Azure
    essentials = payload.get('data', {}).get('essentials', {})
    alert_rule = essentials.get('alertRule', 'Unknown Rule')
    severity = essentials.get('severity', 'N/A')

    logging.info(f"SIGNAL_DETECTED: {alert_rule}")
    logging.info(f"SEVERITY_LEVEL: {severity}")

    # --- TUA LOGICA QUI ---
    # Esempio: Ripristino servizio, pulizia log, etc.

    logging.info("AUTOMATION_LOGIC_EXECUTED")
    print("SUCCESS: Runbook completed successfully")

if __name__ == "__main__":
    main()`,
  },
  {
    id: "sh-manual",
    name: "Bash Manual Script",
    lang: "bash",
    description: "Script robusto per esecuzioni manuali veloci e diagnostica.",
    code: `#!/bin/bash
# ClouDO Bash Template: Manual Execution
# Utilizzato per task ad-hoc sui nodi del cluster.

echo "--- CLOUDO SYSTEM DIAGNOSTICS ---"
echo "NODE_IDENTIFIER: $(hostname)"
echo "EXECUTION_TIME: $(date)"
echo "OPERATOR_CONTEXT: $USER"

# Il payload JSON è disponibile via variabile d'ambiente CLOUDO_PAYLOAD
PAYLOAD=$CLOUDO_PAYLOAD

echo "RAW_PAYLOAD_RECEIVED: $PAYLOAD"

# Funzione per simulare un'operazione tecnica
perform_health_check() {
    echo "STATUS: VERIFYING_FILESYSTEM..."
    df -h | grep '^/'
    sleep 1
    echo "STATUS: CHECKING_RESOURCES..."
    free -m
}

perform_health_check

echo "--------------------------------"
echo "RESULT: COMPLIANT"
echo "ClouDO_EXEC_STATUS: OK"
exit 0`,
  },
  {
    id: "py-minimal",
    name: "Python Minimal",
    lang: "python",
    description:
      "Template essenziale per script personalizzati ad alte prestazioni.",
    code: `#!/usr/bin/env python3
import json
import os
import sys

# ClouDO Data Ingestion
payload = json.loads(os.environ.get('CLOUDO_PAYLOAD', '{}'))

# Logic Block
def run():
    print(f"ClouDO Engine v4.0 Active")
    print(f"Processing payload: {payload}")
    # Insert code here

if __name__ == "__main__":
    run()`,
  },
];

export default function StudioPage() {
  const [selectedTemplate, setSelectedTemplate] = useState(TEMPLATES[0]);
  const [copied, setCopied] = useState(false);
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
  const [payloadInput, setPayloadInput] = useState(
    JSON.stringify(
      {
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
      null,
      2,
    ),
  );
  const [runArgsInput, setRunArgsInput] = useState("--verbose --timeout 30");
  const [parsedEnv, setParsedEnv] = useState<Record<string, string>>({});

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
      const env: Record<string, string> = {};

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const getLower = (obj: any, key: string): any => {
        if (!obj || typeof obj !== "object") return undefined;
        const lowerKey = key.toLowerCase();
        const foundKey = Object.keys(obj).find(
          (k) => k.toLowerCase() === lowerKey,
        );
        return foundKey ? obj[foundKey] : undefined;
      };

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
        candidates.push(...alertTargetIds.filter((x) => typeof x === "string"));
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
          getLower(ctx, "resourceid") || getLower(labels, "resourceid") || null;
      }

      if (resource_id) env["RESOURCE_ID"] = resource_id;
      if (resource_group) env["RESOURCE_RG"] = resource_group;
      if (resource_name) env["RESOURCE_NAME"] = resource_name;

      // Kubernetes fields
      const namespace =
        getLower(labels, "namespace") ||
        getLower(labels, "kubernetes_namespace") ||
        getLower(annotations, "namespace") ||
        getLower(annotations, "kubernetes_namespace");
      if (namespace) env["K8S_NAMESPACE"] = String(namespace);

      const pod =
        getLower(labels, "pod") ||
        getLower(labels, "kubernetes_pod_name") ||
        getLower(annotations, "pod") ||
        getLower(annotations, "kubernetes_pod_name");
      if (pod) env["K8S_POD"] = String(pod);

      const deployment =
        getLower(labels, "deployment") ||
        getLower(labels, "kubernetes_deployment") ||
        getLower(annotations, "deployment") ||
        getLower(annotations, "kubernetes_deployment");
      if (deployment) env["K8S_DEPLOYMENT"] = String(deployment);

      const hpa =
        getLower(labels, "horizontalpodautoscaler") ||
        getLower(labels, "kubernetes_horizontalpodautoscaler") ||
        getLower(annotations, "horizontalpodautoscaler") ||
        getLower(annotations, "kubernetes_horizontalpodautoscaler");
      if (hpa) env["K8S_HPA"] = String(hpa);

      let job =
        getLower(labels, "kubernetes_job_name") ||
        getLower(annotations, "kubernetes_job_name") ||
        getLower(labels, "job_name") ||
        getLower(annotations, "job_name");

      if (!job) {
        const cand = getLower(labels, "job") || getLower(annotations, "job");
        if (cand && cand !== "kube-state-metrics") {
          job = cand;
        }
      }
      if (job) env["K8S_JOB"] = String(job);

      // 4. Essentials
      const monitorCondition = getLower(essentials, "monitorcondition");
      if (monitorCondition) env["MONITOR_CONDITION"] = String(monitorCondition);

      const severity = getLower(essentials, "severity");
      if (severity) env["SEVERITY"] = String(severity);

      // 5. Schema IDs (simulato)
      const alertId = getLower(essentials, "alertid");
      const alertRule = getLower(essentials, "alertrule");
      const schemaCandidates: string[] = [];
      if (alertId)
        schemaCandidates.push(String(alertId).split("/").pop() || "");
      if (alertRule)
        schemaCandidates.push(String(alertRule).split("/").pop() || "");
      if (schemaCandidates.length > 0)
        env["SCHEMA_ID // ALERT_ID"] = Array.from(
          new Set(schemaCandidates),
        ).join(",");

      env["CLOUDO_PAYLOAD"] = JSON.stringify(parsed);
      if (JSON.stringify(parsedEnv) !== JSON.stringify(env)) {
        // eslint-disable-next-line react-hooks/set-state-in-effect
        setParsedEnv(env);
      }
    } catch {}
  }, [payloadInput, parsedEnv]);

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

  const handleCopy = () => {
    navigator.clipboard.writeText(selectedTemplate.code);
    setCopied(true);
    addNotification("success", "Code copied to clipboard");
    setTimeout(() => setCopied(false), 2000);
  };

  const handleTemplateSelect = (tpl: (typeof TEMPLATES)[0]) => {
    setSelectedTemplate(tpl);
  };

  const handleDownload = () => {
    const element = document.createElement("a");
    const file = new Blob([selectedTemplate.code], { type: "text/plain" });
    element.href = URL.createObjectURL(file);
    element.download =
      selectedTemplate.lang === "python" ? "runbook.py" : "runbook.sh";
    document.body.appendChild(element);
    element.click();
    addNotification("success", "Runbook file exported");
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
        {/* Left Sidebar: Templates */}
        <div className="w-80 border-r border-cloudo-border bg-cloudo-accent/5 overflow-y-auto p-6 space-y-6 shrink-0">
          <div className="flex items-center gap-2 mb-3">
            <HiOutlineDocumentText className="text-cloudo-accent w-4 h-4" />
            <h2 className="text-[11px] font-black uppercase tracking-[0.2em] text-cloudo-text">
              Reference Blueprints
            </h2>
          </div>
          <div className="space-y-3">
            {TEMPLATES.map((tpl) => (
              <button
                key={tpl.id}
                onClick={() => handleTemplateSelect(tpl)}
                className={`w-full text-left p-4 border transition-all relative group ${
                  selectedTemplate.id === tpl.id
                    ? "bg-cloudo-accent/5 border-cloudo-accent/40"
                    : "bg-cloudo-panel border-cloudo-border hover:border-cloudo-muted/70"
                }`}
              >
                <div className="flex items-center gap-3 mb-2">
                  {tpl.lang === "python" ? (
                    <HiOutlineCode className="text-cloudo-accent w-4 h-4" />
                  ) : (
                    <HiOutlineTerminal className="text-cloudo-accent w-4 h-4" />
                  )}
                  <span className="text-[11px] font-black text-cloudo-text uppercase tracking-widest">
                    {tpl.name}
                  </span>
                </div>
                <p className="text-[11px] text-cloudo-muted leading-relaxed opacity-60 group-hover:opacity-100">
                  {tpl.description}
                </p>
                {selectedTemplate.id === tpl.id && (
                  <div className="absolute left-[-1px] top-0 w-[2px] h-full bg-cloudo-accent" />
                )}
              </button>
            ))}
          </div>

          <div className="pt-8 space-y-4">
            <div className="flex items-center gap-2 mb-3">
              <HiOutlineLightBulb className="text-cloudo-accent w-4 h-4" />
              <h2 className="text-[11px] font-black uppercase tracking-[0.2em] text-cloudo-text">
                Quick Tips
              </h2>
            </div>
            <div className="space-y-4">
              <TipItem text="Usa sempre stderr per i log di sistema per non inquinare l'output dei dati." />
              <TipItem text="Gestisci esplicitamente gli exit code per permettere al Worker di capire l'esito." />
              <TipItem text="I parametri vengono passati sia via CLOUDO_PAYLOAD che variabili d'ambiente singole." />
              <TipItem text="In caso di allarmi inerenti ad AKS ClouDO si loggerà nel cluster con la proprio SA staccando un token valido per 10 minuti, nel contesto del namespace coinvolto." />
            </div>
          </div>
        </div>

        {/* Center: Handbook Content */}
        <div className="flex-1 overflow-y-auto bg-cloudo-dark custom-scrollbar">
          <div className="max-w-4xl mx-auto p-12 space-y-12">
            <section className="space-y-6">
              <div className="flex items-center gap-3 border-b border-cloudo-border pb-4">
                <HiOutlinePlay className="text-cloudo-accent w-6 h-6" />
                <h2 className="text-xl font-black uppercase tracking-widest text-cloudo-text">
                  Execution & Payload Simulator
                </h2>
              </div>
              <p className="text-sm text-cloudo-muted leading-relaxed">
                Utilizza questa area per simulare come ClouDO interpreterà il
                tuo payload e come verrà composto il comando di esecuzione
                finale.
              </p>

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
                <label className="text-[11px] font-black uppercase tracking-widest text-cloudo-text flex items-center gap-2 block">
                  <HiOutlineVariable className="text-cloudo-accent" /> ClouDO
                  Parsed Environment Variables
                </label>
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

            <section className="space-y-6">
              <div className="flex items-center gap-3 border-b border-cloudo-border pb-4">
                <HiOutlineInformationCircle className="text-cloudo-accent w-6 h-6" />
                <h2 className="text-xl font-black uppercase tracking-widest text-cloudo-text">
                  Manuale Sviluppatore Runbook
                </h2>
              </div>
              <p className="text-sm text-cloudo-muted leading-relaxed">
                Benvenuto nella guida tecnica di ClouDO. In questa sezione
                troverai tutto il necessario per costruire runbook robusti,
                sicuri e integrati correttamente con l&apos;orchestratore.
              </p>
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

            <HandbookSection
              title="Common Environment Variables"
              icon={<HiOutlineVariable />}
            >
              <div className="grid grid-cols-2 gap-x-12 gap-y-4 mt-4">
                <VarItem
                  name="RESOURCE_ID"
                  desc="Azure resource ID (/subscriptions/...)"
                />
                <VarItem
                  name="RESOURCE_RG"
                  desc="Resource Group of the resource."
                />
                <VarItem name="RESOURCE_NAME" desc="Final resource name." />
                <VarItem
                  name="MONITOR_CONDITION"
                  desc="Alert state (Fired/Resolved)."
                />
                <VarItem name="SEVERITY" desc="Severity level (Sev0-4)." />
                <VarItem
                  name="SCHEMA_ID // ALERT_ID"
                  desc="Identificativo dello schema di allarme."
                />
                <VarItem
                  name="K8S_NAMESPACE"
                  desc="Namespace Kubernetes (se presente)."
                />
                <VarItem name="K8S_POD" desc="Nome del Pod (se presente)." />
                <VarItem
                  name="K8S_DEPLOYMENT"
                  desc="Nome del Deployment (se presente)."
                />
                <VarItem name="K8S_JOB" desc="Nome del Job (se presente)." />
                <VarItem
                  name="K8S_HPA"
                  desc="Horizontal Pod Autoscaler (se presente)."
                />
                <VarItem
                  name="CLOUDO_ENVIRONMENT"
                  desc="L'environment di riferimento cloudo (dev, uat, prod)."
                />
                <VarItem
                  name="CLOUDO_ENVIRONMENT_SHORT"
                  desc="L'environment di riferimento cloudo abbreviato (d, u, p)."
                />
                <VarItem
                  name="CLOUDO_PAYLOAD"
                  desc="L'intero payload JSON originale."
                />
              </div>
            </HandbookSection>

            <section className="space-y-6 bg-cloudo-panel border border-cloudo-border p-8">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <HiOutlineCode className="text-cloudo-accent w-5 h-5" />
                  <h3 className="text-sm font-black uppercase tracking-widest text-cloudo-text">
                    Blueprint Selezionato: {selectedTemplate.name}
                  </h3>
                </div>
                <div className="flex gap-4">
                  <button
                    onClick={handleDownload}
                    className="text-[10px] uppercase font-bold text-cloudo-muted hover:text-cloudo-accent flex items-center gap-1 transition-colors"
                  >
                    <HiOutlineDownload className="w-3 h-3" /> Export
                  </button>
                  <button
                    onClick={handleCopy}
                    className="text-[10px] uppercase font-bold text-cloudo-muted hover:text-cloudo-accent flex items-center gap-1 transition-colors"
                  >
                    {copied ? (
                      <HiOutlineCheck className="w-3 h-3" />
                    ) : (
                      <HiOutlineClipboardCopy className="w-3 h-3" />
                    )}
                    {copied ? "Copied" : "Copy"}
                  </button>
                </div>
              </div>
              <div className="bg-cloudo-dark/50 border border-cloudo-border/50 p-4 rounded overflow-hidden">
                <pre className="text-xs text-cloudo-muted overflow-x-auto custom-scrollbar leading-5">
                  <code>{selectedTemplate.code}</code>
                </pre>
              </div>
            </section>

            <HandbookSection
              title="Deployment Workflow"
              icon={<HiOutlineDocumentText />}
            >
              <p>Per rendere operativo il tuo runbook:</p>
              <ol className="list-decimal list-inside mt-4 space-y-2 text-cloudo-muted/80">
                <li>
                  Salva lo script nel path configurato sul Worker (es:{" "}
                  <code className="text-cloudo-text">src/cloudo/runbooks/</code>
                  ).
                </li>
                <li>
                  Assicurati che lo script abbia i permessi di esecuzione (
                  <code className="text-cloudo-text">chmod +x</code>).
                </li>
                <li>
                  Crea o aggiorna lo Schema nel Registry puntando al file
                  creato.
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
                Script Debugger // Live_Test_Environment
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
  title,
  icon,
  children,
}: {
  title: string;
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-cloudo-text/80">
        <span className="text-cloudo-accent">{icon}</span>
        <span className="text-[11px] font-black uppercase tracking-widest">
          {title}
        </span>
      </div>
      <div className="text-[11px] text-cloudo-muted leading-relaxed font-bold opacity-80">
        {children}
      </div>
    </div>
  );
}

function VarItem({ name, desc }: { name: string; desc: string }) {
  return (
    <div className="flex flex-col gap-0.5 border-b border-cloudo-border/20 pb-2">
      <code className="text-cloudo-accent text-[11px] font-black">{name}</code>
      <span className="text-[10px] text-cloudo-muted opacity-60 tracking-tighter">
        {desc}
      </span>
    </div>
  );
}

function TestScriptForm({
  onSuccess,
  onCancel,
  onError,
}: {
  onSuccess: (msg: string) => void;
  onCancel: () => void;
  onError: (err: string) => void;
}) {
  const STORAGE_KEY = "cloudo_test_form_data";

  const [formData, setFormData] = useState(() => {
    if (typeof window !== "undefined") {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved) {
        try {
          return JSON.parse(saved);
        } catch {
          return {
            script: "",
            args: "",
            body: "{}",
            capability: "",
            scriptType: "python",
          };
        }
      }
    }
    return {
      script: "",
      args: "",
      body: "{}",
      capability: "",
      scriptType: "python",
    };
  });
  const [isBodyOpen, setIsBodyOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [workers, setWorkers] = useState<any[]>([]);
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
          setWorkers(Array.isArray(data) ? data : []);

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
      } catch (err) {
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
      setFormData({ ...formData, script: content, scriptType: detectedType });
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
    } catch (err) {
      onError("Invalid JSON format");
    }
  };

  const handleClear = () => {
    setFormData({
      script: "",
      args: "",
      body: "{}",
      capability: "",
      scriptType: "python",
    });
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
            placeholder="--mode=dry-run --target=prod"
          />

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
                className="absolute left-0 top-0 bottom-0 w-10 bg-black/40 border-r border-cloudo-border/50 flex flex-col items-center pt-4 select-none pointer-events-none overflow-hidden"
              >
                {Array.from({
                  length: Math.max(formData.script.split("\n").length, 20),
                }).map((_, i) => (
                  <span
                    key={i}
                    className="text-[9px] font-mono text-cloudo-muted/30 h-6 flex items-center justify-center w-full shrink-0"
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
                className="w-full flex-1 p-4 pl-12 font-mono text-xs bg-transparent text-cloudo-text outline-none
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

              {/* Resize Indicator */}
              <div className="absolute bottom-1 right-1 pointer-events-none opacity-30 group-hover:opacity-100 transition-opacity">
                <svg
                  width="10"
                  height="10"
                  viewBox="0 0 10 10"
                  className="text-cloudo-muted"
                >
                  <line
                    x1="10"
                    y1="0"
                    x2="0"
                    y2="10"
                    stroke="currentColor"
                    strokeWidth="1"
                  />
                  <line
                    x1="10"
                    y1="5"
                    x2="5"
                    y2="10"
                    stroke="currentColor"
                    strokeWidth="1"
                  />
                </svg>
              </div>
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
