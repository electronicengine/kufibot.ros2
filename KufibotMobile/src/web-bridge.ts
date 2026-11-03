import {NetworkHandoff, Robot, robotURL} from './discovery-state';

export type NativeMessage =
  | {action: 'ready' | 'wifi' | 'discovery' | 'share' | 'browser'}
  | {action: 'connection'; connected: boolean}
  | {action: 'networkFailed'; message: string}
  | {action: 'networkHandoff'; handoff: NetworkHandoff};

export function sameRobotOrigin(robot: Robot, url: string): boolean {
  try { return new URL(url).origin === new URL(robotURL(robot)).origin; }
  catch { return false; }
}

export function parseNativeMessage(raw: string, source: string, robot: Robot,
    now = Date.now()): NativeMessage | null {
  if (raw.length > 4096 || !sameRobotOrigin(robot, source)) return null;
  try {
    const value = JSON.parse(raw);
    if (value?.type !== 'kufibotNative' || value.version !== 1) return null;
    if (['ready', 'wifi', 'discovery', 'share', 'browser'].includes(value.action)) {
      return {action: value.action};
    }
    if (value.action === 'connection' && typeof value.connected === 'boolean') {
      return {action: 'connection', connected: value.connected};
    }
    if (value.action === 'networkFailed' && typeof value.message === 'string') {
      return {action: 'networkFailed', message: value.message.slice(0, 500)};
    }
    if (value.action === 'networkHandoff' && typeof value.robot_id === 'string' &&
        value.robot_id.length > 0 && value.robot_id.length <= 128 &&
        (!robot.robotId || robot.robotId === value.robot_id) &&
        typeof value.handoff === 'string' && value.handoff.length >= 16 && value.handoff.length <= 128 &&
        typeof value.ssid === 'string' && value.ssid.length > 0 && value.ssid.length <= 32) {
      return {action: 'networkHandoff', handoff: {
        robotId: value.robot_id, ticket: value.handoff, ssid: value.ssid, started: now,
        ...(value.target === 'setup' ? {target: 'setup' as const} : {}),
      }};
    }
  } catch { /* Malformed page messages never invoke native actions. */ }
  return null;
}
