export type Robot = {
  host: string; port: number; name: string;
  robotId?: string; stationConnected?: boolean; setupActive?: boolean;
};

export type NetworkHandoff = {
  robotId: string; ticket: string; ssid: string; started: number; target?: 'setup';
};

export const robotURL = (robot: Robot) => `http://${robot.host}:${robot.port}/`;

export function sameEndpoint(a: Robot, b: Robot) {
  return a.host === b.host && a.port === b.port;
}

export function selectDiscovered(current: Robot | null, found: Robot): Robot {
  if (!current) return found;
  if (current.robotId) return current.robotId === found.robotId ? found : current;
  return sameEndpoint(current, found) ? found : current;
}

export function handoffCandidate(pending: NetworkHandoff, found: Robot, now = Date.now()) {
  return now - pending.started < 15 * 60 * 1000 &&
    found.robotId === pending.robotId && (pending.target === 'setup'
      ? found.setupActive === true && !found.stationConnected
      : found.stationConnected === true && !found.setupActive);
}

export function verifiedHandoff(pending: NetworkHandoff, value: unknown): boolean {
  if (!value || typeof value !== 'object') return false;
  const identity = value as {robot_id?: string; connected?: boolean};
  return identity.robot_id === pending.robotId && identity.connected === true;
}
