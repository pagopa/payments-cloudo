"use client";

import { useParams } from "next/navigation";
import { SchemaConfigPage } from "../components/SchemaConfigPage";

export default function EditSchemaPage() {
  const params = useParams<{ id: string }>();
  const id = decodeURIComponent(params.id);

  return <SchemaConfigPage schemaId={id} />;
}
