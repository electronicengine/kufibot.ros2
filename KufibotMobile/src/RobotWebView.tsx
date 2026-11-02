import React, {useEffect, useRef} from 'react';
import {ActivityIndicator, AppState, BackHandler, Linking, StyleSheet, Text, View} from 'react-native';
import {WebView} from 'react-native-webview';
import {Robot, robotURL} from './discovery-state';
import {NativeMessage, parseNativeMessage, sameRobotOrigin} from './web-bridge';

type Props = {robot: Robot; path: string; active: boolean;
  onMessage: (message: NativeMessage) => void; onError: (message: string) => void};
const lifecycle = (active: boolean) => active
  ? "window.dispatchEvent(new Event('pageshow'));window.dispatchEvent(new Event('focus'));true;"
  : "window.dispatchEvent(new Event('blur'));window.dispatchEvent(new Event('pagehide'));true;";

export function RobotWebView({robot, path, active, onMessage, onError}: Props) {
  const web = useRef<WebView>(null);
  const ready = useRef(false);
  const current = useRef({active, onMessage, onError});
  current.current = {active, onMessage, onError};
  useEffect(() => {
    web.current?.injectJavaScript(lifecycle(active && AppState.currentState === 'active'));
  }, [active]);
  useEffect(() => {
    const listener = AppState.addEventListener('change', state => {
      web.current?.injectJavaScript(lifecycle(state === 'active' && current.current.active));
    });
    const back = BackHandler.addEventListener('hardwareBackPress', () => {
      if (!current.current.active) return false;
      if (ready.current) web.current?.injectJavaScript("window.dispatchEvent(new Event('kufibot-native-back'));true;");
      else current.current.onMessage({action: 'discovery'});
      return true;
    });
    return () => {
      web.current?.injectJavaScript(lifecycle(false));
      listener.remove(); back.remove();
    };
  }, []);
  return <WebView ref={web} testID="robot-web-interface"
    source={{uri: robotURL(robot) + path.replace(/^\//, '')}}
    originWhitelist={[new URL(robotURL(robot)).origin, 'about:*']}
    javaScriptEnabled domStorageEnabled allowsInlineMediaPlayback allowsFullscreenVideo
    mediaPlaybackRequiresUserAction={false} setSupportMultipleWindows={false}
    androidLayerType="hardware" startInLoadingState
    style={styles.web}
    onShouldStartLoadWithRequest={request => {
      if (request.url === 'about:blank' || sameRobotOrigin(robot, request.url)) return true;
      if (/^https?:\/\//i.test(request.url)) {
        void Linking.openURL(request.url).catch(() => current.current.onError('Bağlantı tarayıcıda açılamadı.'));
      }
      return false;
    }}
    onLoadStart={() => { ready.current = false; current.current.onMessage({action: 'connection', connected: false}); }}
    onLoadEnd={() => {
      web.current?.injectJavaScript(lifecycle(current.current.active && AppState.currentState === 'active'));
    }}
    onMessage={event => {
      const value = parseNativeMessage(event.nativeEvent.data, event.nativeEvent.url, robot);
      if (!value) return;
      if (value.action === 'ready') ready.current = true;
      current.current.onMessage(value);
    }}
    onError={() => {
      ready.current = false;
      current.current.onMessage({action: 'connection', connected: false});
      current.current.onError('Robot arayüzü yüklenemedi. Telefonun Wi-Fi bağlantısını kontrol edin.');
    }}
    onHttpError={event => {
      if (event.nativeEvent.statusCode >= 500) current.current.onError('Robot arayüzü başlatılıyor. Biraz sonra yeniden deneyin.');
    }}
    onRenderProcessGone={() => {
      ready.current = false;
      current.current.onMessage({action: 'connection', connected: false});
      current.current.onError('Arayüz kapandı. Yeniden yükleyin.');
    }}
    renderLoading={() => <View style={styles.loading}><ActivityIndicator color="#8bb1ff"/>
      <Text style={styles.text}>Robot arayüzü açılıyor…</Text></View>}
    renderError={() => <View style={styles.loading}><Text style={styles.text}>Robot bağlantısı bekleniyor.</Text></View>}
  />;
}
const styles = StyleSheet.create({
  web: {flex: 1, backgroundColor: '#101722'},
  loading: {...StyleSheet.absoluteFillObject, alignItems: 'center', justifyContent: 'center',
    gap: 12, backgroundColor: '#101722'},
  text: {color: '#eef4ff', fontSize: 14},
});
