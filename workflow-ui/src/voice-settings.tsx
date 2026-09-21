import React from "react";

type Field = { label: string; default: number; min: number; max: number; step: number; help: string };
export function VoiceSettings({ value, fields, disabled, change }: {
  value: Record<string, any>; fields?: Record<string, Field>; disabled: boolean; change: (value: Record<string, any>) => void;
}) {
  function number(key: string) {
    const field = fields?.[key];
    if (!field) return null;
    const current = value[key];
    const invalid = current !== undefined && (!Number.isFinite(current) || current < field.min || current > field.max || field.step === 1 && !Number.isInteger(current));
    return <label key={key}>{field.label}<input type="number" aria-label={field.label} min={field.min} max={field.max} step={field.step}
      placeholder={String(field.default)} value={current ?? ""} aria-invalid={invalid}
      onChange={event => { const next = {...value}; if (event.target.value === "") delete next[key]; else next[key] = Number(event.target.value); change(next); }}/>
      <small>{field.help} {current === undefined && `Robot varsayılanı kullanılıyor (standart: ${field.default}).`}</small>
      {invalid && <small className="voice-setting-error" role="alert">{field.min}–{field.max} aralığında {field.step === 1 ? "bir tam sayı" : "bir değer"} girin.</small>}
    </label>;
  }
  return <div className="voice-settings">
    <p className="voice-settings-intro">Bu workflow’un yerel sesli görüşme ayarları. Kaydet taslağı saklar; Etkinleştir robota uygular.</p>
    <fieldset disabled={disabled}><legend>Görüşme sınırları</legend>{number('max_session_sec')}{number('max_utterance_sec')}</fieldset>
    <fieldset disabled={disabled}><legend>Konuşma algılama · VAD</legend>{['vad_pre_roll_ms', 'vad_threshold', 'vad_silence_ms', 'vad_min_speech_ms'].map(number)}</fieldset>
    <fieldset disabled={disabled}><legend>Yankı engelleme · AEC</legend>
      <label>Yankı engelleme<select aria-label="Yankı engelleme" value={value.aec_mode || 'system'} onChange={event => change({...value, aec_mode: event.target.value})}>
        <option value="system">Robot varsayılanını kullan</option><option value="enabled">Açık · AEC mikrofon ve hoparlör</option><option value="disabled">Kapalı · Doğrudan ses aygıtları</option>
      </select></label>
      <p>Açık seçimi, robotta kurulu AEC ses aygıtlarını kullanır. Aygıtlar hazır değilse görüşme bir hata mesajıyla başlatılmaz.</p>
      <p>Filtreler ve Bluetooth referans gecikmesi robotun ortak ses yapılandırmasında yönetilir; workflow başına değiştirilmez. Yerel ajan konuşurken mikrofonu dinlemez.</p>
    </fieldset>
    <button disabled={disabled || !Object.keys(value).length} onClick={() => change({})}>Robot varsayılanlarına dön</button>
  </div>;
}
