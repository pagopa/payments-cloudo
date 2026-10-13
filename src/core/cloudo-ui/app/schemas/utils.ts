import type { Schema } from "./types";

export function normalizeSchema(
  schema: Omit<Schema, "oncall" | "aks_login_required"> & {
    oncall?: unknown;
    aks_login_required?: unknown;
  },
): Schema {
  return {
    ...schema,
    oncall:
      schema.oncall === true ||
      (typeof schema.oncall === "string" &&
        schema.oncall.trim().toLowerCase() === "true"),
    aks_login_required:
      schema.aks_login_required === true ||
      (typeof schema.aks_login_required === "string" &&
        schema.aks_login_required.trim().toLowerCase() === "true"),
  };
}
