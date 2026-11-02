import {NetworkHandoff, Robot, robotURL, verifiedHandoff} from './discovery-state';

export async function networkRequest<T = any>(robot: Robot, token: string | undefined,
    path: string, data?: object): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(`${robotURL(robot)}api/device-settings/${path}`, {
      method: data ? 'POST' : 'GET', signal: controller.signal,
      headers: {...(token ? {Authorization: `Bearer ${token}`} : {}),
        ...(data ? {'Content-Type': 'application/json'} : {})},
      ...(data ? {body: JSON.stringify(data)} : {}),
    });
    const value = await response.json();
    if (!response.ok) throw new Error(value.error || 'Ağ işlemi tamamlanamadı.');
    return value;
  } finally { clearTimeout(timer); }
}

export async function verifyNetworkHandoff(pending: NetworkHandoff, robot: Robot): Promise<boolean> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 2500);
  try {
    const url = `${robotURL(robot)}api/network-identity?handoff=${encodeURIComponent(pending.ticket)}`;
    const response = await fetch(url, {signal: controller.signal, cache: 'no-store'});
    return response.ok && verifiedHandoff(pending, await response.json());
  } catch { return false; }
  finally { clearTimeout(timer); }
}
