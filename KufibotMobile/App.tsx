import React, { useEffect, useState } from 'react';
import { Image, Modal, Pressable, ScrollView, StatusBar, StyleSheet, Switch, Text, TextInput, View } from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import Slider from '@react-native-community/slider';
import { useKeepAwake } from 'expo-keep-awake';
import { discover, Robot } from './src/discovery';
import { Joystick } from './src/Joystick';
import { useRobot } from './src/useRobot';

function Controller() {
  useKeepAwake();
  const [robots, setRobots] = useState<Robot[]>([]);
  const [selected, setSelected] = useState<Robot | null>(null);
  const [menu, setMenu] = useState(false);
  const [host, setHost] = useState('');
  const [discoveryError, setDiscoveryError] = useState('');
  const link = useRobot(selected);
  const s = link.state;
  const enabled = !!s?.owner && s.mode === 'remote' && s.appliedMode === 'remote' && !menu;
  useEffect(() => {
    try {
      return discover(robot => {
        setRobots(previous => previous.some(r => r.host === robot.host && r.port === robot.port)
          ? previous : [...previous, robot]);
        setSelected(previous => previous ?? robot);
        setDiscoveryError('');
      }, setDiscoveryError);
    } catch (error) { setDiscoveryError(`Keşif başlatılamadı: ${String(error)}`); }
  }, []);
  const value = (key: string, unit: string) => {
    const v = s?.sensors[key];
    return typeof v === 'number' && Number.isFinite(v) ? `${v.toFixed(1)} ${unit}` : `— ${unit}`;
  };
  const joint = (name: string, angle: number) => link.send({type: 'joint', name, value: angle});
  const openMenu = () => { link.stop(); setMenu(true); };
  const manual = () => {
    const match = host.trim().match(/^(\d{1,3}(?:\.\d{1,3}){3})(?::(\d{1,5}))?$/);
    if (!match || match[1].split('.').some(n => Number(n) > 255) ||
        (match[2] && (Number(match[2]) < 1 || Number(match[2]) > 65535))) {
      setDiscoveryError('Geçerli IPv4 adresi girin: 192.168.1.20:8080'); return;
    }
    link.stop(); setSelected({host: match[1], port: Number(match[2] ?? 8080), name: 'Kufibot'}); setMenu(false);
  };
  return <View style={styles.screen}>
    <StatusBar hidden/>
    {link.frame && s?.camera ? <Image source={{uri: link.frame}} style={StyleSheet.absoluteFill} resizeMode="contain"/> :
      <View style={styles.placeholder}><View style={styles.reticle}/><Text style={styles.cameraTitle}>KUFIBOT</Text>
        <Text style={styles.muted}>{s ? 'Kamera görüntüsü bekleniyor' : 'Aynı Wi-Fi ağındaki robot aranıyor…'}</Text></View>}
    <SafeAreaView style={styles.overlay}>
      <View style={styles.top}>
        <View style={styles.sensorGroup}>
          <Pressable accessibilityLabel="Bağlantı menüsü" onPress={openMenu}><Text style={styles.brand}>☰  KUFIBOT</Text></Pressable>
          <Text style={styles.sensor}>AKIM       {value('current', 'A')}</Text>
          <Text style={styles.sensor}>GERİLİM  {value('voltage', 'V')}</Text>
        </View>
        <View style={styles.centerTop}>
          <View style={styles.modes}>{(['remote', 'ai'] as const).map(mode =>
            <Pressable key={mode} disabled={!s?.owner} onPress={() => {link.stop(); link.send({type: 'mode', mode});}}
              style={[styles.mode, s?.mode === mode && s.appliedMode === mode && styles.selected]}>
              <Text style={styles.buttonText}>{mode === 'remote' ? 'KUMANDA' : 'YZ MODU'}</Text>
            </Pressable>)}</View>
          <Text style={styles.connection}>{link.connection}{selected ? ` · ${selected.host}` : ''}</Text>
          {!!s && s.appliedMode !== s.mode && <Text style={styles.warning}>Servo kontrolü bekleniyor</Text>}
          {!!link.error && <Text style={styles.warning}>{link.error}</Text>}
        </View>
        <View style={styles.sensorGroup}>
          <Text style={[styles.brand, {textAlign: 'right'}]}>CANLI GÖRÜNTÜ</Text>
          <Text style={styles.sensor}>PUSULA  {value('heading', '°')}</Text>
          <Text style={styles.sensor}>MESAFE  {value('distance', 'm')}</Text>
        </View>
      </View>
      <View style={styles.bottom}>
        <View style={styles.controls}>
          <View style={styles.eye}><Text style={styles.small}>SOL GÖZ</Text><Switch disabled={!enabled}
            value={(s?.joints.eyeLeft ?? 30) < 20} onValueChange={v => joint('eyeLeft', v ? 0 : 30)} trackColor={{true: '#a5cd55'}}/></View>
          <Joystick label={s?.driveAvailable ? 'HAREKET' : 'HAREKET · MOTOR YOK'} disabled={!enabled || !s?.driveAvailable}
            onChange={(x, y) => link.input('drive', x, y)}/>
          <Text style={styles.small}>SOL KOL</Text><Slider style={styles.slider} disabled={!enabled}
            minimumValue={109} maximumValue={180} value={s?.joints.leftArm ?? 170}
            minimumTrackTintColor="#b8e75c" thumbTintColor="#b8e75c" onSlidingComplete={v => joint('leftArm', v)}/>
        </View>
        <View style={styles.centerBottom}>
          <Pressable style={styles.stop} onPress={link.stop} accessibilityLabel="Hareketi durdur"><Text style={styles.stopText}>DUR</Text></Pressable>
          <Text style={styles.hint}>{s?.mode === 'ai' ? 'YZ hareket kontrolü etkin' : 'Joystick bırakıldığında hareket durur'}</Text>
        </View>
        <View style={styles.controls}>
          <View style={styles.eye}><Text style={styles.small}>SAĞ GÖZ</Text><Switch disabled={!enabled}
            value={(s?.joints.eyeRight ?? 150) > 160} onValueChange={v => joint('eyeRight', v ? 170 : 150)} trackColor={{true: '#a5cd55'}}/></View>
          <Joystick label="KAFA" disabled={!enabled} onChange={(x, y) => link.input('head', x, y)}/>
          <Text style={styles.small}>SAĞ KOL</Text><Slider style={styles.slider} disabled={!enabled}
            minimumValue={10} maximumValue={72} value={s?.joints.rightArm ?? 15}
            minimumTrackTintColor="#b8e75c" thumbTintColor="#b8e75c" onSlidingComplete={v => joint('rightArm', v)}/>
        </View>
      </View>
      {!selected && <Pressable onPress={openMenu} style={styles.connectButton}><Text style={styles.buttonText}>BAĞLANTI AYARLARI</Text></Pressable>}
    </SafeAreaView>
    <Modal visible={menu} transparent animationType="fade" onRequestClose={() => setMenu(false)}>
      <View style={styles.scrim}><View style={styles.panel}><ScrollView keyboardShouldPersistTaps="handled">
        <Text style={styles.panelTitle}>Robot bağlantısı</Text><Text style={styles.muted}>Aynı Wi-Fi ağı · UDP keşfi / WebSocket kontrolü</Text>
        {robots.map(robot => <Pressable key={`${robot.host}:${robot.port}`} style={styles.robot} onPress={() => {
          link.stop(); setSelected(robot); setMenu(false);
        }}><Text style={styles.buttonText}>{robot.name} · {robot.host}:{robot.port}</Text></Pressable>)}
        {!robots.length && <Text style={styles.help}>Robot aranıyor. Robot ile telefon aynı ağda olmalı; ağdaki cihaz izolasyonu kapalı olmalı.</Text>}
        <TextInput value={host} onChangeText={setHost} placeholder="192.168.1.20:8080" placeholderTextColor="#799099"
          style={styles.input} autoCapitalize="none" autoCorrect={false} keyboardType="numbers-and-punctuation" accessibilityLabel="Robot IP adresi"/>
        {!!discoveryError && <Text style={styles.warning}>{discoveryError}</Text>}
        <Pressable style={styles.robot} onPress={manual}><Text style={styles.buttonText}>IP ile bağlan</Text></Pressable>
        {!!s && !s.owner && <Pressable style={styles.robot} onPress={() => link.send({type: 'claim'})}><Text style={styles.buttonText}>Kumandayı devral</Text></Pressable>}
        <Text style={styles.help}>Kumanda modu YZ hareketlerini engeller. YZ modu mevcut takip ve sesli asistan hareketlerini açar. Sesli görüşme robotun mikrofonunda sürer.</Text>
        <Pressable style={styles.robot} onPress={() => setMenu(false)}><Text style={styles.buttonText}>Kapat</Text></Pressable>
      </ScrollView></View></View>
    </Modal>
  </View>;
}
export default function App() { return <SafeAreaProvider><Controller/></SafeAreaProvider>; }
const styles = StyleSheet.create({
  screen: {flex: 1, backgroundColor: '#050b10'}, overlay: {flex: 1, justifyContent: 'space-between', paddingHorizontal: 18, paddingVertical: 8},
  placeholder: {...StyleSheet.absoluteFillObject, alignItems: 'center', justifyContent: 'center'},
  reticle: {width: 64, height: 64, borderColor: '#22353e', borderWidth: 1, borderRadius: 32, marginBottom: 12},
  cameraTitle: {fontSize: 22, letterSpacing: 8, color: '#526b77', marginBottom: 8}, muted: {fontSize: 12, color: '#81959f'},
  top: {flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start'},
  sensorGroup: {gap: 5, width: 160}, brand: {fontWeight: '700', fontSize: 12, color: '#e5eef2', letterSpacing: 1, paddingVertical: 6},
  sensor: {color: '#ffd290', backgroundColor: '#2e2419dd', borderLeftColor: '#eda347', borderLeftWidth: 2, padding: 6, fontSize: 12, fontVariant: ['tabular-nums']},
  centerTop: {alignItems: 'center', flex: 1, paddingHorizontal: 8}, modes: {flexDirection: 'row', backgroundColor: '#101e27dd', borderRadius: 8, padding: 3},
  mode: {paddingHorizontal: 14, paddingVertical: 12, borderRadius: 6}, selected: {backgroundColor: '#3b6178'},
  buttonText: {fontSize: 12, color: '#eef5f7', fontWeight: '600'}, connection: {color: '#b8d59a', fontSize: 11, marginTop: 7},
  warning: {color: '#ffc27a', fontSize: 11, marginTop: 4},
  bottom: {flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-end'}, controls: {width: 170, alignItems: 'center'},
  eye: {flexDirection: 'row', alignItems: 'center', height: 32, marginBottom: 4}, small: {color: '#c5d5dc', fontSize: 10, letterSpacing: 1}, slider: {width: 170, height: 26},
  centerBottom: {alignItems: 'center', flex: 1, marginBottom: 22}, stop: {borderColor: '#fb7777', borderWidth: 1, borderRadius: 12, backgroundColor: '#6a2025dd', paddingHorizontal: 28, paddingVertical: 13},
  stopText: {fontWeight: '700', letterSpacing: 3, color: '#fff'}, hint: {fontSize: 10, color: '#aebfc7', marginTop: 10, textAlign: 'center'},
  connectButton: {position: 'absolute', alignSelf: 'center', top: '48%', padding: 14, backgroundColor: '#294458', borderRadius: 8},
  scrim: {flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: '#000b'}, panel: {width: '75%', maxWidth: 560, maxHeight: '90%', borderRadius: 16, padding: 22, backgroundColor: '#12222d'},
  panelTitle: {fontSize: 22, color: '#fff', fontWeight: '600', marginBottom: 8}, robot: {backgroundColor: '#253e4e', padding: 14, marginTop: 10, borderRadius: 8},
  input: {color: '#fff', borderColor: '#516c7e', borderWidth: 1, borderRadius: 8, padding: 12, marginTop: 14}, help: {fontSize: 12, color: '#a4b9c4', marginTop: 12, lineHeight: 18},
});
