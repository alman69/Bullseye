import { useEffect, useRef, useState } from "react";
import {
  Activity,
  ArrowRight,
  BookOpen,
  Check,
  CheckCheck,
  ChevronRight,
  CircleDot,
  Crosshair,
  Flag,
  Gem,
  Home,
  Info,
  LoaderCircle,
  MousePointer2,
  Orbit,
  Radio,
  RotateCcw,
  Satellite,
  ScanLine,
  ShieldCheck,
  Square,
  Terminal,
  Unplug,
  X,
} from "lucide-react";
import HalftoneNebula from "@/components/ui/halftone-nebula";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { CameraView } from "@/components/camera-view";
import { ManualControls } from "@/components/manual-controls";
import { ConnectionsDrawer } from "@/components/connections-drawer";
import { MobileTabs, type MobileView } from "@/components/mobile-tabs";
import { useMission } from "@/hooks/use-mission";
import { useMobileView } from "@/hooks/use-mobile-view";
import { PHASE_LABELS, SAMPLE_HEX } from "@/lib/protocol";
import type { MissionState, Phase, SampleColor } from "@/lib/protocol";

const SKY = { planetX: 0.74, planetY: 0.2 };
const STAGES = [
  { name: "Search", icon: ScanLine },
  { name: "Approach", icon: Crosshair },
  { name: "Verify", icon: ShieldCheck },
  { name: "Collect", icon: Gem },
  { name: "Return", icon: Home },
  { name: "Complete", icon: Flag },
];
const STAGE_INDEX: Record<Phase, number> = {
  idle: -1,
  searching: 0,
  approaching: 1,
  awaiting_confirmation: 2,
  collecting: 3,
  collected: 3,
  returning: 4,
  completed: 5,
  stopped: -1,
};
const SAMPLE_COLORS: SampleColor[] = ["red", "green", "blue"];

function MissionClock({ state }: { state: MissionState | null }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  const seconds = state?.started_at
    ? Math.max(
        0,
        Math.floor(
          ((state.finished_at ? Date.parse(state.finished_at) : now) -
            Date.parse(state.started_at)) /
            1000,
        ),
      )
    : 0;
  return (
    <div className="mission-clock">
      <span className="micro">MISSION ELAPSED</span>
      <div>
        <span className="clock-plus">T+</span>
        {String(Math.floor(seconds / 60)).padStart(2, "0")}
        <span className="clock-colon">:</span>
        {String(seconds % 60).padStart(2, "0")}
      </div>
    </div>
  );
}

