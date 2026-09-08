import React, { useEffect, useState } from 'react';
import { RTCView } from 'react-native-webrtc';
import { Modal, Pressable, ScrollView, StatusBar, StyleSheet, Switch, Text, TextInput, View } from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import Slider from '@react-native-community/slider';
import { useKeepAwake } from 'expo-keep-awake';
import { discover, Robot } from './src/discovery';
import { Joystick } from './src/Joystick';
import { useRobot } from './src/useRobot';
import { AiSettingsPanel } from './src/AiSettingsPanel';

function Controller() {
  useKeepAwake();
  const [robots, setRobots] = useState<Robot[]>([]);
  const [selected, setSelected] = useState<Robot | null>(null);
  const [menu, setMenu] = useState(false);
  const [host, setHost] = useState('');
  const [discoveryError, setDiscoveryError] = useState('');
  const [calibrationModal, setCalibrationModal] = useState(false);
  const [aiTriggerUuid, setAiTriggerUuid] = useState('');
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
  const headDirection = typeof s?.sensors.heading === 'number' && Number.isFinite(s.sensors.heading)
    && typeof s?.joints.headLeftRight === 'number' && Number.isFinite(s.joints.headLeftRight)
    ? ((s.sensors.heading + 90 - s.joints.headLeftRight) % 360 + 360) % 360 : null;
  const joint = (name: string, angle: number) => link.send({type: 'joint', name, value: angle});
  const openMenu = () => { link.stop(); setMenu(true); };
  const compassCalibrating = !!s?.calibration?.active;
  const canCalibrateCompass = !!s?.owner && s.mode === 'remote' && s.appliedMode === 'remote' && !compassCalibrating;
  const calibrationValue = (group: 'raw' | 'minimum' | 'maximum', axis: 'x' | 'y') => {
    const value = s?.calibration?.[group]?.[axis];
    return typeof value === 'number' && Number.isFinite(value) ? Math.round(value) : '—';
  };
  const startCompassCalibration = () => {
    link.send({type: 'calibrateCompass'});
    setMenu(false);
    setCalibrationModal(true);
  };
  const validAiTriggerUuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(aiTriggerUuid.trim());
  const startAiWorkflow = () => {
    link.send({type: 'startAiWorkflow', triggerUuid: aiTriggerUuid.trim()});
    setMenu(false);
  };
  useEffect(() => {
    if (s?.aiTriggerUuid) setAiTriggerUuid(s.aiTriggerUuid);
  }, [s?.aiTriggerUuid]);
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
    {link.frame && s?.camera ? <RTCView streamURL={link.frame} style={StyleSheet.absoluteFill} objectFit="contain"/> :
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
          {s?.mode === 'ai' && <>
            <Pressable accessibilityRole="switch" accessibilityState={{checked: !!s.navigation?.enabled}}
              accessibilityLabel="Serbest gezinme"
              disabled={!s.owner || s.aiConfig?.settings.provider !== 'verasist' ||
                s.voiceStatus?.state !== 'connected' || s.appliedMode !== 'ai' || !s.navigation}
              onPress={() => link.send({type: 'setNavigationEnabled', enabled: !s.navigation?.enabled})}
              style={[styles.mode, s.navigation?.enabled && styles.selected]}>
              <Text style={styles.buttonText}>Serbest gezinme{s.navigation?.enabled ? ' · Açık' : ''}</Text>
            </Pressable>
            <Text style={styles.hint}>{s.aiConfig?.settings.provider !== 'verasist'
              ? 'Gezinme yalnızca Verasist ile kullanılabilir'
              : !s.navigation ? 'Gezinme düğümü bekleniyor'
              : (({disabled: 'Kapalı', idle: 'Komut bekleniyor', aligning: 'Kafa hizalanıyor',
                  scanning: 'Taranıyor', advancing: 'İlerleniyor', turning: 'Dönülüyor',
                  waiting_llm: 'LLM bekleniyor', blocked: 'Engellendi', completed: 'Tamamlandı'} as Record<string, string>)[s.navigation.state] || s.navigation.state)
                + (!s.navigation.calibrated ? ' · Hareket kalibrasyonu gerekli' : '')}</Text>
          </>}
          <View style={styles.headDirection} accessibilityLabel={headDirection === null ? 'Kafanın pusulaya göre baktığı yön bilinmiyor' : `Kafanın pusulaya göre baktığı yön ${Math.round(headDirection)} derece`}>
            <Text style={[styles.cardinal, styles.north]}>K</Text><Text style={[styles.cardinal, styles.east]}>D</Text><Text style={[styles.cardinal, styles.south]}>G</Text><Text style={[styles.cardinal, styles.west]}>B</Text>
            <View style={[styles.directionArrow, {transform: [{rotate: `${headDirection ?? 0}deg`}]}]}><View style={styles.arrowTip}/><View style={styles.arrowTail}/></View>
            <Text style={styles.directionValue}>{headDirection === null ? '—°' : `${Math.round(headDirection)}°`}</Text>
          </View>
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
            value={(s?.joints.eyeLeft ?? 30) < 20} onValueChange={v => joint('eyeLeft', v ? 0 : 30)} trackColor={{true: '#4b6fd4'}}/></View>
          <Joystick label={s?.driveAvailable ? 'HAREKET · TAM GÜÇ' : 'HAREKET · MOTOR YOK'} disabled={!enabled || !s?.driveAvailable} fourWay
            onChange={(x, y) => link.input('drive', x, y)}/>
          <Text style={styles.small}>SOL KOL</Text><Slider style={styles.slider} disabled={!enabled}
            minimumValue={109} maximumValue={180} value={s?.joints.leftArm ?? 170}
            minimumTrackTintColor="#4b6fd4" thumbTintColor="#7d9ef0" onSlidingComplete={v => joint('leftArm', v)}/>
        </View>
        <View style={styles.centerBottom}>
          <Pressable style={styles.stop} onPress={link.stop} accessibilityLabel="Hareketi durdur"><Text style={styles.stopText}>DUR</Text></Pressable>
          <Text style={styles.hint}>{s?.mode === 'ai' ? 'Serbest gezinmeyi açıp sesli görev verin' : 'Joystick bırakıldığında hareket durur'}</Text>
        </View>
        <View style={styles.controls}>
          <View style={styles.eye}><Text style={styles.small}>SAĞ GÖZ</Text><Switch disabled={!enabled}
            value={(s?.joints.eyeRight ?? 150) > 160} onValueChange={v => joint('eyeRight', v ? 170 : 150)} trackColor={{true: '#4b6fd4'}}/></View>
          <Joystick label="KAFA" disabled={!enabled} onChange={(x, y) => link.input('head', x, y)}/>
          <Text style={styles.small}>SAĞ KOL</Text><Slider style={styles.slider} disabled={!enabled}
            minimumValue={10} maximumValue={72} value={s?.joints.rightArm ?? 15}
            minimumTrackTintColor="#4b6fd4" thumbTintColor="#7d9ef0" onSlidingComplete={v => joint('rightArm', v)}/>
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
        <AiSettingsPanel config={s?.aiConfig} owner={!!s?.owner} send={link.send}
          status={s?.voiceStatus} error={link.error}/>
        {s?.aiConfig?.settings.provider !== 'local' && <>
        <Text style={styles.panelSection}>YZ İş Akışı</Text>
        <Text style={styles.help}>Verasist iş akışının trigger UUID değerini girin. API anahtarı robotta yapılandırılmış olarak kalır.</Text>
        <TextInput value={aiTriggerUuid} onChangeText={setAiTriggerUuid} placeholder="27eac97b-5e74-494b-984f-01942324fe4b" placeholderTextColor="#799099"
          style={styles.input} autoCapitalize="none" autoCorrect={false} accessibilityLabel="Verasist trigger UUID"/>
        <Pressable disabled={!s?.owner || !validAiTriggerUuid} style={[styles.robot, (!s?.owner || !validAiTriggerUuid) && styles.disabled]} onPress={startAiWorkflow}>
          <Text style={styles.buttonText}>YZ iş akışını başlat</Text>
        </Pressable>
        <Text style={styles.calibrationInfo}>{s?.aiTriggerUuid ? `Seçili UUID: ${s.aiTriggerUuid}` : 'YZ iş akışı UUID bekleniyor'}</Text>
        </>}
        <Text style={styles.panelSection}>HMC Pusula Kalibrasyonu</Text>
        <Text style={styles.help}>Robotu yatay tutun. Başlatınca sensörü yavaşça farklı yönlere, birkaç tam tur döndürün. Metal ve mıknatıslardan uzak tutun.</Text>
        <Pressable disabled={!canCalibrateCompass} style={[styles.robot, !canCalibrateCompass && styles.disabled]} onPress={startCompassCalibration}>
          <Text style={styles.buttonText}>{compassCalibrating ? 'Kalibrasyon sürüyor' : 'Pusulayı kalibre et'}</Text>
        </Pressable>
        <Text style={styles.calibrationInfo}>{s?.calibration ? `${s.calibration.message}${compassCalibrating ? ` (${s.calibration.samples}/${s.calibration.target})` : ''}` : 'Pusula sensörü bekleniyor'}</Text>
        <Text style={styles.help}>Kumanda modu YZ hareketlerini engeller. YZ modu mevcut takip ve sesli asistan hareketlerini açar. Sesli görüşme robotun mikrofonunda sürer.</Text>
        <Pressable style={styles.robot} onPress={() => setMenu(false)}><Text style={styles.buttonText}>Kapat</Text></Pressable>
      </ScrollView></View></View>
    </Modal>
    <Modal visible={calibrationModal} transparent animationType="fade" onRequestClose={() => setCalibrationModal(false)}>
      <View style={styles.scrim}><View style={styles.panel}>
        <Text style={styles.panelTitle}>HMC Pusula Kalibrasyonu</Text>
        <Text style={styles.help}>Robotu yatay tutun ve yavaşça birkaç tam tur, farklı yönlere çevirin. Metal ve mıknatıslardan uzak tutun.</Text>
        <Text style={styles.calibrationInfo}>{s?.calibration ? `${s.calibration.message}${s.calibration.active ? ` (${s.calibration.samples}/${s.calibration.target})` : ''}` : 'Kalibrasyon başlatılıyor'}</Text>
        <View style={styles.calibrationValues}>
          <Text style={styles.calibrationValue}>HAM X  {calibrationValue('raw', 'x')}</Text><Text style={styles.calibrationValue}>HAM Y  {calibrationValue('raw', 'y')}</Text>
          <Text style={styles.calibrationValue}>MİN X  {calibrationValue('minimum', 'x')}</Text><Text style={styles.calibrationValue}>MAKS X  {calibrationValue('maximum', 'x')}</Text>
          <Text style={styles.calibrationValue}>MİN Y  {calibrationValue('minimum', 'y')}</Text><Text style={styles.calibrationValue}>MAKS Y  {calibrationValue('maximum', 'y')}</Text>
        </View>
        <Pressable style={styles.robot} onPress={() => setCalibrationModal(false)}><Text style={styles.buttonText}>Kapat</Text></Pressable>
      </View></View>
    </Modal>
  </View>;
}
export default function App() { return <SafeAreaProvider><Controller/></SafeAreaProvider>; }
const styles = StyleSheet.create({
  screen: {flex: 1, backgroundColor: '#111319'}, overlay: {flex: 1, justifyContent: 'space-between', paddingHorizontal: 18, paddingVertical: 8},
  placeholder: {...StyleSheet.absoluteFillObject, alignItems: 'center', justifyContent: 'center'},
  reticle: {width: 64, height: 64, borderColor: '#4d70d3', borderWidth: 1, borderRadius: 32, marginBottom: 12},
  cameraTitle: {fontSize: 22, letterSpacing: 8, color: '#bdd0f9', marginBottom: 8}, muted: {fontSize: 12, color: '#65758b'},
  top: {flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start'},
  sensorGroup: {gap: 5, width: 160}, brand: {fontWeight: '700', fontSize: 12, color: '#f7f9ff', letterSpacing: 1, paddingVertical: 6},
  sensor: {color: '#e9effd', backgroundColor: '#17233dee', borderLeftColor: '#4b6fd4', borderLeftWidth: 2, padding: 6, fontSize: 12, fontVariant: ['tabular-nums']},
  centerTop: {alignItems: 'center', flex: 1, paddingHorizontal: 8}, modes: {flexDirection: 'row', backgroundColor: '#111319ee', borderRadius: 8, padding: 3},
  mode: {paddingHorizontal: 14, paddingVertical: 12, borderRadius: 6}, selected: {backgroundColor: '#4b6fd4'},
  headDirection: {width: 70, height: 70, marginVertical: 6, borderRadius: 35, borderWidth: 1, borderColor: '#4d70d388', backgroundColor: '#17233dee', overflow: 'hidden'},
  cardinal: {position: 'absolute', color: '#65758b', fontSize: 8, fontWeight: '600'}, north: {top: 4, alignSelf: 'center', color: '#7d9ef0'}, east: {right: 5, top: 30}, south: {bottom: 4, alignSelf: 'center'}, west: {left: 5, top: 30},
  directionArrow: {position: 'absolute', width: 18, height: 46, left: 25, top: 12, alignItems: 'center'}, arrowTip: {width: 0, height: 0, borderLeftWidth: 9, borderRightWidth: 9, borderBottomWidth: 29, borderLeftColor: 'transparent', borderRightColor: 'transparent', borderBottomColor: '#7d9ef0'}, arrowTail: {width: 0, height: 0, marginTop: 1, borderLeftWidth: 6, borderRightWidth: 6, borderTopWidth: 16, borderLeftColor: 'transparent', borderRightColor: 'transparent', borderTopColor: '#2b50b8'}, directionValue: {position: 'absolute', alignSelf: 'center', top: 31, color: '#f7f9ff', fontSize: 10, fontWeight: '600', fontVariant: ['tabular-nums']},
  buttonText: {fontSize: 12, color: '#f7f9ff', fontWeight: '600'}, connection: {color: '#bdd0f9', fontSize: 11, marginTop: 7},
  warning: {color: '#7d9ef0', fontSize: 11, marginTop: 4},
  bottom: {flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-end'}, controls: {width: 170, alignItems: 'center'},
  eye: {flexDirection: 'row', alignItems: 'center', height: 32, marginBottom: 4}, small: {color: '#bdd0f9', fontSize: 10, letterSpacing: 1}, slider: {width: 170, height: 26},
  centerBottom: {alignItems: 'center', flex: 1, marginBottom: 22}, stop: {borderColor: '#fb7777', borderWidth: 1, borderRadius: 12, backgroundColor: '#6a2025dd', paddingHorizontal: 28, paddingVertical: 13},
  stopText: {fontWeight: '700', letterSpacing: 3, color: '#fff'}, hint: {fontSize: 10, color: '#bdd0f9', marginTop: 10, textAlign: 'center'},
  connectButton: {position: 'absolute', alignSelf: 'center', top: '48%', padding: 14, backgroundColor: '#4b6fd4', borderRadius: 8},
  scrim: {flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: '#03040acc'}, panel: {width: '75%', maxWidth: 560, maxHeight: '90%', borderRadius: 16, padding: 22, backgroundColor: '#111319'},
  panelTitle: {fontSize: 22, color: '#f7f9ff', fontWeight: '600', marginBottom: 8}, robot: {backgroundColor: '#2b50b8', padding: 14, marginTop: 10, borderRadius: 8},
  panelSection: {fontSize: 16, color: '#f7f9ff', fontWeight: '600', marginTop: 20}, disabled: {opacity: 0.4}, calibrationInfo: {fontSize: 12, color: '#bdd0f9', marginTop: 10, minHeight: 18},
  calibrationValues: {flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 12}, calibrationValue: {width: '48%', color: '#e9effd', backgroundColor: '#17233d', padding: 10, fontSize: 11, fontVariant: ['tabular-nums']},
  input: {color: '#f7f9ff', borderColor: '#4d70d3', borderWidth: 1, borderRadius: 8, padding: 12, marginTop: 14}, help: {fontSize: 12, color: '#bdd0f9', marginTop: 12, lineHeight: 18},
});
