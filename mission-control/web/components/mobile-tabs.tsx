import { useRef } from "react";
import { Gamepad2, Orbit } from "lucide-react";

export type MobileView = "mission" | "pilot";

export function MobileTabs({
  value,
  onChange,
}: {
  value: MobileView;
  onChange: (view: MobileView) => void;
}) {
  const mission = useRef<HTMLButtonElement>(null);
  const pilot = useRef<HTMLButtonElement>(null);
  return (
    <div
      className="mobile-tabs"
      role="tablist"
      aria-label="Mission Control view"
    >
      {(["mission", "pilot"] as const).map((view) => (
        <button
          key={view}
          type="button"
          ref={view === "mission" ? mission : pilot}
          id={`tab-${view}`}
          role="tab"
          aria-selected={value === view}
          aria-controls="dashboard-content"
          tabIndex={value === view ? 0 : -1}
          onClick={() => onChange(view)}
          onKeyDown={(event) => {
            if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key))
              return;
            event.preventDefault();
            const next =
              event.key === "Home"
                ? "mission"
                : event.key === "End"
                  ? "pilot"
                  : view === "mission"
                    ? "pilot"
                    : "mission";
            onChange(next);
            (next === "mission" ? mission : pilot).current?.focus();
          }}
        >
          {view === "mission" ? <Orbit size={17} /> : <Gamepad2 size={17} />}
          {view === "mission" ? "Mission" : "Pilot"}
        </button>
      ))}
    </div>
  );
}