function SampleReview({
  state,
  canAct,
  busy,
  action,
}: {
  state: MissionState | null;
  canAct: boolean;
  busy: boolean;
  action: (kind: string, payload?: Record<string, unknown>) => Promise<void>;
}) {
  const review = state?.review;
  const phase = state?.phase || "idle";
  const awaiting = phase === "awaiting_confirmation" && review;
  const collected = ["collected", "returning", "completed"].includes(phase);
  const payload = review
    ? { review_id: review.id, mission_id: review.mission_id }
    : {};
  return (
    <section
      className={`panel review-panel ${awaiting ? "review-active" : ""}`}
      aria-label="Sample review"
      aria-live="polite"
    >
      <div className="panel-heading">
        <div className="heading-icon">
          <Gem size={16} />
          <h2>Sample verification</h2>
        </div>
        <span className={`small-indicator ${awaiting ? "accent" : ""}`}>
          {awaiting
            ? "ACTION REQUIRED"
            : collected
              ? "CONFIRMED"
              : "HUMAN IN THE LOOP"}
        </span>
      </div>
      {awaiting ? (
        <div className="review-content">
          <div className="review-photo">
            {review.image_url ? (
              <img
                src={review.image_url}
                alt={`Frozen candidate for the requested ${review.requested_color} sample`}
              />
            ) : (
              <div className="no-review-image">
                <Info size={20} />
                <span>Image unavailable</span>
              </div>
            )}
            <span>CAPTURED FRAME</span>
          </div>
          <div className="review-copy">
            <p className="eyebrow">WE FOUND SOMETHING.</p>
            <h3>Is this the sample you wanted?</h3>
            <p>
              Requested:{" "}
              <strong style={{ color: SAMPLE_HEX[review.requested_color] }}>
                {review.requested_color} sample
              </strong>
              . The rover is stopped and waiting for your call.
            </p>
            <div className="review-buttons">
              <Button
                disabled={!canAct || busy}
                className="primary-button"
                onClick={() => void action("confirm", payload)}
              >
                <Check size={15} />
                Confirm sample
              </Button>
              <Button
                disabled={!canAct || busy}
                variant="outline"
                onClick={() => void action("reject", payload)}
              >
                Keep searching
                <ArrowRight size={14} />
              </Button>
            </div>
            <span className="review-caption">
              {review.simulated ? "Simulated detection" : "Candidate detection"}{" "}
              ·{" "}
              {review.image_source === "live"
                ? "Live camera snapshot"
                : "Simulated snapshot"}{" "}
              · {new Date(review.captured_at).toLocaleTimeString()}
            </span>
          </div>
        </div>
      ) : phase === "collecting" ? (
        <div className="review-placeholder">
          <div className="sample-icon">
            <LoaderCircle
              className={state?.collection_error ? "" : "spin"}
              size={25}
            />
          </div>
          <div>
            <h3>
              {state?.collection_error
                ? "Collection needs another try."
                : "One small sample. One big moment."}
            </h3>
            <p>
              {state?.collection_error ||
                "Waiting for the sample indicator acknowledgement before marking this sample collected."}
            </p>
            {state?.collection_error && (
              <Button
                className="retry-button"
                disabled={!canAct || busy}
                onClick={() => void action("retry_collection")}
              >
                <RotateCcw size={14} />
                Retry collection
              </Button>
            )}
          </div>
        </div>
      ) : collected ? (
        <div className="review-placeholder">
          <div className="sample-icon success">
            <CheckCheck size={26} />
          </div>
          <div>
            <h3>
              {phase === "completed"
                ? "Sample home. Mission accomplished."
                : "Sample confirmed and collected."}
            </h3>
            <p>
              {phase === "completed"
                ? "Home arrival was confirmed by the pilot. Ready for another discovery."
                : `The ${state?.target} indicator command was acknowledged. Your sample is ready for the journey home.`}
            </p>
          </div>
          <div
            className="collected-gem"
            style={{
              color: state?.target ? SAMPLE_HEX[state.target] : undefined,
            }}
          >
            <Gem size={38} />
          </div>
        </div>
      ) : (
        <div className="review-placeholder">
          <div className="sample-icon">
            <Gem size={26} />
          </div>
          <div>
            <h3>
              {phase === "stopped"
                ? "Mission on hold."
                : phase === "searching" || phase === "approaching"
                  ? "A discovery is getting closer."
                  : "Your next discovery starts here."}
            </h3>
            <p>
              {phase === "stopped"
                ? "Claim controls and reset the stop when you are ready."
                : "When the rover finds a candidate, review its image here and confirm the sample."}
            </p>
          </div>
          <span className="waiting-lines">
            <i />
            <i />
            <i />
          </span>
        </div>
      )}
    </section>
  );
}

