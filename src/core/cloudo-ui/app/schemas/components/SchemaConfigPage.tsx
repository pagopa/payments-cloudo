"use client";

import React, { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  HiOutlineArrowLeft,
  HiOutlineExclamationCircle,
  HiOutlineInformationCircle,
  HiOutlineSave,
  HiOutlineX,
} from "react-icons/hi";
import { MdOutlineSchema } from "react-icons/md";
import { cloudoFetch } from "@/lib/api";
import { Schema } from "../types";
import { SchemaForm, SCHEMA_FORM_ID } from "./SchemaForm";

interface SchemaConfigPageProps {
  // When omitted, the page operates in "create" mode.
  schemaId?: string;
}

const isTerraformSchema = (tags?: string) =>
  tags
    ?.split(",")
    .map((t) => t.trim().toLowerCase())
    .includes("terraform");

export function SchemaConfigPage({ schemaId }: SchemaConfigPageProps) {
  const router = useRouter();
  const isCreate = !schemaId;

  const [user, setUser] = useState<{ role: string; team?: string } | null>(
    null,
  );
  const [schema, setSchema] = useState<Schema | null>(null);
  const [availableRunbooks, setAvailableRunbooks] = useState<string[]>([]);
  const [availableWorkers, setAvailableWorkers] = useState<string[]>([]);
  const [loading, setLoading] = useState(!isCreate);
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const goToList = (notice?: {
    type: "success" | "error";
    message: string;
  }) => {
    if (!notice) {
      router.push("/schemas");
      return;
    }
    const params = new URLSearchParams({
      notice: notice.type,
      msg: notice.message,
    });
    router.push(`/schemas?${params.toString()}`);
  };

  useEffect(() => {
    const userData = localStorage.getItem("cloudo_user");
    if (userData) {
      try {
        setUser(JSON.parse(userData));
      } catch (e) {
        console.error("Failed to parse user data", e);
      }
    }
  }, []);

  useEffect(() => {
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

    fetchAvailableRunbooks();
    fetchWorkers();
  }, []);

  useEffect(() => {
    if (isCreate) return;

    let cancelled = false;
    const fetchSchema = async () => {
      setLoading(true);
      try {
        const res = await cloudoFetch(`/schemas`);
        const data = await res.json();
        const schemasList: Schema[] = Array.isArray(data) ? data : [];
        const found = schemasList.find(
          (s) => s.id === schemaId || s.RowKey === schemaId,
        );
        if (cancelled) return;
        if (found) {
          setSchema(found);
        } else {
          setNotFound(true);
        }
      } catch {
        if (!cancelled) setNotFound(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    fetchSchema();
    return () => {
      cancelled = true;
    };
  }, [isCreate, schemaId]);

  const isViewer = user?.role === "VIEWER";
  const ownsSchema =
    isCreate ||
    user?.role === "ADMIN" ||
    (schema?.team || "default") === (user?.team || "default");
  const canEdit =
    !isViewer &&
    ownsSchema &&
    (user?.role === "ADMIN" || user?.role === "OPERATOR") &&
    !isTerraformSchema(schema?.tags);

  const mode: "create" | "edit" | "view" = isCreate
    ? "create"
    : canEdit
      ? "edit"
      : "view";

  const title = isCreate
    ? "Register New Schema"
    : mode === "edit"
      ? "Update Configuration"
      : "View Configuration";

  const subtitle = isCreate
    ? "System Inventory // NEW_ASSET"
    : `System Inventory // ${schemaId}`;

  const showContent = isCreate || (!loading && !notFound);

  return (
    <div className="flex flex-col h-full bg-cloudo-dark text-cloudo-text font-mono selection:bg-cloudo-accent/30">
      {/* Top Bar */}
      <div className="flex items-center justify-between px-8 py-4 border-b border-cloudo-border bg-cloudo-panel sticky top-0 z-20">
        <div className="flex items-center gap-4 shrink-0 min-w-0">
          <button
            onClick={() => goToList()}
            className="p-2 border border-cloudo-border text-cloudo-muted hover:text-cloudo-accent hover:border-cloudo-accent/40 transition-all shrink-0"
            title="Back to list"
          >
            <HiOutlineArrowLeft className="w-4 h-4" />
          </button>
          <div className="p-2 bg-cloudo-accent/5 border border-cloudo-accent/20 shrink-0">
            <MdOutlineSchema className="text-cloudo-accent w-5 h-5" />
          </div>
          <div className="min-w-0">
            <h1 className="text-sm font-black tracking-[0.2em] text-cloudo-text uppercase truncate">
              {title}
            </h1>
            <p className="text-[11px] text-cloudo-muted font-bold uppercase tracking-[0.3em] opacity-70 truncate">
              {subtitle}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-4 shrink-0">
          <button
            onClick={() => goToList()}
            className="btn btn-ghost h-10 px-4 flex items-center gap-2"
          >
            Back to List
          </button>
          {mode !== "view" && showContent && (
            <button
              type="submit"
              form={SCHEMA_FORM_ID}
              disabled={submitting}
              className="btn btn-primary h-10 px-6 flex items-center gap-2"
            >
              <HiOutlineSave className="w-4 h-4" />
              {submitting
                ? "Saving..."
                : isCreate
                  ? "Register Schema"
                  : "Save Changes"}
            </button>
          )}
          {mode === "view" && showContent && (
            <div className="flex items-center gap-2 px-4 py-2 bg-cloudo-accent/5 border border-cloudo-accent/20 text-cloudo-muted text-[10px] font-black uppercase tracking-widest">
              <HiOutlineInformationCircle className="w-4 h-4 text-cloudo-accent" />
              READ_ONLY_ACCESS
            </div>
          )}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto p-8">
        <div className="w-full space-y-6">
          {error && (
            <div className="flex items-center gap-3 px-5 py-3 border border-cloudo-err/40 bg-cloudo-err/10 text-cloudo-err">
              <HiOutlineExclamationCircle className="w-4 h-4 shrink-0" />
              <span className="text-[11px] font-bold uppercase tracking-widest flex-1">
                {error}
              </span>
              <button
                onClick={() => setError(null)}
                className="p-1 hover:opacity-70 transition-opacity"
              >
                <HiOutlineX className="w-3.5 h-3.5" />
              </button>
            </div>
          )}

          {!isCreate && loading ? (
            <div className="py-32 text-center text-cloudo-muted italic animate-pulse uppercase tracking-[0.5em] font-black opacity-50">
              Loading Schema Configuration...
            </div>
          ) : !isCreate && notFound ? (
            <div className="py-32 text-center text-sm font-black uppercase tracking-[0.5em] opacity-40 italic border border-cloudo-border bg-cloudo-panel">
              SCHEMA_NOT_FOUND
            </div>
          ) : (
            <SchemaForm
              initialData={schema}
              mode={mode}
              availableRunbooks={availableRunbooks}
              availableWorkers={availableWorkers}
              onSuccess={(message) => {
                goToList({ type: "success", message });
              }}
              onError={(message) => setError(message)}
              onSubmittingChange={setSubmitting}
            />
          )}
        </div>
      </div>
    </div>
  );
}
