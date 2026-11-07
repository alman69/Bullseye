import { useEffect, useRef, useState } from "react";
import { Camera, Expand, Radio, VideoOff } from "lucide-react";
import { Button } from "@/components/ui/button";
import { socketUrl } from "@/hooks/use-mission";
import type { MissionState } from "@/lib/protocol";

export function CameraView({
  state,
  connected,
  onStop,
}: {
  state: MissionState | null;
  connected: boolean;
  onStop: () => void;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const [hasFrame, setHasFrame] = useState(false);
  const [videoConnected, setVideoConnected] = useState(false);
  useEffect(() => {
    let disposed = false,
      processing = false;
    let latest: Blob | null = null;
    let ws: WebSocket;
    let retry: ReturnType<typeof setTimeout>;
    const draw = async () => {
      if (processing || disposed) return;
      processing = true;
      try {
        while (latest && !disposed) {
          const blob = latest;
          latest = null;
          const frame = await createImageBitmap(blob);
          const target = canvas.current;
          if (target && !disposed) {
            if (
              target.width !== frame.width ||
              target.height !== frame.height
            ) {
              target.width = frame.width;
              target.height = frame.height;
            }
            target.getContext("2d")?.drawImage(frame, 0, 0);
            setHasFrame(true);
          }
          frame.close();
        }
      } catch {
        if (!disposed) setHasFrame(false);
      } finally {
        processing = false;
      }
    };
    const connect = () => {
      if (disposed) return;
      ws = new WebSocket(socketUrl("/ws/video"));
      ws.binaryType = "blob";
      ws.onopen = () => {
        if (!disposed) setVideoConnected(true);
      };
      ws.onmessage = (event) => {
        latest = event.data;
        void draw();
      };
      ws.onclose = () => {
        if (!disposed) {
          setVideoConnected(false);
          retry = setTimeout(connect, 1500);
        }
      };
      ws.onerror = () => ws.close();
    };
    connect();
    return () => {
      disposed = true;
      latest = null;
      clearTimeout(retry);
      ws?.close();
    };
  }, []);
  const source = state?.camera.source;
  const stale =
    !connected || !videoConnected || state?.camera.status !== "streaming";
  const expand = () => {
    if (document.fullscreenElement) void document.exitFullscreen();
    else void panel.current?.requestFullscreen?.();
  };
  return (
    <section
      className="panel camera-panel"
      ref={panel}
      aria-label="Rover camera"
    >
      <div className="panel-heading">
        <div className="heading-icon">
          <Camera size={16} />
          <h2>Rover perspective</h2>
        </div>
        <div className="camera-actions">
          <Button
            className="stop-button fullscreen-stop"
            aria-label="STOP rover in fullscreen"
            onClick={onStop}
          >
            STOP
          </Button>
          <span className="micro muted">CAM 01</span>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Expand camera"
            onClick={expand}
          >
            <Expand size={14} />
          </Button>
        </div>
      </div>
      <div className="camera-stage">
        <canvas
          ref={canvas}
          width={800}
          height={450}
          aria-label={
            source === "live"
              ? "Live rover camera feed"
              : source === "simulation" ? "Simulated rover camera feed" : "Rover camera feed unavailable"
          }
        />
        {!hasFrame && (
          <div className="camera-empty">
            <Camera size={30} />
            <p>Establishing visual link</p>
            <span>Waiting for camera frames…</span>
          </div>
        )}
        {hasFrame && stale && (
          <div className="camera-stale">
            <VideoOff size={23} />
            <strong>Visual link interrupted</strong>
            <span>The last frame is frozen. Reconnecting…</span>
          </div>
        )}
        <span className="camera-source">
          <i className={stale ? "status-dot warn" : "status-dot"} />
          {source === "live"
            ? "LIVE CAMERA"
            : source === "simulation"
              ? "SIMULATED FEED"
              : "CAMERA NOT CONFIGURED"}
        </span>
      </div>
      <div className="camera-footer">
        <span>
          <Radio size={12} />
          {source === "live"
            ? "ESP32 camera relay"
            : source === "simulation"
              ? "Virtual training environment"
              : "Waiting for a configured camera"}
        </span>
        <span>
          {state?.camera.frame_age_ms != null
            ? `${Math.round(state.camera.frame_age_ms)} ms frame age`
            : "Awaiting frames"}
          <b>·</b>Up to 10 fps
        </span>
      </div>
    </section>
  );
}
