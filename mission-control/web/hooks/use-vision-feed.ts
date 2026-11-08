import { useEffect, useRef } from "react";
import { socketUrl } from "@/hooks/use-mission";
import type { VisionFrame } from "@/lib/vision";

/** Calls onFrame for every processed camera frame from /ws/vision (no React re-render per frame). */
export function useVisionFeed(onFrame: (frame: VisionFrame) => void, enabled = true) {
  const callback = useRef(onFrame);
  useEffect(() => {
    callback.current = onFrame;
  });
  useEffect(() => {
    if (!enabled) return;
    let disposed = false;
    let ws: WebSocket;
    let retry: ReturnType<typeof setTimeout>;
    const connect = () => {
      if (disposed) return;
      ws = new WebSocket(socketUrl("/ws/vision"));
      ws.onmessage = (event) => {
        try {
          callback.current(JSON.parse(event.data));
        } catch {
          /* ignore malformed frames */
        }
      };
      ws.onclose = () => {
        if (!disposed) retry = setTimeout(connect, 2000);
      };
      ws.onerror = () => ws.close();
    };
    connect();
    return () => {
      disposed = true;
      clearTimeout(retry);
      ws?.close();
    };
  }, [enabled]);
}
