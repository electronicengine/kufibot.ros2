import React from 'react';
import {StyleSheet, Text, View} from 'react-native';
import {AiConfig} from './useRobot';

export function HeardText({config}: {config?: AiConfig}) {
  const heard = config?.activation_status?.last_heard;
  return <View style={styles.box} accessibilityLiveRegion="polite">
    <Text style={styles.label}>Son algılanan metin</Text>
    <Text selectable style={styles.text}>{heard?.text || 'Henüz konuşma algılanmadı.'}</Text>
    {!!heard && <Text style={styles.label}>{new Date(heard.timestamp_ms).toLocaleTimeString('tr-TR')} · {heard.matched ? 'Uyanma kelimesi eşleşti' : 'Uyanma kelimesi eşleşmedi'}</Text>}
  </View>;
}
const styles = StyleSheet.create({
  box: {backgroundColor: '#111319ee', borderRadius: 10, padding: 8, marginVertical: 6, maxWidth: 320},
  label: {color: '#abb9cf', fontSize: 11},
  text: {color: '#eef2ff', fontSize: 13},
});
