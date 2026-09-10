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
