"use client";

import { useEffect, useRef, useState } from "react";
import { shouldIncludeAllTeams } from "@/lib/api";

export type LogStreamStatus =
  | "idle"
  | "connecting"
  | "live"
  | "ended"
  | "unavailable";

export interface LogStreamState<T> {
  status: LogStreamStatus;
  // Latest execution row (without Log) pushed by the orchestrator.
  entry: Partial<T> | null;
  // Full log text, rebuilt from "replace"/"append" messages.
  log: string | null;
}

interface InternalState<T> extends LogStreamState<T> {
  key: string | null;
}

type StreamMessage<T> =
  | { type: "entry"; entry: Partial<T> }
  | { type: "replace"; log: string }
  | { type: "append"; data: string }
  | { type: "end"; status: string }
  | { type: "upsert"; items: T[] }
  | { type: "ping" }
  | { type: "error"; error: string };

const IDLE: InternalState<never> = {
  key: null,
  status: "idle",
  entry: null,
  log: null,
};
const MAX_RETRIES = 3;
// Server close codes that a reconnect cannot fix (auth, team, not found).
const FATAL_CLOSE_CODES = new Set([4401, 4403, 4404, 4408]);

let baseUrlPromise: Promise<string> | null = null;

// The Next.js API proxy cannot upgrade WebSockets, so the browser connects to
// the orchestrator directly
function resolveStreamBaseUrl(): Promise<string> {
  if (!baseUrlPromise) {
    baseUrlPromise = fetch("/api/config")
      .then((res) => (res.ok ? res.json() : {}))
      .catch(() => ({}))
      .then((cfg: { streamWsUrl?: string }) => {
        if (cfg.streamWsUrl) return cfg.streamWsUrl.replace(/\/+$/, "");
        const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
        return `${proto}//${window.location.host}/api/ws`;
      });
  }
  return baseUrlPromise;
}

/**
 * Opens an authenticated orchestrator stream, reconnecting with backoff.
 * `onMessage` returns true once the stream is complete (no reconnect).
 * `onUnavailable` fires when the stream cannot be (re)established.
 * Returns a function that closes the stream.
 */
function openStream<T>(
  path: string,
  onMessage: (msg: StreamMessage<T>) => boolean,
  onUnavailable: () => void,
): () => void {
  let cancelled = false;
  let finished = false;
  let retries = 0;
  let socket: WebSocket | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | undefined;

  const connect = async () => {
    const baseUrl = await resolveStreamBaseUrl();
    const token = localStorage.getItem("cloudo_token");
    if (cancelled) return;
    if (!token) {
      onUnavailable();
      return;
    }

    const ws = new WebSocket(`${baseUrl}/${path}`);
    socket = ws;

    ws.onopen = () => {
      ws.send(
        JSON.stringify({
          type: "auth",
          token,
          includeAllTeams: shouldIncludeAllTeams(),
        }),
      );
    };

    ws.onmessage = (event) => {
      let msg: StreamMessage<T>;
      try {
        msg = JSON.parse(event.data);
      } catch {
        return;
      }
      retries = 0;
      finished = onMessage(msg);
    };

    ws.onclose = (event) => {
      if (cancelled || finished) return;
      if (FATAL_CLOSE_CODES.has(event.code) || retries >= MAX_RETRIES) {
        onUnavailable();
        return;
      }
      retries += 1;
      retryTimer = setTimeout(connect, 1000 * 2 ** (retries - 1));
    };
  };

  connect();

  return () => {
    cancelled = true;
    clearTimeout(retryTimer);
    socket?.close();
  };
}

function useLatestRef<V>(value: V) {
  const ref = useRef(value);
  useEffect(() => {
    ref.current = value;
  }, [value]);
  return ref;
}

/**
 * Streams the log of a running execution over WebSocket.
 * Returns status "unavailable" when the stream cannot be established, so the
 * caller can fall back to polling.
 */
export function useExecutionLogStream<T>(
  partitionKey: string | undefined,
  execId: string | undefined,
  enabled: boolean,
  onEnd?: () => void,
): LogStreamState<T> {
  const [state, setState] = useState<InternalState<T>>(IDLE);
  const onEndRef = useLatestRef(onEnd);

  const key =
    enabled && partitionKey && execId ? `${partitionKey}/${execId}` : null;

  useEffect(() => {
    if (!key) return;

    let entry: Partial<T> | null = null;
    let log: string | null = null;

    const close = openStream<T>(
      `logs/${key}`,
      (msg) => {
        let ended = false;
        if (msg.type === "entry") entry = msg.entry;
        else if (msg.type === "replace") log = msg.log;
        else if (msg.type === "append") log = (log ?? "") + msg.data;
        else if (msg.type === "end") ended = true;
        else return false;

        setState({ key, status: ended ? "ended" : "live", entry, log });
        if (ended) onEndRef.current?.();
        return ended;
      },
      () => setState({ key, status: "unavailable", entry, log }),
    );

    return () => {
      close();
      setState(IDLE);
    };
  }, [key, onEndRef]);

  if (!key) return IDLE;
  if (state.key !== key)
    return { status: "connecting", entry: null, log: null };
  return state;
}

/**
 * Streams changes to the executions of a day (partition). `onItems` receives
 * the latest row of every execution that changed, as returned by logs/query.
 */
export function useExecutionsStream<T>(
  partitionKey: string | undefined,
  enabled: boolean,
  onItems: (items: T[]) => void,
): LogStreamStatus {
  const [state, setState] = useState<{
    key: string | null;
    status: LogStreamStatus;
  }>({ key: null, status: "idle" });
  const onItemsRef = useLatestRef(onItems);

  const key = enabled && partitionKey ? partitionKey : null;

  useEffect(() => {
    if (!key) return;

    const close = openStream<T>(
      `executions/${key}`,
      (msg) => {
        if (msg.type === "upsert" && msg.items.length > 0) {
          onItemsRef.current(msg.items);
        }
        setState({ key, status: "live" });
        return false;
      },
      () => setState({ key, status: "unavailable" }),
    );

    return () => {
      close();
      setState({ key: null, status: "idle" });
    };
  }, [key, onItemsRef]);

  if (!key) return "idle";
  return state.key === key ? state.status : "connecting";
}
