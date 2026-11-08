"use client";

import HalftoneNebula from "@/components/ui/halftone-nebula";

const SKY = { planetX: 0.74, planetY: 0.2 };

/** The supplied standalone nebula demonstration, separate from Mission Control. */
export default function Demo() {
  return (
    <div className="relative w-full">
      <HalftoneNebula params={SKY}>
        <div className="flex h-full flex-col justify-between p-6 sm:p-10">
          <div className="flex items-center justify-between font-mono text-[10px] uppercase tracking-[0.32em] text-[#f6e2e8]/60">
            <span>sector 07 / crimson drift</span>
            <span className="hidden sm:inline">ra 04h 22m · dec −12°</span>
          </div>
          <div className="max-w-xl">
            <p className="mb-4 font-mono text-[11px] uppercase tracking-[0.34em] text-[#ff1f5a]">
              ✦ transmission received
            </p>
            <h1 className="text-5xl font-semibold leading-[0.95] tracking-tight text-[#f6e2e8] sm:text-7xl">
              Lost in the
              <br />
              red static.
            </h1>
            <p className="mt-5 max-w-sm text-sm leading-relaxed text-[#f6e2e8]/60">
              A nebula printed one dot at a time. Move to light the gas — click
              anywhere to hang a star of your own.
            </p>
            <button
              type="button"
              className="pointer-events-auto mt-8 border border-[#ff1f5a] bg-[#ff1f5a]/10 px-5 py-2.5 font-mono text-[11px] uppercase tracking-[0.28em] text-[#f6e2e8] transition-colors hover:bg-[#ff1f5a] hover:text-[#050309]"
            >
              Begin descent →
            </button>
          </div>
        </div>
      </HalftoneNebula>
    </div>
  );
}
