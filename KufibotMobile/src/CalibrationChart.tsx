import React, {useState} from 'react';
import {Pressable, Text, View, StyleSheet} from 'react-native';
import type {CalibrationData} from './useRobot';

export function CalibrationChart({data, title}: {data?: CalibrationData | null; title: string}) {
  const [selected, setSelected] = useState<number | null>(null);
  const counts = data?.bin_counts;
  const complete = counts?.filter(n => n >= 3).length ?? 0;
  const p = data?.parameters;
  return <View style={styles.panel}>
    <Text style={styles.title}>{title}</Text>
    {!counts || counts.length !== 36 ? <Text style={styles.text}>Kayıtlı açı kapsamı verisi yok. Yeni kalibrasyon yapın.{p ? ` Mevcut kayıt: Ofset X/Y ${p.offset_x.toFixed(2)} / ${p.offset_y.toFixed(2)}; Ölçek X/Y ${p.scale_x.toFixed(4)} / ${p.scale_y.toFixed(4)}. Eski kayıtta tarih ve açı ölçümleri bulunmuyor.` : ''}</Text> : <>
      <Text style={styles.text}>{complete}/36 dilim · {data?.samples} ölçüm / en az {data?.target}</Text>
      <View style={styles.ring}>
        {counts.map((count, index) => {
          const angle = (index * 10 + 5) * Math.PI / 180;
          return <Pressable key={index} accessibilityRole="button"
            accessibilityLabel={`${index * 10}–${(index + 1) * 10} derece: ${count} ölçüm, en az 3`}
            onPress={() => setSelected(index)} style={[styles.segment, {
              left: 140 + Math.sin(angle) * 109 - 9, top: 140 - Math.cos(angle) * 109 - 20,
              transform: [{rotate: `${index * 10 + 5}deg`}],
            }]}>
            {[2, 1, 0].map(slot => <View key={slot} style={[styles.dot,
              {backgroundColor: count > slot ? (count >= 3 ? '#b8e75c' : '#ffc27a') : '#405064'}]}/>)}
          </Pressable>;
        })}
        <View pointerEvents="none" style={styles.center}><Text style={styles.title}>{complete}/36</Text>
          <Text style={styles.text}>{typeof data?.angle_deg === 'number' ? `${data.angle_deg.toFixed(1)}°` : 'Açı bekleniyor'}</Text></View>
        {typeof data?.angle_deg === 'number' && <View pointerEvents="none" style={[styles.pointer, {transform: [{rotate: `${data.angle_deg}deg`}]}]}><Text style={styles.arrow}>▲</Text></View>}
      </View>
      <Text style={styles.text}>{selected === null ? 'Bir dilime dokunarak ölçüm sayısını görün.' : `${selected * 10}–${(selected + 1) * 10}°: ${counts[selected]} ölçüm (en az 3)`}</Text>
      {!!data?.completed_at && <Text style={styles.text}>Kaydedildi: {new Date(data.completed_at).toLocaleString('tr-TR')}</Text>}
      {p && <Text style={styles.text}>Ofset X/Y: {p.offset_x.toFixed(2)} / {p.offset_y.toFixed(2)}{ '\n' }Ölçek X/Y: {p.scale_x.toFixed(4)} / {p.scale_y.toFixed(4)}</Text>}
      {data?.completed_at && data.minimum && data.maximum && <Text style={styles.text}>X aralığı: {data.minimum.x}…{data.maximum.x}{ '\n' }Y aralığı: {data.minimum.y}…{data.maximum.y}</Text>}
    </>}
  </View>;
}
const styles = StyleSheet.create({
  panel: {borderWidth: 1, borderColor: '#405064', borderRadius: 12, padding: 12, marginVertical: 10},
  title: {color: '#eef5f7', fontSize: 16, fontWeight: '600', marginBottom: 6},
  text: {color: '#c5d5dc', fontSize: 12, lineHeight: 19},
  ring: {width: 280, height: 280, alignSelf: 'center'},
  segment: {position: 'absolute', width: 18, height: 40, alignItems: 'center', justifyContent: 'space-around'},
  dot: {width: 10, height: 10, borderRadius: 5},
  center: {position: 'absolute', top: 120, left: 85, width: 110, alignItems: 'center'},
  pointer: {position: 'absolute', top: 60, left: 130, height: 160, width: 20},
  arrow: {color: '#79d6ff', fontSize: 20, textAlign: 'center'},
});
