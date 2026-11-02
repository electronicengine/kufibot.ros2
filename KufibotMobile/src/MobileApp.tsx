import React, {useEffect, useRef, useState} from 'react';
import {ActivityIndicator, Linking, Modal, Pressable, ScrollView, Share, StatusBar, StyleSheet, Text, TextInput, View} from 'react-native';
import {SafeAreaView} from 'react-native-safe-area-context';
import {useKeepAwake} from 'expo-keep-awake';
import {discover, Robot} from './discovery';
import {handoffCandidate, NetworkHandoff, robotURL, selectDiscovered} from './discovery-state';
import {verifyNetworkHandoff} from './network-api';
import {NativeMessage} from './web-bridge';
import {RobotWebView} from './RobotWebView';

export default function MobileApp() {
  useKeepAwake();
  const [robots, setRobots] = useState<Robot[]>([]);
  const [selected, setSelected] = useState<Robot | null>(null);
  const [chooser, setChooser] = useState(false);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [host, setHost] = useState('');
  const [handoff, setHandoff] = useState<NetworkHandoff | null>(null);
  const [entry, setEntry] = useState('/');
  const [reload, setReload] = useState(0);
  const pending = useRef<NetworkHandoff | null>(null);
  useEffect(() => {
    let closed = false, verifying = false;
    let dispose: (() => void) | undefined;
    try {
      dispose = discover(found => {
        setRobots(found);
        const request = pending.current;
        if (request) {
          if (Date.now() - request.started >= 900000) {
            pending.current = null; setHandoff(null);
            setNotice('Ağ geçişi süresi doldu. Telefonun Wi-Fi bağlantısını kontrol edin.');
            return;
          }
          const candidate = found.find(robot => handoffCandidate(request, robot));
          if (!candidate || verifying) return;
          verifying = true;
          void verifyNetworkHandoff(request, candidate).then(valid => {
            if (closed || pending.current !== request || !valid) return;
            pending.current = null; setHandoff(null); setConnected(false);
            setEntry('/#connection'); setSelected(candidate); setReload(value => value + 1);
            setNotice(`“${request.ssid}” bağlantısı doğrulandı. ${robotURL(candidate)}`);
            setError('');
          }).finally(() => { verifying = false; });
          return;
        }
        setSelected(previous => found.reduce<Robot | null>((current, robot) => selectDiscovered(current, robot), previous));
      }, setError);
    } catch (cause) { setError(`Robot araması başlatılamadı: ${String(cause)}`); }
    return () => { closed = true; dispose?.(); };
  }, []);
  const wifi = () => {
    void Linking.sendIntent('android.settings.WIFI_SETTINGS').catch(() => setError('Telefonun Wi-Fi ayarlarını açın.'));
  };
  const choose = (robot: Robot) => {
    pending.current = null; setHandoff(null); setConnected(false);
    setSelected(robot); setEntry('/'); setReload(value => value + 1);
    setChooser(false); setError(''); setNotice('');
  };
  const manual = () => {
    const match = host.trim().match(/^(\d{1,3}(?:\.\d{1,3}){3})(?::(\d{1,5}))?$/);
    if (!match || match[1].split('.').some(part => Number(part) > 255) ||
        (match[2] && (Number(match[2]) < 1 || Number(match[2]) > 65535))) {
      setError('Geçerli bir IP adresi girin. Örnek: 192.168.1.20:8080'); return;
    }
    choose({host: match[1], port: Number(match[2] || 8080), name: 'Kufibot'});
  };
  const message = (value: NativeMessage) => {
    if (value.action === 'connection') { setConnected(value.connected); if (value.connected) setError(''); }
    if (value.action === 'wifi') wifi();
    if (value.action === 'discovery') setChooser(true);
    if (value.action === 'share' && selected) {
      void Share.share({message: robotURL(selected)}).catch(() => setError('Adres paylaşılamadı.'));
    }
    if (value.action === 'browser' && selected) {
      void Linking.openURL(robotURL(selected)).catch(() => setError('Tarayıcı açılamadı.'));
    }
    if (value.action === 'networkHandoff') {
      pending.current = value.handoff; setHandoff(value.handoff);
      setNotice(`Telefonu “${value.handoff.ssid}” ağına geçirin. Aynı robot yeni IP adresinde aranıyor.`);
    }
    if (value.action === 'networkFailed') {
      pending.current = null; setHandoff(null); setNotice(value.message);
    }
  };
  const button = (text: string, action: () => void) => <Pressable accessibilityRole="button" style={styles.button} onPress={action}><Text style={styles.buttonText}>{text}</Text></Pressable>;
  const selection = <ScrollView contentContainerStyle={styles.selection} keyboardShouldPersistTaps="handled">
    <Text style={styles.brand}>Kufi</Text>
    <Text style={styles.heading}>Robotuna bağlan</Text>
    <Text style={styles.help}>Telefonunu Kufi ile aynı Wi-Fi ağına bağla. Robot bulunduğunda arayüz otomatik açılır.</Text>
    {!robots.length && <View style={styles.search}><ActivityIndicator color="#8bb1ff"/><Text style={styles.text}>Kufi aranıyor…</Text></View>}
    {robots.map(robot => <Pressable key={robotURL(robot)} style={styles.robot} onPress={() => choose(robot)}>
      <Text style={styles.robotName}>{robot.name}</Text><Text selectable style={styles.help}>{robotURL(robot)}</Text>
    </Pressable>)}
    {button('Telefonun Wi-Fi ayarlarını aç', wifi)}
    <Text style={styles.help}>İnternet bağlantısı gerekmez. İlk kurulumda telefonunu robotun “kufi” ağına bağlayabilirsin.</Text>
    <View style={styles.manual}><Text style={styles.text}>IP adresini biliyorsan</Text>
      <TextInput accessibilityLabel="Robot IP adresi" value={host} onChangeText={setHost}
        placeholder="192.168.1.20:8080" placeholderTextColor="#a9bbd4" style={styles.input}
        autoCapitalize="none" autoCorrect={false} keyboardType="numbers-and-punctuation"/>
      {button('IP ile bağlan', manual)}
    </View>
    {!!error && <Text accessibilityRole="alert" style={styles.error}>{error}</Text>}
    {!!selected && button('Robot arayüzüne dön', () => setChooser(false))}
  </ScrollView>;
  return <SafeAreaView style={styles.screen}>
    <StatusBar hidden/>
    {!selected ? selection : <>
      {!connected && <View style={styles.toolbar}>
        {button('Robotlar', () => setChooser(true))}
        <Text selectable style={styles.address}>{selected.host} · Bağlantı bekleniyor</Text>
        {button('Yeniden yükle', () => { setError(''); setReload(value => value + 1); })}
      </View>}
      {!!handoff && <View style={styles.notice}><Text accessibilityLiveRegion="polite" style={styles.text}>{notice}</Text>
        {button('Telefonun Wi-Fi ayarlarını aç', wifi)}
        {button('Geçiş takibini bırak', () => { pending.current = null; setHandoff(null); setNotice(''); })}
      </View>}
      {!!error && !chooser && <View style={styles.notice}><Text accessibilityRole="alert" style={styles.error}>{error}</Text>
        {button('Wi-Fi ayarları', wifi)}</View>}
      <RobotWebView key={`${robotURL(selected)}:${reload}`} robot={selected} path={entry}
        active={!chooser} onMessage={message} onError={setError}/>
    </>}
    <Modal visible={chooser && !!selected} animationType="slide" onRequestClose={() => setChooser(false)}>
      <SafeAreaView style={styles.screen}>{selection}</SafeAreaView>
    </Modal>
  </SafeAreaView>;
}
const styles = StyleSheet.create({
  screen: {flex: 1, backgroundColor: '#101722'},
  selection: {flexGrow: 1, padding: 24, gap: 16, maxWidth: 620, width: '100%', alignSelf: 'center', justifyContent: 'center'},
  brand: {fontSize: 30, color: '#eef4ff', fontWeight: '600'}, heading: {fontSize: 22, fontWeight: '600', color: '#eef4ff'},
  help: {fontSize: 14, color: '#a9bbd4', lineHeight: 21}, text: {fontSize: 14, color: '#eef4ff'},
  search: {flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 16},
  robot: {backgroundColor: '#182235', borderColor: '#30445e', borderWidth: 1, borderRadius: 10, padding: 16, gap: 6},
  robotName: {fontSize: 17, color: '#eef4ff', fontWeight: '600'},
  button: {minHeight: 44, backgroundColor: '#253b5c', paddingHorizontal: 14, paddingVertical: 12, borderRadius: 8, justifyContent: 'center'},
  buttonText: {fontSize: 13, color: '#eef4ff', fontWeight: '500'},
  input: {minHeight: 44, borderWidth: 1, borderColor: '#30445e', borderRadius: 8, color: '#eef4ff', padding: 12},
  manual: {gap: 10, borderTopColor: '#30445e', borderTopWidth: 1, paddingTop: 16},
  toolbar: {flexDirection: 'row', alignItems: 'center', gap: 8, padding: 8},
  address: {flex: 1, color: '#a9bbd4', fontSize: 12},
  notice: {padding: 12, gap: 8, borderBottomWidth: 1, borderBottomColor: '#30445e'},
  error: {fontSize: 13, color: '#ffb7a8', lineHeight: 20},
});
