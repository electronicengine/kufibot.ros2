import React, {useEffect, useState} from 'react';
import {Pressable, StyleSheet, Text, TextInput, View} from 'react-native';
import {AiConfig, AiSettings} from './useRobot';

export function AiSettingsPanel({config, owner, send, status, error}: {
  config?: AiConfig; owner: boolean; send: (data: {type: string; [key: string]: unknown}) => unknown;
  status?: {state: string; detail: string}; error?: string;
}) {
  const [draft, setDraft] = useState<AiSettings>({provider: 'verasist', language: 'tr', stt: '', llm: '', tts: '', system_prompt: ''});
  const [dirty, setDirty] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const saved = JSON.stringify(config?.settings);
  useEffect(() => {
    if (config && (!dirty || pending === saved)) {
      setDraft(config.settings); setDirty(false); setPending(null);
    }
  }, [saved, dirty, pending]);
  const models = config?.models ?? [];
  const languages = [...new Set(models.filter(m => m.available).flatMap(m => m.languages))].sort();
  const local = draft.provider === 'local';
  const valid = !local || (['stt', 'llm', 'tts'] as const).every(kind => models.some(m =>
    m.kind === kind && m.id === draft[kind] && m.available && m.languages.includes(draft.language)));
  function choose(key: keyof AiSettings, value: string) {
    setDraft(previous => ({...previous, [key]: value, ...(key === 'language' ? {stt: '', llm: '', tts: ''} : {})}));
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
    {choices('provider', [{id: 'verasist', label: 'Verasist AI'}, {id: 'local', label: 'Local AI'}])}
    {local && <>
      <Text style={styles.text}>Dil</Text>
      {choices('language', languages.map(id => ({id})))}
      {(['stt', 'llm', 'tts'] as const).map(kind => <View key={kind}>
        <Text style={styles.text}>{({stt: 'STT · Vosk', llm: 'LLM · llama.cpp', tts: 'TTS · Piper'})[kind]}</Text>
        {choices(kind, models.filter(m => m.kind === kind && m.available && m.languages.includes(draft.language)))}
      </View>)}
      <Text style={styles.text}>Sistem mesajı</Text>
      <TextInput multiline maxLength={6000} editable={owner && !!config}
        value={draft.system_prompt} onChangeText={value => { setDraft(previous => ({...previous, system_prompt: value})); setDirty(true); setPending(null); }}
        placeholder="Yerel ajanın rolünü, yanıt tarzını ve kurallarını yazın."
        placeholderTextColor="#799099" accessibilityLabel="Yerel ajan sistem mesajı"
        style={styles.prompt}/>
      {!valid && <Text style={styles.notice}>Bu dil için robotta STT, LLM ve TTS modellerini kurup seçin.</Text>}
    </>}
    <Pressable disabled={!owner || !config || !valid} style={[styles.option, (!owner || !config || !valid) && {opacity: 0.4}]}
      onPress={() => {send({type: 'setAiSettings', settings: draft}); setPending(JSON.stringify(draft));}}>
      <Text style={styles.text}>Ayarları kaydet ve uygula</Text>
    </Pressable>
    <Text style={styles.notice}>Ayarları kaydettikten sonra ana ekrandan YZ modu seçildiğinde seçili ajan başlar.</Text>
    <Text accessibilityLiveRegion="polite" style={styles.notice}>{[!config ? 'Sesli ajan ayarları bekleniyor' : '',
      pending ? 'Uygulanması bekleniyor…' : '', config?.error, error, status?.state, status?.detail].filter(Boolean).join(' · ')}</Text>
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
