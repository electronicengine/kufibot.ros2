import {HeardText} from './HeardText';
import React, {useEffect, useState} from 'react';
import {Pressable, StyleSheet, Switch, Text, TextInput, View} from 'react-native';
import {AiConfig, AiSettings, ActivationSettings, activationDefaults} from './useRobot';

export function AiSettingsPanel({config, owner, send, status, error}: {
  config?: AiConfig; owner: boolean; send: (data: {type: string; [key: string]: unknown}) => unknown;
  status?: {state: string; detail: string}; error?: string;
}) {
  const [draft, setDraft] = useState<AiSettings>({provider: 'verasist', language: 'tr', stt: '', llm: '', embedding: '', tts: '', system_prompt: '', camera_attach_to_every_user_turn: false});
  const [dirty, setDirty] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const saved = JSON.stringify(config?.settings);
  useEffect(() => {
    if (config && (!dirty || pending === saved)) {
      setDraft(config.settings); setDirty(false); setPending(null);
    }
  }, [saved, dirty, pending]);
  const models = config?.models ?? [];
  const activation = {...activationDefaults, ...draft.activation};
  activation.language = ({eng:'en',tur:'tr'} as Record<string,string>)[activation.language] || activation.language;
  const activationLanguages = [...new Set(['tr','en',...models.filter(m => m.available && ['stt','tts'].includes(m.kind)).flatMap(m => m.languages)])].sort();
  function updateActivation(key: keyof ActivationSettings, value: string | boolean) {
    setDraft(previous => ({...previous, activation:{...activationDefaults, ...previous.activation, [key]:value,
      ...(key === 'language' ? {stt:'',tts:''} : {})}}));
    setDirty(true); setPending(null);
  }
  const missingActivation = (['stt','tts'] as const).filter(kind =>
    (kind === 'stt' ? activation.enabled : activation.greeting_text || activation.farewell_text) &&
    !models.some(m => m.kind === kind && m.available && m.languages.includes(activation.language) && (!activation[kind] || activation[kind] === m.id)));

  const languages = [...new Set(models.filter(m => m.available).flatMap(m => m.languages))].sort();
  const local = draft.provider === 'local';
  const valid = !local || (['stt', 'llm', 'embedding', 'tts'] as const).every(kind => models.some(m =>
    m.kind === kind && m.id === draft[kind] && m.available &&
    (kind === 'llm' || kind === 'embedding' || m.languages.includes(draft.language))));
  function choose(key: keyof AiSettings, value: string) {
    setDraft(previous => ({...previous, [key]: value, ...(key === 'language' ? {stt: '', tts: ''} : {})}));
    setDirty(true); setPending(null);
  }
  function choices(key: keyof AiSettings, items: {id: string; label?: string}[]) {
    return <View style={styles.options}>{items.map(item => <Pressable key={item.id}
      accessibilityRole="radio" accessibilityState={{selected: draft[key] === item.id, disabled: !owner}}
      disabled={!owner || !config} onPress={() => choose(key, item.id)}
      style={[styles.option, draft[key] === item.id && styles.selected]}>
      <Text style={styles.text}>{item.label || item.id}</Text>
    </Pressable>)}</View>;
  }
  return <View>
    <Text style={styles.title}>Sesli Ajan Ayarları</Text>
    <HeardText config={config}/>
    {choices('provider', [{id: 'verasist', label: 'Verasist AI'}, {id: 'local', label: 'Local AI'}])}
    {local && <>
      <Text style={styles.text}>Workflow</Text>
      {choices('workflow_id', [{id:'',label:'Düz sohbet'}, ...(config?.workflows || [])])}
      <Text style={styles.text}>Dil</Text>
      {choices('language', languages.map(id => ({id, label: ({tr: 'Türkçe', en: 'English'} as Record<string, string>)[id] || id})))}
      {(['stt', 'llm', 'embedding', 'tts'] as const).map(kind => <View key={kind}>
        <Text style={styles.text}>{({stt: 'STT', llm: 'LLM · llama.cpp', embedding: 'Embedding modeli · llama.cpp', tts: 'TTS · Piper'})[kind]}</Text>
        {choices(kind, models.filter(m => m.kind === kind && m.available && (kind === 'llm' || kind === 'embedding' || m.languages.includes(draft.language))))}
      </View>)}
      <Text style={styles.text}>Sistem mesajı</Text>
      <TextInput multiline maxLength={6000} editable={owner && !!config}
        value={draft.system_prompt} onChangeText={value => { setDraft(previous => ({...previous, system_prompt: value})); setDirty(true); setPending(null); }}
        placeholder="Yerel ajanın rolünü, yanıt tarzını ve kurallarını yazın."
        placeholderTextColor="#799099" accessibilityLabel="Yerel ajan sistem mesajı"
        style={styles.prompt}/>
      {!valid && <Text style={styles.notice}>Bu dil için robotta STT, LLM ve TTS modellerini kurup seçin.</Text>}
    </>}
    <Text style={styles.title}>Uyanma ve karşılama</Text>
    <Text style={styles.text}>Uyanma kelimesini dinle (kumandada da açık)</Text>
    <Switch accessibilityLabel="Uyanma kelimesini dinle" disabled={!owner || !config} value={activation.enabled} onValueChange={value => updateActivation('enabled', value)}/>
    {([['phrase','Uyanma kelimesi veya ifadesi'],['greeting_text','Karşılama metni'],['farewell_text','Kapanış metni']] as const).map(([key,label]) => <View key={key}>
      <Text style={styles.text}>{label}</Text>
      <TextInput accessibilityLabel={label} editable={owner && !!config} style={[styles.option, styles.text]} value={activation[key]}
        maxLength={key === 'phrase' ? 120 : 500} onChangeText={value => updateActivation(key,value)}/>
    </View>)}
    <Text style={styles.notice}>Modeller workflow editörüyle aynı robot klasörlerinden taranır. Önce dili, ardından STT ve TTS modelini seçin.</Text>
    <Text style={styles.text}>Dinleyici dili</Text>
    <View style={styles.options}>{activationLanguages.map(language => <Pressable key={language}
      accessibilityRole="radio" accessibilityState={{selected:activation.language === language}} disabled={!owner || !config}
      style={[styles.option,activation.language === language && styles.selected]} onPress={() => updateActivation('language',language)}>
      <Text style={styles.text}>{({tr:'Türkçe',en:'English'} as Record<string,string>)[language] || language}</Text>
    </Pressable>)}</View>
    {(['stt','tts','greeting_mimic','farewell_mimic'] as const).map(key => {
      const mimic = key.endsWith('mimic');
      const items = mimic ? (config?.mimics || []).map(m => ({id:m.id,label:m.name})) : models.filter(m => m.kind === key && m.available && m.languages.includes(activation.language)).map(m => ({id:m.id,label:m.label || m.id}));
      if (activation[key] && !items.some(m => m.id === activation[key])) items.push({id:activation[key],label:activation[key]+' · kullanılamıyor'});
      return <View key={key}><Text style={styles.text}>{{stt:'Yerel dinleyici STT',tts:'Sabit söyleyiş TTS',greeting_mimic:'Karşılama mimiği',farewell_mimic:'Kapanış mimiği'}[key]}</Text>
        <View style={styles.options}>{[{id:'',label:mimic ? 'Mimik yok' : 'Otomatik (kurulu model)'},...items].map(item =>
          <Pressable key={item.id} accessibilityRole="radio" accessibilityState={{selected:activation[key] === item.id}} disabled={!owner || !config}
            style={[styles.option,activation[key] === item.id && styles.selected]} onPress={() => updateActivation(key,item.id)}><Text style={styles.text}>{item.label}</Text></Pressable>)}</View>
      </View>;
    })}
    <Text style={styles.notice}>Boş metin seslendirmeyi kapatır. Bu ayarlar tüm sağlayıcı ve workflow’larda geçerlidir.</Text>
    {!!missingActivation.length && <Text style={styles.notice}>Yerel {missingActivation.join(' / ').toUpperCase()} modeli kurup seçin.</Text>}
    {!!config?.activation_status?.error && <Text style={styles.notice}>{config.activation_status.error}</Text>}
    <Text style={styles.text}>Konuşmalarıma kamera görüntüsü ekle</Text>
    <Switch accessibilityLabel="Konuşmalarıma kamera görüntüsü ekle"
      disabled={!owner || !config || local} value={!!draft.camera_attach_to_every_user_turn}
      onValueChange={value => {
        setDraft(previous => ({...previous, camera_attach_to_every_user_turn: value}));
        setDirty(true); setPending(null);
      }}/>
    <Text style={styles.notice}>{local ? 'Yerel sağlayıcı görüntü desteklemiyor.' :
      'Açıkken her konuşmanıza robot kamerasından bir fotoğraf eklenir. Kapalıyken açık kamera talepleri ve navigasyon çalışmaya devam eder.'}</Text>
    <Pressable disabled={!owner || !config || !valid} style={[styles.option, (!owner || !config || !valid) && {opacity: 0.4}]}
      onPress={() => {send({type: 'setAiSettings', settings: draft}); setPending(JSON.stringify(draft));}}>
      <Text style={styles.text}>Ayarları kaydet ve uygula</Text>
    </Pressable>
    <Text style={styles.notice}>Ayarları kaydettikten sonra ana ekrandan YZ modu seçildiğinde seçili ajan başlar.</Text>
    <Text accessibilityLiveRegion="polite" style={styles.notice}>{[!config ? 'Sesli ajan ayarları bekleniyor' : '',
      pending ? 'Uygulanması bekleniyor…' : '', config?.error, error, ({preparing:'Hazırlanıyor',wake_listening:'Uyanma kelimesi bekleniyor',greeting:'Karşılıyor',connecting:'Bağlanıyor',connected:'Görüşmede',listening:'Görüşmede',farewell:'Kapanış',disabled:'Dinleme kapalı',error:'Hata',idle:'Oturum kapalı',suspended:'Dinleme askıda'} as Record<string,string>)[status?.state || ''] || status?.state, status?.detail].filter(Boolean).join(' · ')}</Text>
  </View>;
}
const styles = StyleSheet.create({
  title: {color: '#7d9ef0', fontSize: 18, marginTop: 18, marginBottom: 10},
  options: {flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginVertical: 8},
  option: {padding: 12, backgroundColor: '#17233d', borderRadius: 8, borderWidth: 1, borderColor: '#4d70d3', marginVertical: 4},
  selected: {borderColor: '#7d9ef0', backgroundColor: '#2b50b8'},
  prompt: {minHeight: 130, color: '#f7f9ff', backgroundColor: '#17233d', borderColor: '#4d70d3', borderWidth: 1, borderRadius: 8, padding: 10, textAlignVertical: 'top', marginVertical: 8},
  text: {color: '#f7f9ff'}, notice: {color: '#bdd0f9', marginVertical: 8},
});
