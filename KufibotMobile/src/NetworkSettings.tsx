import React, {useEffect, useRef, useState} from 'react';
import {Linking, Pressable, StyleSheet, Text, TextInput, View} from 'react-native';
import {NetworkHandoff, Robot} from './discovery-state';
import {networkRequest} from './network-api';

type Network = {id: string; ssid: string; security: string; supported: boolean;
  connected: boolean; saved: boolean; password_allowed: boolean};
type Payload = {networks?: Network[]; addresses?: string[];
  hotspot?: {active?: boolean; ssid?: string; robot_id?: string};
  status?: {state?: string; message?: string}};
type Props = {robot: Robot; token?: string; owner: boolean; pending: NetworkHandoff | null;
  onHandoff: (value: NetworkHandoff) => void; onFailure: (message: string) => void};

export function NetworkSettings({robot, token, owner, pending, onHandoff, onFailure}: Props) {
  const [data, setData] = useState<Payload>({});
  const [selected, setSelected] = useState<Network | null>(null);
  const [ssid, setSSID] = useState('');
  const [security, setSecurity] = useState('wpa-psk');
  const [manual, setManual] = useState(false);
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('Wi-Fi bilgileri okunuyor…');
  const latest = useRef({robot, token, pending, onFailure});
  latest.current = {robot, token, pending, onFailure};
  const working = useRef(false);
  const generation = useRef(0);
  const editable = owner && !!token && !busy && !pending;

  async function load(scan = false) {
    if (working.current) return;
    working.current = true;
    const current = generation.current;
    if (scan) { setBusy(true); setMessage('Yakındaki Wi-Fi ağları taranıyor…'); }
    try {
      const props = latest.current;
      const value = await networkRequest<Payload>(props.robot, props.token,
        scan ? 'network/scan' : 'network', scan ? {} : undefined);
      if (generation.current !== current) return;
      setData(value);
      if (value.status?.message) setMessage(value.status.message);
      else setMessage(scan ? 'Ağı listeden seçin veya adını elle girin.' : '');
      if (value.status?.state === 'error' && latest.current.pending) {
        latest.current.onFailure(value.status.message || 'Wi-Fi bağlantısı kurulamadı.');
      }
    } catch {
      if (generation.current === current) setMessage(latest.current.pending
        ? 'Telefonu hedef Wi-Fi ağına bağlayıp uygulamaya dönün. Robot yeniden aranıyor.'
        : 'Robotun Wi-Fi bilgileri okunamadı. Telefonu robotla aynı ağa bağlayın.');
    } finally {
      if (generation.current === current) { working.current = false; setBusy(false); }
    }
  }

  useEffect(() => {
    generation.current++;
    working.current = false;
    setPassword(''); setSelected(null); setData({});
    void load();
    const timer = setInterval(() => { void load(); }, 5000);
    return () => { generation.current++; clearInterval(timer); };
  }, [robot.host, robot.port]);

  async function connect() {
    const target = manual ? ssid : selected?.ssid;
    if (!editable || !target || (!manual && (!selected?.supported || selected.connected))) return;
    const bytes = Array.from(target).reduce((size, char) => {
      const code = char.codePointAt(0)!;
      return size + (code < 0x80 ? 1 : code < 0x800 ? 2 : code < 0x10000 ? 3 : 4);
    }, 0);
    if (manual && (!target.trim() || bytes > 32 || /[\x00-\x1f]/.test(target))) {
      setMessage('Ağ adı 1–32 bayt olmalı.'); return;
    }
    // Keep scans and refreshes from overwriting a newly accepted connection job.
    if (working.current) { setMessage('Ağ işleminin tamamlanmasını bekleyin.'); return; }
    working.current = true; setBusy(true);
    const secret = password;
    setPassword('');
    setMessage('Bağlantı isteği gönderiliyor…');
    const current = generation.current;
    try {
      const value = await networkRequest<{robot_id?: string; handoff: string}>(robot, token,
        'network/connect', manual ? {ssid: target, security, password: security === 'open' ? '' : secret}
          : {network: selected!.id, password: selected!.password_allowed ? secret : ''});
      if (generation.current !== current) return;
      const id = value.robot_id || robot.robotId || data.hotspot?.robot_id;
      if (!id || !value.handoff) throw new Error('Robotun ağ hizmetini güncelleyin; otomatik yeniden bulma kimliği alınamadı.');
      onHandoff({robotId: id, ticket: value.handoff, ssid: target, started: Date.now()});
      setMessage('İstek alındı. Telefonu da hedef Wi-Fi ağına bağlayıp uygulamaya dönün.');
    } catch (error) {
      if (generation.current === current) setMessage(String(error));
    } finally {
      if (generation.current === current) { working.current = false; setBusy(false); }
    }
  }

  const wifiSettings = () => {
    void Linking.sendIntent('android.settings.WIFI_SETTINGS').catch(() =>
      setMessage('Telefonun Wi-Fi ayarlarını açıp hedef ağa bağlanın.'));
  };
  return <View style={styles.panel}>
    <Text style={styles.title}>Robotun Wi-Fi bağlantısı</Text>
    {data.hotspot?.active && <Text style={styles.text}>{data.hotspot.ssid || 'kufi'} · Kurulum ağı açık</Text>}
    {!!data.addresses?.length && <Text selectable style={styles.text}>Robot IP: {data.addresses.join(' · ')}</Text>}
    {!owner && <Text style={styles.help}>Wi-Fi ayarlarını değiştirmek için kumandayı devralın.</Text>}
    <Pressable disabled={!editable} style={styles.button} onPress={() => { void load(true); }}>
      <Text style={styles.text}>Wi-Fi ağlarını tara</Text>
    </Pressable>
    {(data.networks || []).map(network => <Pressable key={network.id} disabled={!editable}
      style={[styles.button, !manual && selected?.id === network.id && styles.selected]}
      onPress={() => { setSelected(network); setManual(false); setPassword(''); }}>
      <Text style={styles.text}>{network.ssid} · {network.security}{network.connected ? ' · Bağlı' : ''}</Text>
    </Pressable>)}
    <Pressable disabled={!editable} style={styles.button}
      onPress={() => { setManual(!manual); setPassword(''); }}>
      <Text style={styles.text}>{manual ? 'Listeden ağ seç' : 'Ağ adını elle gir'}</Text>
    </Pressable>
    {manual && <>
      <TextInput accessibilityLabel="Wi-Fi ağ adı" editable={editable} style={styles.input}
        value={ssid} onChangeText={setSSID} placeholder="Hedef Wi-Fi adı" placeholderTextColor="#799099"
        autoCapitalize="none" autoCorrect={false}/>
      <View style={styles.row}>{[['wpa-psk', 'WPA/WPA2'], ['sae', 'WPA3'], ['open', 'Parolasız']].map(([key, label]) =>
        <Pressable key={key} disabled={!editable} onPress={() => { setSecurity(key); setPassword(''); }}
          style={[styles.button, security === key && styles.selected]}><Text style={styles.text}>{label}</Text></Pressable>)}</View>
    </>}
    {(manual || selected) && <>
      {!manual && <Text style={styles.text}>{selected?.ssid}</Text>}
      {(manual ? security !== 'open' : selected?.password_allowed && !selected.connected) &&
        <TextInput accessibilityLabel="Wi-Fi parolası" editable={editable} style={styles.input}
          value={password} onChangeText={setPassword} placeholder={selected?.saved && !manual ? 'Kayıtlı parolayı kullanmak için boş bırak' : 'Wi-Fi parolası'}
          placeholderTextColor="#799099" secureTextEntry autoCapitalize="none" autoCorrect={false}/>}
      <Pressable disabled={!editable || (!manual && (!selected?.supported || selected.connected))}
        style={styles.button} onPress={() => { void connect(); }}><Text style={styles.text}>Robotu bu ağa bağla</Text></Pressable>
    </>}
    {pending && <Text style={styles.help}>Telefonu “{pending.ssid}” ağına geçirin. Aynı robot yeni IP adresinde aranıyor… Bağlantı kurulamazsa kufi ağına dönüp parolayı kontrol edin.</Text>}
    <Pressable style={styles.button} onPress={wifiSettings}><Text style={styles.text}>Telefonun Wi-Fi ayarlarını aç</Text></Pressable>
    <Text accessibilityLiveRegion="polite" style={styles.help}>{message}</Text>
  </View>;
}

const styles = StyleSheet.create({
  panel: {gap: 10, marginVertical: 20}, title: {color: '#f7f9ff', fontSize: 17, fontWeight: '600'},
  text: {color: '#e9effd', fontSize: 14}, help: {color: '#9eb1d0', fontSize: 13},
  row: {flexDirection: 'row', flexWrap: 'wrap', gap: 8},
  button: {padding: 12, backgroundColor: '#17233d', borderRadius: 6}, selected: {backgroundColor: '#3454a0'},
  input: {color: '#f7f9ff', borderWidth: 1, borderColor: '#4d70d3', borderRadius: 6, padding: 12},
});
