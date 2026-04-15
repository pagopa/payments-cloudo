import { useState } from "react";
import { HiOutlineTrash } from "react-icons/hi";
import { cloudoFetch } from "@/lib/api";

export function parseRunbookIntoCells(
  code: string,
): { heading: string | null; code: string }[] {
  if (!code) return [{ heading: null, code: "No content available." }];

  const lines = code.split("\n");
  const cells: { heading: string | null; code: string }[] = [];
  let currentHeading: string | null = null;
  let currentLines: string[] = [];

  const pushCell = () => {
    const codeContent = currentLines.join("\n").trim();
    if (codeContent !== "" || currentHeading !== null) {
      cells.push({
        heading: currentHeading,
        code: codeContent || "",
      });
    }
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();

    if (trimmed === "#" || /^#\s+/.test(trimmed)) {
      if (currentLines.some((l) => l.trim() !== "")) {
        pushCell();
        currentLines = [];
      }

      if (trimmed === "#") {
        let j = i + 1;
        while (j < lines.length && lines[j].trim() === "#") j++;

        if (j < lines.length && /^#\s+/.test(lines[j].trim())) {
          currentHeading = lines[j].trim().replace(/^#\s+/, "");
          i = j;
        } else {
          currentHeading = currentHeading || "Sezione";
        }
      } else {
        currentHeading = trimmed.replace(/^#\s+/, "");
      }

      if (i + 1 < lines.length && lines[i + 1].trim() === "#") {
        i++;
      }
      continue;
    }

    currentLines.push(line);
  }

  pushCell();

  return cells.length > 0 ? cells : [{ heading: null, code: code }];
}
