"use client";

import React from "react";
import { HiOutlineChevronDown, HiOutlineChevronUp } from "react-icons/hi";

/**
 * Icon + Label Component
 * Reusable label with optional icon
 */
export function IconLabel({
  icon: Icon,
  text,
  className = "",
}: {
  icon?: React.ReactNode;
  text: string;
  className?: string;
}) {
  return (
    <label
      className={`text-[10px] font-black uppercase tracking-[0.2em] text-cloudo-muted ml-1 flex items-center gap-2 ${className}`}
    >
      {Icon && Icon}
      {text}
    </label>
  );
}

/**
 * Section Header Component
 * Used for major section titles with icon
 */
export function SectionHeader({
  title,
  icon: Icon,
}: {
  title: string;
  icon: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-3 border-b border-cloudo-border pb-4">
      <div className="text-cloudo-accent w-6 h-6">{Icon}</div>
      <h2 className="text-xl font-black uppercase tracking-widest text-cloudo-text">
        {title}
      </h2>
    </div>
  );
}

/**
 * Form Field Component
 * Unified input/select/textarea wrapper with label and icon
 */
export function FormField({
  label,
  icon: Icon,
  type = "text",
  value,
  onChange,
  placeholder,
  options,
  disabled = false,
  className = "",
  required = false,
}: {
  label: string;
  icon?: React.ReactNode;
  type?: "text" | "select" | "textarea";
  value: string;
  onChange: (
    e: React.ChangeEvent<
      HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement
    >,
  ) => void;
  placeholder?: string;
  options?: Array<{ value: string; label: string }>;
  disabled?: boolean;
  className?: string;
  required?: boolean;
}) {
  return (
    <div className="space-y-2">
      <IconLabel icon={Icon} text={label} />
      {type === "select" ? (
        <select
          value={value}
          onChange={onChange}
          disabled={disabled}
          required={required}
          className={`select h-10 w-full ${className}`}
        >
          <option value="">Select an option...</option>
          {options?.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      ) : type === "textarea" ? (
        <textarea
          value={value}
          onChange={onChange}
          placeholder={placeholder}
          disabled={disabled}
          required={required}
          className={`w-full h-32 p-3 font-mono text-[11px] bg-black/20 text-cloudo-accent outline-none border border-cloudo-border focus:border-cloudo-accent/30 resize-none ${className}`}
        />
      ) : (
        <input
          type="text"
          value={value}
          onChange={onChange}
          placeholder={placeholder}
          disabled={disabled}
          required={required}
          className={`input h-10 font-mono text-xs w-full ${className}`}
        />
      )}
    </div>
  );
}

/**
 * Collapsible Section Component
 * Section that can be toggled open/closed with header
 */
export function CollapsibleSection({
  title,
  icon: Icon,
  isOpen,
  onToggle,
  children,
  actions,
  className = "",
}: {
  title: string;
  icon?: React.ReactNode;
  isOpen: boolean;
  onToggle: () => void;
  children: React.ReactNode;
  actions?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`border border-cloudo-border transition-all ${
        isOpen ? "bg-cloudo-panel/60" : "bg-cloudo-panel/20"
      } ${className}`}
    >
      <div
        className="flex items-center justify-between px-4 py-3 cursor-pointer hover:bg-cloudo-accent/5"
        onClick={onToggle}
      >
        <div className="flex items-center gap-3">
          {Icon && Icon}
          <span className="text-[10px] font-black uppercase tracking-[0.2em] text-cloudo-text">
            {title}
          </span>
        </div>
        <div className="flex items-center gap-4">
          {actions && isOpen && actions}
          {isOpen ? (
            <HiOutlineChevronUp className="w-4 h-4" />
          ) : (
            <HiOutlineChevronDown className="w-4 h-4" />
          )}
        </div>
      </div>

      {isOpen && (
        <div className="p-4 border-t border-cloudo-border animate-in fade-in zoom-in-95 duration-200">
          {children}
        </div>
      )}
    </div>
  );
}

/**
 * Icon Box Component
 * Small container with icon inside
 */
export function IconBox({
  icon: Icon,
  size = "w-5 h-5",
  className = "",
}: {
  icon: React.ReactNode;
  size?: string;
  className?: string;
}) {
  return (
    <div
      className={`p-2 bg-cloudo-accent/5 border border-cloudo-accent/20 shrink-0 flex items-center justify-center ${className}`}
    >
      <div className={`text-cloudo-accent ${size}`}>{Icon}</div>
    </div>
  );
}

/**
 * Info Grid Component
 * Display key-value pairs in a grid layout
 */
export function InfoGrid({
  items,
  columns = 2,
  className = "",
}: {
  items: Array<{
    key: string;
    value: React.ReactNode;
  }>;
  columns?: number;
  className?: string;
}) {
  const gridClass =
    columns === 2
      ? "grid-cols-2"
      : columns === 3
        ? "grid-cols-3"
        : "grid-cols-1";

  return (
    <div className={`grid ${gridClass} gap-4 ${className}`}>
      {items.map((item, idx) => (
        <div key={idx} className="space-y-1">
          <div className="text-[9px] font-mono text-cloudo-muted uppercase">
            {item.key}
          </div>
          <div className="text-[11px] font-mono text-cloudo-text break-words">
            {item.value || "-"}
          </div>
        </div>
      ))}
    </div>
  );
}

/**
 * Modal Resize Handle Component
 * Provides visual resize handle with proper event handling
 */
export function ResizeHandle({
  direction = "se",
  onResize,
  title = "Drag to resize",
  showIcon = true,
}: {
  direction?: "e" | "s" | "se";
  onResize: (e: React.MouseEvent<HTMLDivElement>) => void;
  title?: string;
  showIcon?: boolean;
}) {
  const getClasses = () => {
    switch (direction) {
      case "e":
        return "absolute right-0 top-0 bottom-0 w-1 cursor-e-resize bg-cloudo-accent/0 hover:bg-cloudo-accent/60";
      case "s":
        return "absolute bottom-0 left-0 right-0 h-1 cursor-s-resize bg-cloudo-accent/0 hover:bg-cloudo-accent/60";
      case "se":
        return "absolute bottom-0 right-0 w-4 h-4 cursor-se-resize bg-gradient-to-tl from-cloudo-accent/60 to-transparent hover:from-cloudo-accent flex items-center justify-center group";
      default:
        return "";
    }
  };

  return (
    <div
      onMouseDown={onResize}
      className={`transition-colors ${getClasses()}`}
      title={title}
    >
      {direction === "se" && showIcon && (
        <svg
          width="10"
          height="10"
          viewBox="0 0 10 10"
          className="text-white/80 group-hover:text-white"
        >
          <line
            x1="8"
            y1="2"
            x2="2"
            y2="8"
            stroke="currentColor"
            strokeWidth="1.5"
          />
          <line
            x1="8"
            y1="5"
            x2="5"
            y2="8"
            stroke="currentColor"
            strokeWidth="1.5"
          />
        </svg>
      )}
    </div>
  );
}

/**
 * Text Truncation Helper Component
 * Displays truncated text with ellipsis
 */
export function TruncatedText({
  text,
  maxLength = 50,
  className = "",
}: {
  text: string;
  maxLength?: number;
  className?: string;
}) {
  const displayText =
    text && text.length > maxLength
      ? `${text.substring(0, maxLength)}...`
      : text;

  return (
    <span className={className} title={text}>
      {displayText}
    </span>
  );
}
