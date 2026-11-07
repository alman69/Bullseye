import { useEffect, useRef } from "react";
import type { MutableRefObject } from "react";
import type { CommandPayload } from "@/lib/protocol";

const KEYS: Record<string, string> = { ArrowUp: "forward", w: "forward", ArrowDown: "backward", s: "backward", ArrowLeft: "left", a: "left", ArrowRight: "right", d: "right" };
type Controls = {
  enabled: boolean;
  isPilot: boolean;
  busy: boolean;
  stopPending: boolean;
  token: string | null;
  speed: number;
  reservedToken: MutableRefObject<string | null>;
  act: (type: string, payload?: CommandPayload) => Promise<boolean>;
  stop: () => Promise<void>;
};

export function useRoverKeyboard(controls: Controls) {
  const latest = useRef(controls);
  latest.current = controls;
  const reset = useRef<() => void>(() => {});

  useEffect(() => {
    const keys = new Map<string, string>();
    let hold: { id: string; token: string; started: boolean; pending: boolean } | null = null;
    const clear = (notify = false) => {
      const old = hold;
      hold = null;
      keys.clear();
      if (notify && old?.started) void latest.current.act("hold_end", { hold_id: old.id, arm_token: old.token });
    };
    reset.current = () => clear();
    const pump = () => {
      const current = latest.current;
      const gesture = hold;
      const direction = [...keys.values()].at(-1);
      if (!gesture || !direction) return;
      if (!current.enabled || !current.isPilot || !current.token) { clear(); return; }
      if (gesture.pending) return;
      if (!gesture.started && (current.busy || current.stopPending || current.reservedToken.current === current.token)) return;
      const kind = gesture.started ? "hold_update" : "hold_start";
      if (!gesture.started) {
        gesture.token = current.token;
        gesture.started = true;
        current.reservedToken.current = current.token;
      }
      gesture.pending = true;
      void current.act(kind, { direction, speed: current.speed, hold_id: gesture.id, arm_token: gesture.token }).then((accepted) => {
        if (!accepted && hold === gesture) { clear(); void latest.current.stop(); }
      }).finally(() => { gesture.pending = false; });
    };
    const safetyStop = () => {
      clear();
      const current = latest.current;
      if (current.isPilot && (current.enabled || current.busy)) void current.stop();
    };
    const hidden = () => { if (document.hidden) safetyStop(); };
    const down = (event: KeyboardEvent) => {
      const target = event.target instanceof Element ? event.target : null;
      if (event.code === "Space") { event.preventDefault(); clear(); void latest.current.stop(); return; }
      if (target?.closest("input,select,textarea,[role=slider],a") || event.repeat || event.ctrlKey || event.metaKey || event.altKey) return;
      const direction = KEYS[event.key] ?? KEYS[event.key.toLowerCase()];
      if (!direction || !latest.current.enabled) return;
      event.preventDefault();
      const key = event.code || event.key;
      if (keys.has(key)) return;
      keys.set(key, direction);
      if (!hold) hold = { id: crypto.randomUUID(), token: "", started: false, pending: false };
      pump();
    };
    const up = (event: KeyboardEvent) => {
      const key = event.code || event.key;
      if (!keys.delete(key)) return;
      if (!keys.size) clear(true);
      else pump();
    };
    const timer = setInterval(pump, 100);
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("blur", safetyStop);
    window.addEventListener("pagehide", safetyStop);
    document.addEventListener("visibilitychange", hidden);
    return () => {
      clearInterval(timer);
      safetyStop();
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      window.removeEventListener("blur", safetyStop);
      window.removeEventListener("pagehide", safetyStop);
      document.removeEventListener("visibilitychange", hidden);
    };
  }, []);

  useEffect(() => {
    if (!controls.enabled || !controls.isPilot) reset.current();
  }, [controls.enabled, controls.isPilot]);
}
