import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MASK_TOOL, type FrozenVision, type MaskView, type VisionOverlayOptions } from "@/lib/vision";

/** Freeze once, sample those exact pixels, then resume live to verify. */
export function useVisionUi(act: (type: string, payload?: Record<string, unknown>) => Promise<boolean>, canEdit = true) {
  const [show, setShow] = useState(true);
  const [floor, setFloor] = useState(false);
  const [mask, setMask] = useState<MaskView>("off");
  const [samplingClass, selectClass] = useState<string | null>(null);
  const [calibrating, setCalibrating] = useState(false);
  const [frozen, setFrozen] = useState<FrozenVision | null>(null);
  const [freezeError, setFreezeError] = useState<string | null>(null);
  const [freezing, setFreezing] = useState(false);
  const generation = useRef(0);
  const resumeLive = useCallback(() => {
    generation.current++;
    setFrozen(null);
    selectClass(null);
    setFreezing(false);
    setFreezeError(null);
  }, []);
  // The pilot lease is external state: clear the frozen driving view on lease loss.
  // oxlint-disable-next-line react(set-state-in-effect)
  useEffect(() => { if (!canEdit) resumeLive(); }, [canEdit, resumeLive]);
  const setSamplingClass = async (cls: string | null) => {
    const request = ++generation.current;
    selectClass(null);
    setFreezeError(null);
    if (!cls || !canEdit) { setFreezing(false); return; }
    if (!frozen) {
      setFreezing(true);
      try {
        const response = await fetch("/api/vision/calibration-frame", { cache: "no-store" });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail ?? "Could not freeze video.");
        if (request !== generation.current) return;
        setFrozen(data);
      } catch (error) {
        if (request === generation.current) setFreezeError(error instanceof Error ? error.message : "Could not freeze video.");
        return;
      } finally {
        if (request === generation.current) setFreezing(false);
      }
    }
    setMask("off");
    setShow(true);
    selectClass(cls);
  };
  const overlay: VisionOverlayOptions = useMemo(() => ({
    show, floor, mask, samplingClass, frozen, onResumeLive: resumeLive,
    onSampleBox: samplingClass && frozen && samplingClass !== MASK_TOOL
      ? (box) => void act("vision_palette_sample", { cls: samplingClass, box, frame_id: frozen.id }) : undefined,
    onMaskPolygon: samplingClass === MASK_TOOL && frozen
      ? (points) => void act("vision_mask_polygon", { points }) : undefined,
  }), [show, floor, mask, samplingClass, frozen, act, resumeLive]);
  return {
    overlay, show, setShow, floor, setFloor, mask, setMask, samplingClass, setSamplingClass,
    frozen, freezing, freezeError, resumeLive, calibrating,
    setCalibrating: (open: boolean) => { setCalibrating(open); if (!open) resumeLive(); },
  };
}
export type VisionUi = ReturnType<typeof useVisionUi>;
