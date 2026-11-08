import { useMemo, useState } from "react";
import { MASK_TOOL, parseHsvTool, type MaskView, type VisionOverlayOptions } from "@/lib/vision";

/** Local (per-browser) vision view state: overlay toggles, mask preview, sampling, calibration panel. */
export function useVisionUi(act: (type: string, payload?: Record<string, unknown>) => Promise<boolean>) {
  const [show, setShow] = useState(true);
  const [floor, setFloor] = useState(true);
  const [mask, setMask] = useState<MaskView>("off");
  const [samplingClass, setSamplingClass] = useState<string | null>(null);
  const [calibrating, setCalibrating] = useState(false);
  const overlay: VisionOverlayOptions = useMemo(
    () => ({
      show,
      floor,
      mask,
      samplingClass,
      onSampleBox: samplingClass
        ? (box) =>
            void (samplingClass === MASK_TOOL
              ? act("vision_mask_add", { box })
              : parseHsvTool(samplingClass)
                ? act("vision_hsv_teach", { ...parseHsvTool(samplingClass), box })
                : act("vision_sample", { cls: samplingClass, box }))
        : undefined,
    }),
    [show, floor, mask, samplingClass, act],
  );
  return {
    overlay,
    show,
    setShow,
    floor,
    setFloor,
    mask,
    setMask,
    samplingClass,
    setSamplingClass,
    calibrating,
    setCalibrating: (open: boolean) => {
      setCalibrating(open);
      if (!open) setSamplingClass(null);
    },
  };
}

export type VisionUi = ReturnType<typeof useVisionUi>;
