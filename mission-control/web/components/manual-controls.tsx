import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  Gamepad2,
  Square,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import type { SendCommand } from "@/lib/protocol";

const DIRECTIONS: Record<string, [number, number]> = {
  ArrowUp: [1, 0],
  w: [1, 0],
  ArrowDown: [-1, 0],
  s: [-1, 0],
  ArrowLeft: [0, -1],
  a: [0, -1],
  ArrowRight: [0, 1],
  d: [0, 1],
};

export function ManualControls({
  enabled,
  send,
  onStop,
}: {
  enabled: boolean;
  send: SendCommand;
  onStop: () => void;
}) {
  const [speed, setSpeed] = useState(35);
  const [active, setActive] = useState<string | null>(null);
  const interval = useRef<ReturnType<typeof setInterval> | null>(null);
  const held = useRef<string | null>(null);
  const sendRef = useRef(send);
  sendRef.current = send;
  const end = useCallback(() => {
    const wasDriving = held.current !== null;
    if (interval.current) clearInterval(interval.current);
    interval.current = null;
    held.current = null;
    setActive(null);
    if (wasDriving)
      void sendRef.current("drive", { linear: 0, angular: 0 }).catch(() => {});
  }, []);
  const begin = useCallback(
    (key: string) => {
      if (!enabled || held.current === key) return;
      end();
      held.current = key;
      setActive(key);
      const [linear, angular] = DIRECTIONS[key];
      const renew = () => {
        void sendRef
          .current("drive", {
            linear: (linear * speed) / 100,
            angular: (angular * speed) / 100,
          })
          .catch(end);
      };
      renew();
      interval.current = setInterval(renew, 100);
    },
    [enabled, end, speed],
  );
  useEffect(() => {
    if (!enabled) end();
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if (
        target.closest(
          "input,textarea,select,button,[role=slider],[role=dialog],[contenteditable=true]",
        )
      )
        return;
      const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
      if (DIRECTIONS[key] && enabled) {
        event.preventDefault();
        begin(key);
      }
    };
    const onKeyUp = (event: KeyboardEvent) => {
      const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
      if (held.current === key) {
        event.preventDefault();
        end();
      }
    };
    const visibility = () => {
      if (document.hidden) end();
    };
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    window.addEventListener("blur", end);
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);
    document.addEventListener("visibilitychange", visibility);
    return () => {
      end();
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      window.removeEventListener("blur", end);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [enabled, begin, end]);
  const directionButton = (
    key: string,
    label: string,
    icon: React.ReactNode,
    className: string,
  ) => (
    <button
      type="button"
      className={`drive-key ${className} ${active === key ? "pressed" : ""}`}
      disabled={!enabled}
      aria-label={label}
      onPointerDown={(event) => {
        event.preventDefault();
        event.currentTarget.setPointerCapture(event.pointerId);
        begin(key);
      }}
      onPointerUp={end}
      onPointerCancel={end}
      onLostPointerCapture={end}
    >
      {icon}
    </button>
  );
  return (
    <section className="panel manual-panel">
      <div className="panel-heading">
        <div className="heading-icon">
          <Gamepad2 size={16} />
          <h2>Manual override</h2>
        </div>
        <span className={`small-indicator ${enabled ? "available" : ""}`}>
          {enabled ? "AVAILABLE" : "STANDBY"}
        </span>
      </div>
      <div className="manual-body">
        <div className="drive-pad" aria-label="Hold to drive">
          {directionButton(
            "ArrowUp",
            "Drive forward",
            <ArrowUp size={19} />,
            "drive-up",
          )}
          {directionButton(
            "ArrowLeft",
            "Turn left",
            <ArrowLeft size={19} />,
            "drive-left",
          )}
          <Button
            variant="ghost"
            className="drive-center"
            aria-label="Stop rover"
            onClick={() => {
              end();
              onStop();
            }}
          >
            <Square size={12} fill="currentColor" />
          </Button>
          {directionButton(
            "ArrowRight",
            "Turn right",
            <ArrowRight size={19} />,
            "drive-right",
          )}
          {directionButton(
            "ArrowDown",
            "Drive backward",
            <ArrowDown size={19} />,
            "drive-down",
          )}
        </div>
        <p className="control-hint">
          Hold to drive <span>·</span> WASD or arrow keys
        </p>
        <div className="speed-label">
          <label id="speed-label">Drive speed</label>
          <span>
            {speed}
            <small>%</small>
          </span>
        </div>
        <Slider
          aria-labelledby="speed-label"
          min={10}
          max={70}
          step={5}
          value={[speed]}
          disabled={!enabled}
          onValueChange={(value) => {
            end();
            setSpeed(Array.isArray(value) ? value[0] : value);
          }}
        />
        <p className="manual-note">
          {enabled
            ? "Release to stop. Commands expire after 300 ms."
            : "Claim controls. Manual drive is available before launch and during return."}
        </p>
      </div>
    </section>
  );
}
