import { AppState } from 'react-native';
import { requireNativeModule } from 'expo';

export type Robot = { host: string; port: number; name: string };

export function discover(onRobot: (robot: Robot) => void, onError: (message: string) => void) {
  const native = requireNativeModule<{scan(): Promise<Robot[]>}>('KufibotDiscovery');
  let timer: ReturnType<typeof setTimeout> | undefined;
  let closed = false;
  const scan = async () => {
    try {
      if (AppState.currentState === 'active') {
        const robots = await native.scan();
        if (!closed) robots.forEach(onRobot);
      }
    } catch (error) {
      if (!closed) onError(String(error));
    } finally {
      if (!closed) timer = setTimeout(scan, 2000);
    }
  };
  void scan();
  return () => { closed = true; clearTimeout(timer); };
}
