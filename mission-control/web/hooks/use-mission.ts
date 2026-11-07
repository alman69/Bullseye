import { useCallback, useEffect, useRef, useState } from "react";
import type { CommandPayload, MissionState, SendCommand } from "@/lib/protocol";

export function socketUrl(path: string) {
  return `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}${path}`;
}

export function useMission() {
  const [state, setState] = useState<MissionState | null>(null);
  const [connected, setConnected] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const socket = useRef<WebSocket | null>(null);
  const current = useRef({ state, sessionId });
  current.current = { state, sessionId };
  const pending = useRef(
    new Map<
      string,
      {
        resolve: () => void;
        reject: (e: Error) => void;
        timer: ReturnType<typeof setTimeout>;
      }
    >(),
  );

  const send: SendCommand = useCallback(
    (type: string, payload: CommandPayload = {}) => {
      return new Promise<void>((resolve, reject) => {
        if (socket.current?.readyState !== WebSocket.OPEN) {
          reject(new Error("Control link is disconnected."));
          return;
        }
        const id = crypto.randomUUID();
        const timer = setTimeout(() => {
          pending.current.delete(id);
          reject(new Error("Command acknowledgement timed out."));
        }, 2500);
        pending.current.set(id, { resolve, reject, timer });
        socket.current.send(JSON.stringify({ id, type, payload }));
      });
    },
    [],
  );

  useEffect(() => {
    let disposed = false;
    let reconnect: ReturnType<typeof setTimeout>;
    let attempts = 0;
    const connect = () => {
      if (disposed) return;
      const ws = new WebSocket(socketUrl("/ws/control"));
      socket.current = ws;
      ws.onopen = () => {
        if (disposed) return;
        attempts = 0;
        setConnected(true);
      };
      ws.onmessage = (event) => {
        if (disposed) return;
        const message = JSON.parse(event.data);
        if (message.type === "hello") setSessionId(message.session_id);
        else if (message.type === "state") setState(message.state);
        else if (message.type === "ack") {
          const entry = pending.current.get(message.id);
          if (!entry) return;
          clearTimeout(entry.timer);
          pending.current.delete(message.id);
          if (message.status === "accepted") entry.resolve();
          else entry.reject(new Error(message.error || "Command rejected."));
        }
      };
      ws.onclose = () => {
        if (disposed) return;
        setConnected(false);
        setSessionId(null);
        for (const item of pending.current.values()) {
          clearTimeout(item.timer);
          item.reject(new Error("Control link disconnected."));
        }
        pending.current.clear();
        reconnect = setTimeout(connect, Math.min(1000 * 2 ** attempts++, 5000));
      };
      ws.onerror = () => ws.close();
    };
    connect();
    return () => {
      disposed = true;
      clearTimeout(reconnect);
      socket.current?.close();
      for (const item of pending.current.values()) {
        clearTimeout(item.timer);
        item.reject(new Error("Control link closed."));
      }
      pending.current.clear();
    };
  }, []);

  useEffect(() => {
    const timer = setInterval(() => {
      const { state: latest, sessionId: id } = current.current;
      if (id && latest?.pilot_id === id) void send("heartbeat").catch(() => {});
    }, 1000);
    return () => clearInterval(timer);
  }, [send]);

  const act = useCallback(
    async (type: string, payload?: CommandPayload) => {
      try {
        await send(type, payload);
        return true;
      } catch (e) {
        setError(e instanceof Error ? e.message : "Command failed.");
        return false;
      }
    },
    [send],
  );

  const stop = useCallback(async () => {
    try {
      const response = await fetch("/api/stop", {
        method: "POST",
        signal: AbortSignal.timeout(2500),
      });
      if (!response.ok) throw new Error("Stop request failed.");
      setState(await response.json());
    } catch {
      try {
        await send("stop");
      } catch {
        setError(
          "STOP could not reach the server. Use the rover’s physical power switch if necessary.",
        );
      }
    }
  }, [send]);

  return {
    state,
    connected,
    sessionId,
    isPilot: !!sessionId && state?.pilot_id === sessionId,
    act,
    send,
    stop,
    error,
    clearError: () => setError(null),
  };
}
