import React, {useEffect, useRef, useState} from 'react';
import {Pressable, StyleSheet, Switch, Text, TextInput, View} from 'react-native';
import {AiConfig, VerasistAudioSettings} from './useRobot';

const defaults: Required<VerasistAudioSettings> = {
  aec_mode: 'system', mute_mic_during_playback: false, noise_gate_rms: 0,
  noise_gate_hangover_sec: .5, noise_suppression_db: -25, barge_in_rms: 0,
  barge_in_start_sec: .18, playback_echo_tail_sec: 1.2, aec_play_delay_ms: 180,
  aec_high_pass_filter: true, aec_noise_suppression: true, aec_gain_control: false,
  aec_extended_filter: true, aec_delay_agnostic: true,
};

export function VerasistAudioSettingsPanel({config, owner, send, origin, token}: {
  origin?: string; token?: string; config?: AiConfig; owner: boolean; send: (data: {type: string; [key: string]: unknown}) => unknown;
}) {
  const [draft, setDraft] = useState<Required<VerasistAudioSettings>>(defaults);
  const [workflowUuid, setWorkflowUuid] = useState('');
  const [publishing, setPublishing] = useState(false);
  const [publishStatus, setPublishStatus] = useState('');
  const publishRequest = useRef<AbortController | null>(null);
  useEffect(() => () => { publishRequest.current?.abort(); }, [origin]);
  const validWorkflow = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(workflowUuid.trim());
  const canPublish = owner && !!origin && !!token && validWorkflow && !publishing;
  const publishTools = async () => {
    if (!canPublish || publishRequest.current) return;
    const controller = new AbortController();
    publishRequest.current = controller;
    const timer = setTimeout(() => controller.abort(), 40000);
    setPublishing(true); setPublishStatus('');
    try {
      const response = await fetch(`${origin}/api/verasist/toolset`, {
        method: 'POST', headers: {'Content-Type': 'application/json', Authorization: `Bearer ${token}`},
        body: JSON.stringify({workflow_uuid: workflowUuid.trim()}), signal: controller.signal,
      });
      const text = await response.text();
      let result; try { result = JSON.parse(text); } catch { throw new Error('Yayın yapılamadı. Robot bağlantısını ve kumanda sahipliğini kontrol edin.'); }
      if (!response.ok) throw new Error(result.error || 'Araç yayını başarısız');
      setPublishStatus(`${result.count} araç ${result.workflow_uuid} workflow’una yayımlandı. Araçları görmek için workflow sayfasını yenileyin.`);
    } catch (error) {
      setPublishStatus(controller.signal.aborted ? 'Yayın sonucu alınamadı. Yeniden denemeden önce workflow kataloğunu kontrol edin.' : String(error));
    } finally { clearTimeout(timer); publishRequest.current = null; setPublishing(false); }
  };
  const [saved, setSaved] = useState(false);
  const [openHelp, setOpenHelp] = useState<string | null>(null);
  const available = config?.settings.provider === 'verasist';
  useEffect(() => { if (available) { setDraft({...defaults, ...config?.settings.verasist_audio}); setSaved(false); } }, [available, JSON.stringify(config?.settings.verasist_audio)]);
  const update = <K extends keyof VerasistAudioSettings>(key: K, value: Required<VerasistAudioSettings>[K]) => {
    setDraft(previous => ({...previous, [key]: value})); setSaved(false);
  };
  const help = (id: string, text: string) => <><Pressable accessibilityRole="button" accessibilityLabel={`${id} hakkında bilgi`}
    onPress={() => setOpenHelp(current => current === id ? null : id)} style={styles.helpButton}><Text style={styles.helpMark}>?</Text></Pressable>
    {openHelp === id && <Text style={styles.helpText}>{text}</Text>}</>;
  const number = (key: Exclude<keyof VerasistAudioSettings, 'aec_mode' | 'mute_mic_during_playback'>, label: string, minimum: number, maximum: number, step: string, explanation: string) => <View key={key}>
    <View style={styles.labelRow}><Text style={styles.label}>{label}</Text>{help(key, explanation)}</View><TextInput accessibilityLabel={label} editable={owner && available}
      keyboardType="decimal-pad" style={styles.input} value={String(draft[key])}
      onChangeText={value => update(key, Number(value))}/><Text style={styles.hint}>{minimum} - {maximum} · adım {step}</Text>
  </View>;
  const toggle = (key: 'aec_high_pass_filter' | 'aec_noise_suppression' | 'aec_gain_control' | 'aec_extended_filter' | 'aec_delay_agnostic', label: string, explanation: string) => <View key={key} style={styles.switchRow}><View style={styles.labelRow}><Text style={styles.text}>{label}</Text>{help(key, explanation)}</View><Switch value={draft[key]} disabled={!owner} onValueChange={value => update(key, value)}/></View>;
  if (!available) return <Text style={styles.notice}>Bu sayfa Verasist sağlayıcısı seçiliyken kullanılabilir.</Text>;
  return <View><Text style={styles.title}>Verasist Ses Ayarları</Text>
    <Text style={styles.label}>SDK araçları · Workflow UUID</Text>
    <TextInput accessibilityLabel="Workflow UUID" value={workflowUuid} editable={owner && !publishing}
      autoCapitalize="none" autoCorrect={false} placeholder="Workflow UUID" placeholderTextColor="#799099" style={styles.input}
      onChangeText={value => {setWorkflowUuid(value); setPublishStatus('');}}/>
    <Text style={styles.notice}>Workflow UUID girin (görüşme trigger UUID’si değil). Yayın, bu workflow’un SDK araç kataloğunu robotun güncel araçlarıyla değiştirir. Görüşme açılmaz.</Text>
    <Pressable accessibilityRole="button" disabled={!canPublish} style={[styles.save, !canPublish && styles.disabled]} onPress={publishTools}>
      <Text style={styles.text}>{publishing ? 'Yayımlanıyor…' : 'Mevcut araçları yayımla'}</Text>
    </Pressable>
    <Text accessibilityLiveRegion="polite" style={styles.notice}>{publishStatus}</Text>
    <Text style={styles.notice}>Ayarlar kaydedilir; sonraki Verasist görüşmesinde uygulanır.</Text>
    <View style={styles.labelRow}><Text style={styles.label}>Yankı engelleme · AEC</Text>{help('aec', 'Sistem yapılandırması PipeWire aygıtını kullanır. Zorunlu AEC, sistemdeki Kufibot yankı engelleme aygıtını ister. AEC kapalı fiziksel mikrofonu kullanır; hoparlör sesi yeniden algılanabilir.')}</View>
    <View style={styles.options}>{([['system', 'Sistem yapılandırması'], ['enabled', 'Kufibot AEC zorunlu'], ['disabled', 'AEC kapalı · fiziksel aygıtlar']] as const).map(([id, label]) => <Pressable key={id}
      disabled={!owner} onPress={() => update('aec_mode', id)} style={[styles.option, draft.aec_mode === id && styles.selected]}><Text style={styles.text}>{label}</Text></Pressable>)}</View>
    <Text style={styles.label}>PipeWire WebRTC AEC modülü</Text>
    {number('aec_play_delay_ms', 'Referans gecikmesi (ms)', 0, 500, '1', 'Hoparlör referansını mikrofonun duyduğu yankıyla hizalar. Bluetooth için başlangıç 180 ms. Düşük değer yankıyı yeterince iptal etmez; yüksek değer yanlış hizalama yapar. Yeni görüşmeden önce uygulanır.')}
    {toggle('aec_high_pass_filter', 'Yüksek geçiren filtre', 'Düşük frekanslı motor uğultusunu azaltır. Açık önerilir; kapatmak daha dolgun bas bırakır fakat uğultu ve yankıyı artırabilir.')}
    {toggle('aec_noise_suppression', 'WebRTC gürültü bastırma', 'AEC içindeki ek sabit gürültü azaltmadır. Açık ortam gürültüsünü düşürür; çok hafif konuşmada yapaylık oluşturursa kapatılabilir.')}
    {toggle('aec_gain_control', 'Otomatik kazanç kontrolü', 'Mikrofon düzeyini yükseltir. Sessiz kullanıcıyı güçlendirebilir ama motor ve yankıyı da büyütebilir; varsayılan kapalıdır.')}
    {toggle('aec_extended_filter', 'Genişletilmiş yankı filtresi', 'Uzun veya karmaşık yankı yollarında daha iyi uyum sağlar. Açık önerilir; kaynak sınırlıysa kapatmak CPU yükünü azaltabilir.')}
    {toggle('aec_delay_agnostic', 'Değişken gecikme modu', 'Değişken Bluetooth gecikmesini izlemeye yardım eder. Açık önerilir; sabit kablolu ses yolunda kapatmak daha kararlı olabilir.')}
    <Text style={styles.label}>Mikrofon akışı</Text>
    <View style={styles.switchRow}><View style={styles.labelRow}><Text style={styles.text}>Robot konuşurken mikrofonu sessize al</Text>{help('mute', 'Açıkken robot konuşurken mikrofon sıfır PCM gönderir; yankıyı önler fakat bu sürede kullanıcı sesi kayda gitmez. Kapalıyken kullanıcı sesi sürekli iletilir; çalışan AEC gerekir.')}</View><Switch value={draft.mute_mic_during_playback} disabled={!owner} onValueChange={value => update('mute_mic_during_playback', value)}/></View>
    {number('noise_suppression_db', 'Gürültü bastırma (dB)', -60, 0, '1', 'Daha negatif değer daha güçlü sabit gürültü azaltır; aşırı düşük değer konuşmayı metalik veya zayıf yapabilir. 0 kapatır.')}
    {number('noise_gate_rms', 'Sessizlik eşiği (RMS, 0 kapalı)', 0, 32768, '10', 'Bu değerin altındaki mikrofon kareleri sessizliğe çevrilir. Yüksek değer arka planı keser, ancak sessiz konuşma hecelerini de kesebilir. 0 kapatır.')}
    {number('noise_gate_hangover_sec', 'Sessizlik bırakma süresi (sn)', 0, 5, '.1', 'Ses eşik altına indikten sonra kapının açık kaldığı süredir. Yüksek değer kelime aralarını korur ama daha fazla ortam sesi geçirir; düşük değer konuşmayı daha erken keser.')}
    <Text style={styles.label}>Söz kesme koruması</Text>
    {number('barge_in_rms', 'Söz kesme eşiği (RMS, 0 kapalı)', 0, 32768, '10', 'Yalnız robot konuşurken uygulanır. Yüksek değer hoparlör yankısını reddeder fakat normal sesli kullanıcıyı da engelleyebilir. 0 korumayı kapatır.')}
    {number('barge_in_start_sec', 'Gerekli ses süresi (sn)', .01, .5, '.01', 'Söz kesme kabul edilmeden önce gereken sürekli ses süresidir. Yüksek değer yankı ve tıkırtıları azaltır, ancak kullanıcının ilk hecesini geciktirir.')}
    {number('playback_echo_tail_sec', 'Oynatma yankı kuyruğu (sn)', 0, 5, '.1', 'Robot sesi bittiğinde korumanın sürdüğü süredir. Yüksek değer Bluetooth gecikmeli yankıyı bastırır, ancak kullanıcının hemen başlayan konuşmasını da geciktirebilir.')}
    <Text style={styles.notice}>Verasist sunucu VAD değeri SDK tarafından istemciye sunulmuyor. Buradaki ayarlar, PCM sesini sunucuya gitmeden önce etkiler.</Text>
    <Pressable disabled={!owner || !config} style={[styles.save, (!owner || !config) && styles.disabled]} onPress={() => {
      send({type: 'setAiSettings', settings: {...config?.settings, verasist_audio: draft}}); setSaved(true);
    }}><Text style={styles.text}>Ses ayarlarını kaydet</Text></Pressable>
    <Text style={styles.notice}>{saved ? 'Kaydedildi; sonraki Verasist görüşmesinde uygulanacak.' : owner ? 'Yapılan değişiklikleri kaydedin.' : 'Kumandayı devralarak değiştirin.'}</Text>
  </View>;
}

