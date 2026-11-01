// Servo-space preview counterpart of kufibot_interaction.mimics.evaluate.
export function evaluateMotion(motion, time) {
  let previous = motion.keyframes[0];
  for (const next of motion.keyframes.slice(1)) {
    if (time < next.time_ms) {
      if (motion.interpolation === "step") return { ...previous.joints };
      const fraction = Math.max(
        0,
        (time - previous.time_ms) / (next.time_ms - previous.time_ms),
      );
      return Object.fromEntries(
        Object.entries(previous.joints).map(([name, angle]) => [
          name,
          angle + (next.joints[name] - angle) * fraction,
        ]),
      );
    }
    previous = next;
  }
  return { ...previous.joints };
}

// Command keyframes are targets, not measured servo positions. Replay the same
// per-tick slew limiter as ServoNode, including travel after the last command.
export function compileServoPreview(motion, profile, initialPose) {
  const {step_deg:step, arm_step_deg:armStep, step_delay_ms:period, command_period_ms:commandPeriod} = profile;
  if (![step, armStep, period, commandPeriod].every(value => typeof value === 'number' && Number.isFinite(value) && value > 0)) {
    throw new Error('Servo hız ayarları geçersiz.');
  }
  const frames = motion.keyframes, names = Object.keys(frames[0].joints);
  const final = frames.at(-1).joints;
  const pose = Object.fromEntries(names.map(name => [name,
    Number.isFinite(initialPose?.[name]) ? initialPose[name] : frames[0].joints[name]]));
  const times = [], positions = [];
  const append = time => { times.push(time); positions.push({...pose}); };
  const settled = () => names.every(name => Math.abs(pose[name] - final[name]) < 1e-8);
  let frameIndex = 0;
  function targetsAt(time) {
    while (frameIndex + 1 < frames.length && frames[frameIndex + 1].time_ms <= time) frameIndex++;
    const previous = frames[frameIndex], next = frames[frameIndex + 1];
    if (!next || motion.interpolation === 'step') return previous.joints;
    const fraction = Math.max(0, (time - previous.time_ms) / (next.time_ms - previous.time_ms));
    return Object.fromEntries(names.map(name => [name,
      previous.joints[name] + (next.joints[name] - previous.joints[name]) * fraction]));
  }
  let targets = targetsAt(0), time = 0, servoTick = 1, commandTick = 1, lastCommand = 0;
  let endMark = motion.duration_ms > 0 ? motion.duration_ms : Infinity;
  append(0);
  while (!(time >= motion.duration_ms && lastCommand >= frames.at(-1).time_ms && settled())) {
    if (times.length >= 120000) throw new Error('Bu hız ayarlarıyla önizleme çok uzun sürüyor.');
    const servoTime = servoTick * period, commandTime = commandTick * commandPeriod;
    time = Math.min(servoTime, commandTime, endMark);
    if (Math.abs(time - commandTime) < 1e-7) {
      targets = targetsAt(time); lastCommand = time; commandTick++;
    }
    if (Math.abs(time - servoTime) < 1e-7) {
      for (const name of names) {
        const delta = targets[name] - pose[name];
        const limit = name === 'leftArm' || name === 'rightArm' ? armStep : step;
        pose[name] += Math.sign(delta) * Math.min(Math.abs(delta), limit);
      }
      servoTick++;
    }
    if (time >= endMark) endMark = Infinity;
    append(time);
  }
  return {times, positions, duration_ms:time, command_duration_ms:motion.duration_ms};
}

export function sampleServoPreview(preview, time) {
  const {times, positions} = preview;
  if (time <= 0) return {...positions[0]};
  if (time >= preview.duration_ms) return {...positions.at(-1)};
  let low = 0, high = times.length - 1;
  while (low + 1 < high) {
    const middle = (low + high) >> 1;
    if (times[middle] <= time) low = middle; else high = middle;
  }
  const fraction = (time - times[low]) / (times[high] - times[low]);
  return Object.fromEntries(Object.entries(positions[low]).map(([name, angle]) => [name,
    angle + (positions[high][name] - angle) * fraction]));
}
