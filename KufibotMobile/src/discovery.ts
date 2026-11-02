import { AppState } from 'react-native';
import { requireNativeModule } from 'expo';
import { Robot } from './discovery-state';

export type { Robot } from './discovery-state';

export function discover(onRobots: (robots: Robot[]) => void, onError: (message: string) => void) {
  const native = requireNativeModule<{scan(): Promise<Robot[]>}>('KufibotDiscovery');
  let timer: ReturnType<typeof setTimeout> | undefined;
  let closed = false;
  let scanning = false;
  let rescan = false;
  const scan = async () => {
    if (closed) return;
    if (scanning) { rescan = true; return; }
    scanning = true;
    clearTimeout(timer);
    try {
      if (AppState.currentState === 'active') {
        const robots = await native.scan();
        if (!closed && AppState.currentState === 'active') onRobots(robots);
      }
    } catch (error) {
      if (!closed) onError(String(error));
    } finally {
      scanning = false;
      if (!closed) timer = setTimeout(scan, rescan ? 0 : 2000);
      rescan = false;
    }
  };
  const listener = AppState.addEventListener('change', next => {
    if (next === 'active') void scan();
    else clearTimeout(timer);
  });
  void scan();
  return () => { closed = true; clearTimeout(timer); listener.remove(); };
}
