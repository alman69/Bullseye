import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useMission } from "./use-mission";

class FakeSocket {
  static OPEN = 1;
  static instances: FakeSocket[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  sent: { id: string; type: string; payload: unknown }[] = [];
  url: string;
  constructor(url: string) {
    this.url = url;
    FakeSocket.instances.push(this);
  }
  send(text: string) {
    this.sent.push(JSON.parse(text));
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  message(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) });
  }
  close() {
    this.readyState = 3;
    this.onclose?.();
  }
  ackLatest() {
    this.message({ type: "ack", id: this.sent.at(-1)!.id, status: "accepted" });
  }
}

beforeEach(() => {
  vi.useFakeTimers();
  FakeSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeSocket);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("browser latency probes", () => {
  it("measures only an acknowledged ping and repeats every five seconds for viewers", async () => {
    const now = vi.spyOn(performance, "now").mockReturnValue(1000);
    const { result } = renderHook(() => useMission());
    const socket = FakeSocket.instances[0];
    await act(async () => socket.open());
    expect(socket.sent.map((command) => command.type)).toEqual(["ping"]);
    expect(result.current.latency.status).toBe("measuring");
    now.mockReturnValue(1042);
    await act(async () => socket.ackLatest());
    expect(result.current.latency).toEqual({ status: "ready", rttMs: 42 });
    await act(async () => vi.advanceTimersByTime(4999));
    expect(socket.sent).toHaveLength(1);
    await act(async () => vi.advanceTimersByTime(1));
    expect(socket.sent.map((command) => command.type)).toEqual([
      "ping",
      "ping",
    ]);
    expect(result.current.isPilot).toBe(false);
  });

  it("reports timeouts, recovers, and clears latency on disconnect without replaying probes", async () => {
    const { result } = renderHook(() => useMission());
    const socket = FakeSocket.instances[0];
    await act(async () => socket.open());
    await act(async () => vi.advanceTimersByTime(2500));
    expect(result.current.latency).toEqual({ status: "timeout", rttMs: null });
    await act(async () => vi.advanceTimersByTime(2500));
    await act(async () => socket.ackLatest());
    expect(result.current.latency.status).toBe("ready");
    await act(async () => socket.close());
    expect(result.current.latency).toEqual({ status: "offline", rttMs: null });
    await act(async () => vi.advanceTimersByTime(1000));
    const replacement = FakeSocket.instances[1];
    expect(replacement.sent).toHaveLength(0);
    await act(async () => replacement.open());
    expect(replacement.sent.map((command) => command.type)).toEqual(["ping"]);
    expect(result.current.sessionId).toBeNull();
  });

  it("keeps pilot heartbeats independent from probes", async () => {
    const { result } = renderHook(() => useMission());
    const socket = FakeSocket.instances[0];
    await act(async () => {
      socket.open();
      socket.message({ type: "hello", session_id: "pilot" });
    });
    await act(async () => socket.ackLatest());
    await act(async () =>
      socket.message({ type: "state", state: { pilot_id: "pilot" } }),
    );
    await act(async () => vi.advanceTimersByTime(1000));
    expect(socket.sent.map((command) => command.type)).toEqual([
      "ping",
      "heartbeat",
    ]);
    expect(result.current.isPilot).toBe(true);
    await act(async () => socket.ackLatest());
  });
});