export default function App() {
  const {
    state,
    connected,
    latency,
    isPilot,
    sessionId,
    act,
    send,
    stop,
    error,
    clearError,
  } = useMission();
  const [busy, setBusy] = useState(false);
  const [guideOpen, setGuideOpen] = useState(false);
  const [connectionsOpen, setConnectionsOpen] = useState(false);
  const [mobileView, setMobileView] = useState<MobileView>("mission");
  const isMobile = useMobileView();
  const activity = useRef<HTMLElement>(null);
  const phase = state?.phase || "idle";
  const target = state?.target || "red";
  const pilotAvailable = !state?.pilot_id;
  const editable =
    connected && isPilot && ["idle", "completed"].includes(phase);
  const controlsEnabled = !!(
    connected &&
    isPilot &&
    state?.capabilities.manual &&
    ["idle", "completed", "returning"].includes(phase) &&
    !guideOpen &&
    !connectionsOpen &&
    (!isMobile || mobileView === "pilot")
  );
  const run = async (kind: string, payload?: Record<string, unknown>) => {
    setBusy(true);
    try {
      await act(kind, payload);
    } finally {
      setBusy(false);
    }
  };
  const step = STAGE_INDEX[phase];
  return (
    <>
      <div className="nebula-backdrop" aria-hidden="true">
        <HalftoneNebula
          preset="bullseye"
          params={SKY}
          interactive={false}
          maxDpr={1}
          maxFps={30}
        />
        <div className="nebula-shade" />
      </div>
      <div className="app-shell" data-mobile-view={mobileView}>
        <header className="topbar">
          <a
            className="brand"
            href="#main"
            aria-label="Bullseye Mission Control"
          >
            <img
              className="event-badge brand-badge"
              src="/assets/ub-hacking-2026.png"
              width={44}
              height={44}
              alt="UB Hacking XV Years 2026 badge"
            />
            <span>
              BULLSEYE<span className="brand-sub">MISSION CONTROL</span>
            </span>
          </a>
          <nav className="main-nav" aria-label="Dashboard navigation">
            <a href="#main" className="nav-active">
              <Orbit size={15} />
              Overview
            </a>
            <button
              onClick={() =>
                activity.current?.scrollIntoView({ behavior: "smooth" })
              }
            >
              <Activity size={15} />
              Activity
            </button>
          </nav>
          <div className="topbar-actions">
            <ConnectionsDrawer
              open={connectionsOpen}
              onOpenChange={setConnectionsOpen}
              state={state}
              connected={connected}
              latency={latency}
              onStop={() => void stop()}
            />
            <Sheet open={guideOpen} onOpenChange={setGuideOpen}>
              <SheetTrigger
                render={
                  <Button
                    variant="ghost"
                    className="guide-trigger"
                    aria-label="Open field guide"
                  />
                }
              >
                <BookOpen size={16} />
                <span>Field guide</span>
              </SheetTrigger>
              <SheetContent className="guide-sheet">
                <SheetHeader>
                  <SheetTitle>Field guide</SheetTitle>
                  <SheetDescription>
                    Everything you need for your next discovery.
                  </SheetDescription>
                  <Button
                    className="stop-button"
                    aria-label="STOP rover in field guide"
                    onClick={() => void stop()}
                  >
                    <Square size={11} fill="currentColor" /> STOP ROVER
                  </Button>
                </SheetHeader>
                <div className="guide-content">
                  <div className="event-insignia">
                    <img
                      className="event-badge"
                      src="/assets/ub-hacking-2026.png"
                      width={150}
                      height={150}
                      alt="UB Hacking XV Years 2026 event badge"
                    />
                    <p>BULLSEYE / BUILT AT UB HACKING</p>
                  </div>
                  <h3>01 / Take the controls</h3>
                  <p>
                    Anyone with this link can claim the available pilot seat.
                    Everyone can watch, and everyone can press STOP.
                  </p>
                  <h3>02 / Choose your sample</h3>
                  <p>
                    Select red, green, or blue, then launch. The simulator
                    searches, approaches, and stops for review.
                  </p>
                  <h3>03 / Make the call</h3>
                  <p>
                    Confirm the captured sample or send the rover searching
                    again. Collection completes after the indicator command is
                    acknowledged.
                  </p>
                  <h3>04 / Bring it home</h3>
                  <p>
                    On phones, open the Pilot tab for manual driving. Select
                    Return Home. Use the on-screen pad or WASD/arrow keys to
                    assist the return. Select Mark Home when the rover has
                    arrived.
                  </p>
                  <h3>Stopping and reconnecting</h3>
                  <p>
                    Hold to drive; release to stop. STOP latches until the pilot
                    resets it. Reconnecting never resumes motion or reclaims
                    controls automatically. Leaving Pilot view or opening a
                    drawer stops manual input.
                  </p>
                  <div className="guide-notice">
                    <Info size={18} />
                    <p>
                      Simulation is always labeled. Hardware movement stays
                      unavailable until your rover adapter is connected. A
                      software STOP does not replace the physical power switch.
                    </p>
                  </div>
                  <p className="micro muted">BULLSEYE / UB HACKATHON 2026</p>
                </div>
              </SheetContent>
            </Sheet>
            <Button
              className="stop-button"
              onClick={() => void stop()}
              aria-label="STOP rover"
            >
              <Square size={11} fill="currentColor" />
              STOP
            </Button>
          </div>
        </header>
        <main id="main">
          <section className="mission-intro">
            <div>
              <div className="intro-eyebrow">
                <span className="micro">SURFACE OPERATIONS</span>
                <span className="intro-divider" />
                <Badge variant="outline" className="mode-badge">
                  {state?.mode === "hardware"
                    ? "HARDWARE MODE"
                    : "SIMULATION MODE"}
                </Badge>
              </div>
              <h1>
                Mission control<span>.</span>
              </h1>
              <p>A little rover. A world of discoveries.</p>
            </div>
            <MissionClock state={state} />
          </section>
          {isMobile && (
            <>
              <div className="mobile-mission-status" aria-live="polite">
                <span
                  className={`status-dot ${phase === "stopped" ? "warn" : ""}`}
                />
                <span>{PHASE_LABELS[phase]}</span>
                <small>
                  {mobileView === "pilot"
                    ? `${state?.mode === "hardware" ? "HARDWARE" : "SIMULATION"} · ${connected ? "ONLINE" : "OFFLINE"}`
                    : connected
                      ? "LINK CONNECTED"
                      : "LINK OFFLINE"}
                </small>
              </div>
              <MobileTabs value={mobileView} onChange={setMobileView} />
            </>
          )}
          <div className="connection-strip">
            <div>
              <span className={`status-dot ${connected ? "" : "warn"}`} />
              <span>Control link</span>
              <strong>{connected ? "Connected" : "Reconnecting"}</strong>
            </div>
            <div>
              <Satellite size={14} />
              <span>Rover</span>
              <strong>
                {state?.rover === "simulated"
                  ? "Simulated"
                  : state?.rover === "connected"
                    ? "Connected"
                    : "Not connected"}
              </strong>
            </div>
            <div>
              <Radio size={14} />
              <span>Camera</span>
              <strong>
                {state?.camera.status === "streaming"
                  ? state.camera.source === "live"
                    ? "Live"
                    : "Simulated"
                  : "Waiting for feed"}
              </strong>
            </div>
            <div className="session-indicator">
              <CircleDot size={14} />
              <span>Session</span>
              <strong>
                {state?.mission_id
                  ? state.mission_id.slice(0, 6).toUpperCase()
                  : "PRE-FLIGHT"}
              </strong>
            </div>
          </div>
          {!connected && (
            <div className="connection-banner" role="status">
              <Unplug size={16} />
              Control link disconnected. Controls are unavailable while
              reconnecting.
            </div>
          )}
          {phase === "stopped" && (
            <div className="stop-banner" role="status">
              <Square size={15} />
              <div>
                <strong>Software stop is latched.</strong>
                <span>
                  {state?.last_stop_acknowledged
                    ? state.mode === "simulation"
                      ? "All simulated movement has stopped."
                      : "The hardware adapter acknowledged STOP."
                    : "Hardware stop has not been verified."}
                </span>
              </div>
              <Button
                disabled={!connected || !isPilot || busy}
                variant="outline"
                onClick={() => void run("reset")}
              >
                <RotateCcw size={14} />
                Reset stop
              </Button>
            </div>
          )}
          <div
            className="dashboard-grid"
            id="dashboard-content"
            role={isMobile ? "tabpanel" : undefined}
            aria-labelledby={isMobile ? `tab-${mobileView}` : undefined}
          >
            <div className="main-column">
              <CameraView
                state={state}
                connected={connected}
                onStop={() => void stop()}
              />
              {isMobile &&
                mobileView === "pilot" &&
                phase === "awaiting_confirmation" && (
                  <div className="pilot-review-notice">
                    <Gem size={19} />
                    <span>A sample is waiting for your decision.</span>
                    <Button
                      variant="outline"
                      onClick={() => setMobileView("mission")}
                    >
                      Review sample
                    </Button>
                  </div>
                )}
              <div className="telemetry-strip">
                <div>
                  <span className="micro">ULTRASONIC RANGE</span>
                  <strong>
                    {state?.distance_cm != null ? (
                      <>
                        {state.distance_cm}
                        <small>
                          cm ·{" "}
                          {state.mode === "simulation"
                            ? "SIMULATED"
                            : "MEASURED"}
                        </small>
                      </>
                    ) : (
                      "Unavailable"
                    )}
                  </strong>
                </div>
                <div>
                  <span className="micro">MOTION REQUEST</span>
                  <strong>
                    {state?.motion.linear || state?.motion.angular
                      ? "Manual drive"
                      : "Stationary"}
                    <small>
                      {state?.mode === "hardware"
                        ? "NO HARDWARE LINK"
                        : "SIMULATED"}
                    </small>
                  </strong>
                </div>
                <div>
                  <span className="micro">TARGET SIGNATURE</span>
                  <strong style={{ color: SAMPLE_HEX[target] }}>
                    {target}
                    <small>COLOR SAMPLE</small>
                  </strong>
                </div>
              </div>
              <div className="mission-progress panel">
                <div className="progress-heading">
                  <span className="micro">MISSION SEQUENCE</span>
                  <span
                    className={phase === "completed" ? "success-text" : "muted"}
                  >
                    <span
                      className={`status-dot ${phase === "stopped" ? "warn" : ""}`}
                    />
                    {PHASE_LABELS[phase]}
                  </span>
                </div>
                <ol>
                  {STAGES.map(({ name, icon: Icon }, index) => (
                    <li
                      key={name}
                      className={`${index === step ? "current" : ""} ${index < step ? "done" : ""}`}
                      aria-current={index === step ? "step" : undefined}
                    >
                      <span className="step-icon">
                        {index < step ? (
                          <Check size={14} />
                        ) : (
                          <Icon size={15} />
                        )}
                      </span>
                      <span>{name}</span>
                      {index < STAGES.length - 1 && (
                        <span className="step-line" />
                      )}
                    </li>
                  ))}
                </ol>
              </div>
              <SampleReview
                state={state}
                canAct={connected && isPilot}
                busy={busy}
                action={run}
              />
            </div>
            <aside className="side-column" aria-label="Mission controls">
              <Card className="panel target-panel">
                <div className="panel-heading">
                  <div className="heading-icon">
                    <Crosshair size={16} />
                    <h2>Mission objective</h2>
                  </div>
                  <span className="micro muted">01 / 01</span>
                </div>
                <div className="target-body">
                  <span className="eyebrow">CHOOSE YOUR DISCOVERY</span>
                  <h3>Every sample tells a story.</h3>
                  <p>Select the color of the sample you want to find.</p>
                  <div
                    className="sample-options"
                    role="group"
                    aria-label="Sample color"
                  >
                    {SAMPLE_COLORS.map((color, index) => (
                      <button
                        key={color}
                        type="button"
                        className={`sample-option ${target === color ? "selected" : ""}`}
                        aria-pressed={target === color}
                        aria-label={`Select ${color} sample`}
                        disabled={!editable || busy}
                        onClick={() => void run("select_target", { color })}
                        style={
                          {
                            "--sample-color": SAMPLE_HEX[color],
                          } as React.CSSProperties
                        }
                      >
                        <span className="sample-rock">
                          <Gem size={27} strokeWidth={1.3} />
                        </span>
                        <strong>{color}</strong>
                        <small>SMP–0{index + 1}</small>
                        {target === color && (
                          <span className="sample-check">
                            <Check size={9} />
                          </span>
                        )}
                      </button>
                    ))}
                  </div>
                  <Button
                    className="launch-button"
                    disabled={!editable || busy || !state?.capabilities.mission}
                    onClick={() => void run("start")}
                  >
                    {busy ? (
                      <LoaderCircle className="spin" size={15} />
                    ) : (
                      <Orbit size={16} />
                    )}
                    Launch mission
                    <ArrowRight size={15} />
                  </Button>
                  <p className="launch-note">
                    {state?.mode === "hardware" && !state.capabilities.mission
                      ? "Waiting for the hardware navigation adapter."
                      : phase !== "idle" && phase !== "completed"
                        ? "Mission in progress. Stay curious."
                        : "Search. Verify. Collect. Return."}
                  </p>
                </div>
              </Card>
              <div className={`pilot-strip ${isPilot ? "pilot-active" : ""}`}>
                <span className="pilot-avatar">
                  <MousePointer2 size={17} />
                </span>
                <div>
                  <strong>
                    {isPilot
                      ? "You have the controls"
                      : pilotAvailable
                        ? "Pilot seat available"
                        : "Another pilot is in control"}
                  </strong>
                  <span>
                    {isPilot
                      ? "Keep this tab connected"
                      : "Everyone can watch and stop"}
                  </span>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={
                    !connected ||
                    busy ||
                    (!isPilot && !pilotAvailable) ||
                    !sessionId
                  }
                  onClick={() => void run(isPilot ? "release" : "claim")}
                >
                  {isPilot ? "Release" : "Claim"}
                </Button>
              </div>
              <ManualControls
                enabled={controlsEnabled}
                send={send}
                onStop={() => void stop()}
              />
              <section className="panel sample-status">
                <div
                  className="sample-led"
                  style={
                    {
                      "--led-color": state?.led_color
                        ? SAMPLE_HEX[state.led_color]
                        : "#49414d",
                    } as React.CSSProperties
                  }
                />
                <div>
                  <span className="micro">SAMPLE INDICATOR</span>
                  <strong>
                    {state?.led_color
                      ? `${state.led_color} sample`
                      : "No sample collected"}
                  </strong>
                  <small>
                    {state?.mode === "simulation"
                      ? "Simulated onboard RGB"
                      : "Hardware acknowledgement required"}
                  </small>
                </div>
                <Gem size={19} />
              </section>
              <section className="panel home-panel">
                <div>
                  <Home size={17} />
                  <h2>The journey home</h2>
                </div>
                <p>
                  {phase === "returning"
                    ? "Use manual controls to return to base, then confirm your arrival."
                    : "Your mission ends where it began."}
                </p>
                <Button
                  variant="outline"
                  className="return-button"
                  disabled={
                    !connected ||
                    !isPilot ||
                    busy ||
                    !state?.capabilities.return ||
                    !["collected", "returning"].includes(phase)
                  }
                  onClick={() =>
                    void run(phase === "returning" ? "mark_home" : "return")
                  }
                >
                  {phase === "returning" ? (
                    <Flag size={15} />
                  ) : (
                    <Home size={15} />
                  )}{" "}
                  {phase === "returning"
                    ? "Mark home · finish mission"
                    : "Return home"}
                  <ChevronRight size={14} />
                </Button>
              </section>
            </aside>
          </div>
          <section
            className="panel activity-panel"
            ref={activity}
            id="activity"
            aria-label="Mission activity"
          >
            <div className="panel-heading">
              <div className="heading-icon">
                <Terminal size={16} />
                <h2>Mission log</h2>
                <span className="log-count">
                  {state?.events.length || 0} EVENTS
                </span>
              </div>
              <span className="micro muted">LOCAL TIME</span>
            </div>
            <div className="log-entries">
              {state?.events.length ? (
                state.events.map((event) => (
                  <div className={`log-row ${event.level}`} key={event.id}>
                    <time>
                      {new Date(event.at).toLocaleTimeString([], {
                        hour12: false,
                      })}
                    </time>
                    <span className="log-dot" />
                    <p>{event.message}</p>
                    <span className="log-level">
                      {event.level === "success"
                        ? "SUCCESS"
                        : event.level === "warning"
                          ? "NOTICE"
                          : "SYSTEM"}
                    </span>
                  </div>
                ))
              ) : (
                <div className="log-empty">Waiting for Mission Control…</div>
              )}
            </div>
          </section>
          <footer className="app-footer">
            <span>
              <Crosshair size={13} />
              BULLSEYE<span className="footer-divider">/</span>BUILT FOR
              DISCOVERY
            </span>
            <span>
              UB HACKATHON 2026<span className="footer-divider">·</span>EARTH →
              THE NEXT FRONTIER
            </span>
          </footer>
        </main>
      </div>
      {error && (
        <div className="error-toast" role="alert">
          <Info size={19} />
          <p>{error}</p>
          <button aria-label="Dismiss message" onClick={clearError}>
            <X size={17} />
          </button>
        </div>
      )}
    </>
  );
}