const styles = StyleSheet.create({
  title: {color: '#7d9ef0', fontSize: 18, marginBottom: 10}, label: {color: '#f7f9ff', marginTop: 16, marginBottom: 6}, labelRow: {flexDirection: 'row', alignItems: 'center', gap: 6},
  text: {color: '#f7f9ff'}, notice: {color: '#bdd0f9', marginVertical: 8, lineHeight: 18},
  options: {gap: 8}, option: {padding: 12, backgroundColor: '#17233d', borderRadius: 8, borderWidth: 1, borderColor: '#4d70d3'},
  selected: {borderColor: '#7d9ef0', backgroundColor: '#2b50b8'}, switchRow: {flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingVertical: 8},
  input: {color: '#f7f9ff', borderColor: '#4d70d3', borderWidth: 1, borderRadius: 8, padding: 12}, hint: {color: '#799099', fontSize: 11, marginTop: 4}, helpButton: {width: 22, height: 22, borderRadius: 11, borderWidth: 1, borderColor: '#7d9ef0', alignItems: 'center', justifyContent: 'center'}, helpMark: {color: '#bdd0f9', fontWeight: '700'}, helpText: {color: '#bdd0f9', lineHeight: 18, marginTop: 4},
  save: {backgroundColor: '#2b50b8', padding: 14, borderRadius: 8, marginTop: 16, alignItems: 'center'}, disabled: {opacity: .4},
});