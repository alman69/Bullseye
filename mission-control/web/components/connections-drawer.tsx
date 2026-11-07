import { useState } from "react";
import {
  ArrowDownUp,
  Camera,
  Check,
  CircleHelp,
  Copy,
  Cpu,
  Globe,
  Info,
  Network,
  Radio,
  Square,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { describeAccess } from "@/lib/access-path";
import type { LinkLatency, MissionState } from "@/lib/protocol";

export function ConnectionsDrawer({
  open,
  onOpenChange,
  state,
  connected,
  latency,
  onStop,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  state: MissionState | null;
  connected: boolean;
  latency: LinkLatency;
  onStop: () => void;
}) {
  const access = describeAccess(window.location.href);
  const [copyStatus, setCopyStatus] = useState("");
  const camera = state?.camera;
  const cameraLabel = !connected
    ? "Status unavailable"
    : camera?.status === "streaming"
      ? camera.source === "live"
        ? "Live camera"
        : "Simulated feed"
      : camera?.status === "stale"
        ? "Stale frame"
        : camera?.status === "unconfigured"
          ? "Not configured"
          : "Waiting for frames";
  const latencyLabel = !connected
    ? "Offline"
    : latency.status === "ready"
      ? `${latency.rttMs} ms round trip`
      : latency.status === "timeout"
        ? "Probe timed out"
        : "Measuring response…";
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(access.origin);
      setCopyStatus("Address copied");
    } catch {
      setCopyStatus("Copy unavailable. Select the address below.");
    }
  };
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetTrigger
        render={
          <Button
            variant="ghost"
            className="connections-trigger"
            aria-label="Open connections"
          />
        }
      >
        <Network size={17} />
        <span>Connections</span>
      </SheetTrigger>
      <SheetContent className="guide-sheet connections-sheet">
        <SheetHeader>
          <SheetTitle>Connection readiness</SheetTitle>
          <SheetDescription>
            One mission. Every link accounted for.
          </SheetDescription>
          <Button
            className="stop-button"
            aria-label="STOP rover in connections"
            onClick={onStop}
          >
            <Square size={11} fill="currentColor" /> STOP ROVER
          </Button>
        </SheetHeader>
        <div className="connections-content">
          <div className="connection-mode">
            <span className="micro">MISSION BEHAVIOR</span>
            <strong>
              {!state
                ? "Waiting for server"
                : state.mode === "simulation"
                  ? "Simulation"
                  : "Hardware mode"}
            </strong>
            <p>
              {state?.mode === "simulation"
                ? "Mission actions and telemetry are simulated, even when a real camera is connected."
                : "Hardware capabilities remain gated by the rover adapter."}
            </p>
          </div>
          <section className="connection-item" aria-label="Browser connection">
            <ArrowDownUp size={20} />
            <div>
              <h3>Browser ↔ Mission Control</h3>
              <strong
                className={connected ? "connection-good" : "connection-wait"}
              >
                {connected ? "WebSocket connected" : "Disconnected"}
              </strong>
              <p aria-live="polite">{latencyLabel}</p>
              <small>
                Measured to the laptop every 5 seconds. This is not the rover
                Wi-Fi latency.
              </small>
            </div>
          </section>
          <section className="connection-item" aria-label="Camera connection">
            <Camera size={20} />
            <div>
              <h3>Camera relay</h3>
              <strong
                className={
                  connected && camera?.status === "streaming"
                    ? "connection-good"
                    : "connection-wait"
                }
              >
                {cameraLabel}
              </strong>
              <p>
                {connected && camera?.frame_age_ms != null
                  ? `${camera.frame_age_ms} ms since the latest frame`
                  : "No fresh frame status available"}
              </p>
              <small>
                One shared stream serves all viewers. A live picture does not
                verify motor control.
              </small>
            </div>
          </section>
          <section
            className="connection-item"
            aria-label="ESP32 bridge readiness"
          >
            <Radio size={20} />
            <div>
              <h3>ESP32 command bridge</h3>
              <strong className="connection-wait">Awaiting integration</strong>
              <p>
                Camera streaming and command delivery are separate connections.
              </p>
            </div>
          </section>
          <section className="connection-item" aria-label="UNO readiness">
            <Cpu size={20} />
            <div>
              <h3>UNO acknowledgement & telemetry</h3>
              <strong className="connection-wait">Not tested</strong>
              <p>
                Movement, sensor responses, and device timeouts still need
                hardware validation.
              </p>
            </div>
          </section>
          <section
            className="connection-item access-item"
            aria-label="Current access address"
          >
            <Globe size={20} />
            <div>
              <h3>Current access address</h3>
              <strong>
                {access.kind} · {access.secure ? "HTTPS" : "HTTP"}
              </strong>
              <input
                aria-label="Current website address"
                readOnly
                value={access.origin}
              />
              <Button variant="outline" size="sm" onClick={() => void copy()}>
                {copyStatus === "Address copied" ? (
                  <Check size={14} />
                ) : (
                  <Copy size={14} />
                )}
                Copy address
              </Button>
              <p role="status">
                {copyStatus ||
                  (access.kind === "This laptop"
                    ? "Localhost works on this laptop. Open the public tunnel URL to share with other devices."
                    : "This describes the website address; it does not verify rover or tunnel health.")}
              </p>
            </div>
          </section>
          <div className="connection-checklist">
            <h3>
              <CircleHelp size={16} />
              Before the real-rover demo
            </h3>
            <ol>
              <li>Wi-Fi connects the laptop to the rover.</li>
              <li>Ethernet gives the laptop Internet access.</li>
              <li>Verify the camera and UNO separately.</li>
              <li>Test the public URL on a phone using cellular data.</li>
            </ol>
          </div>
          <div className="guide-notice">
            <Info size={18} />
            <p>
              Keep the laptop awake. Remote video and commands depend on it.
              Hardware movement stays unavailable until the adapter is ready.
            </p>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}
