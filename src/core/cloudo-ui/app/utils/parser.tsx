import { useState } from "react";
import { HiOutlineTrash } from "react-icons/hi";
import { cloudoFetch } from "@/lib/api";

export function parseRunbookIntoCells(
  code: string,
): { heading: string | null; code: string }[] {
  if (!code) return [{ heading: null, code: "No content available." }];

  const lines = code.split("\n");
  const cells: { heading: string | null; code: string }[] = [];
  let current: { heading: string | null; lines: string[] } = {
    heading: null,
    lines: [],
  };

  for (const line of lines) {
    const trimmed = line.trim();
    // Match "# Heading" — single hash followed by a space and text
    const isHeading = /^#\s+\S/.test(trimmed) && !trimmed.startsWith("//");
    if (isHeading) {
      if (current.lines.some((l) => l.trim() !== "")) {
        cells.push({
          heading: current.heading,
          code: current.lines.join("\n").replace(/^\n+|\n+$/g, ""),
        });
      }
      current = { heading: trimmed.slice(2).trim(), lines: [] };
    } else {
      current.lines.push(line);
    }
  }

  if (current.lines.some((l) => l.trim() !== "")) {
    cells.push({
      heading: current.heading,
      code: current.lines.join("\n").replace(/^\n+|\n+$/g, ""),
    });
  }

  return cells.length > 0 ? cells : [{ heading: null, code: code }];
}
