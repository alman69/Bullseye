// A short "space victory" fanfare made with the Web Audio API (no sound files): a shimmering
// rising arpeggio, a held major chord with a slow vibrato, and a soft echo.
// Browsers only allow audio after a click, so call unlockAudio() from a button handler first.

let ctx: AudioContext | null = null;

export function unlockAudio(): void {
  try {
    const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctor) return;
    ctx ??= new Ctor();
    if (ctx.state === "suspended") void ctx.resume();
  } catch {
    ctx = null;
  }
}

export function playVictory(): void {
  unlockAudio();
  if (!ctx) return;
  const t0 = ctx.currentTime + 0.05;
  const out = ctx.createGain();
  out.gain.value = 0.18;
  // Echo: a delay line feeding back into itself, for a spacey tail.
  const delay = ctx.createDelay();
  delay.delayTime.value = 0.28;
  const feedback = ctx.createGain();
  feedback.gain.value = 0.35;
  delay.connect(feedback).connect(delay);
  out.connect(ctx.destination);
  out.connect(delay);
  delay.connect(ctx.destination);

  const note = (freq: number, start: number, length: number, type: OscillatorType, level: number, vibrato = 0) => {
    const osc = ctx!.createOscillator();
    const env = ctx!.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, start);
    if (vibrato) {
      const lfo = ctx!.createOscillator();
      const depth = ctx!.createGain();
      lfo.frequency.value = 5;
      depth.gain.value = vibrato;
      lfo.connect(depth).connect(osc.frequency);
      lfo.start(start);
      lfo.stop(start + length);
    }
    env.gain.setValueAtTime(0, start);
    env.gain.linearRampToValueAtTime(level, start + 0.03);
    env.gain.exponentialRampToValueAtTime(0.001, start + length);
    osc.connect(env).connect(out);
    osc.start(start);
    osc.stop(start + length + 0.05);
  };

  // Rising arpeggio (C major, two octaves), then a held chord with a high "star" sparkle.
  const arp = [261.6, 329.6, 392.0, 523.3, 659.3, 784.0, 1046.5];
  arp.forEach((f, i) => note(f, t0 + i * 0.09, 0.35, "triangle", 0.6));
  const chordAt = t0 + arp.length * 0.09 + 0.05;
  for (const f of [261.6, 329.6, 392.0, 523.3]) note(f, chordAt, 1.8, "sawtooth", 0.12, 3);
  for (const f of [261.6, 392.0]) note(f / 2, chordAt, 1.8, "sine", 0.5);
  [2093, 2637, 3136, 2637].forEach((f, i) => note(f, chordAt + 0.15 + i * 0.12, 0.25, "sine", 0.15));
}
