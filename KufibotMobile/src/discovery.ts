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
  let emptyScans = 0;
  let reportedError = false;
  const scan = async () => {
    if (closed) return;
    if (scanning) { rescan = true; return; }
    scanning = true;
    clearTimeout(timer);
    try {
      if (AppState.currentState === 'active') {
        const robots = await native.scan();
        if (!closed && AppState.currentState === 'active') {
          onRobots(robots);
          emptyScans = robots.length ? 0 : emptyScans + 1;
          if (robots.length && reportedError) { onError(''); reportedError = false; }
          else if (emptyScans >= 2) {
            reportedError = true;
            onError('Robot keşif isteğine yanıt vermiyor. Telefon ve robotun aynı Wi-Fi ağında olduğunu kontrol edin. IP adresini biliyorsanız aşağıdan bağlanabilirsiniz. Arama devam ediyor.');
          }
        }
      }
    } catch (error) {
      if (!closed) { reportedError = true; onError(error instanceof Error ? error.message : String(error)); }
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
