import React, { useEffect, useRef, useState } from 'react';
import { GestureResponderEvent, StyleSheet, Text, View } from 'react-native';

type Props = { label: string; disabled: boolean; onChange: (x: number, y: number) => void };
export function Joystick({ label, disabled, onChange }: Props) {
  const [position, setPosition] = useState({x: 0, y: 0});
  const props = useRef({disabled, onChange});
  props.current = {disabled, onChange};
  const touch = useRef<{id: string; x: number; y: number} | null>(null);
  const reset = () => {
    touch.current = null;
    setPosition({x: 0, y: 0});
    props.current.onChange(0, 0);
  };
  useEffect(() => { if (disabled) reset(); }, [disabled]);
  // Track each finger independently; a single PanResponder would prevent
  // simultaneous use of the drive and head joysticks.
  const start = (event: GestureResponderEvent) => {
    if (props.current.disabled || touch.current) return;
    const point = event.nativeEvent;
    touch.current = {id: point.identifier, x: point.pageX, y: point.pageY};
  };
  const move = (event: GestureResponderEvent) => {
    const origin = touch.current;
    if (!origin || props.current.disabled) return;
    const point = event.nativeEvent.touches.find(t => t.identifier === origin.id);
    if (!point) return;
    const dx = point.pageX - origin.x, dy = point.pageY - origin.y;
    const scale = Math.max(52, Math.hypot(dx, dy));
    const x = dx / scale, y = dy / scale;
    setPosition({x: x * 52, y: y * 52});
    props.current.onChange(x, y);
  };
  const end = (event: GestureResponderEvent) => {
    if (event.nativeEvent.changedTouches.some(t => t.identifier === touch.current?.id)) reset();
  };
  return <View style={[styles.wrapper, disabled && {opacity: 0.35}]}>
    <Text style={styles.label}>{label}</Text>
    <View accessibilityLabel={label} style={styles.base}
      onTouchStart={start} onTouchMove={move} onTouchEnd={end} onTouchCancel={reset}>
      <View style={styles.horizontal}/><View style={styles.vertical}/>
      <View style={[styles.knob, {transform: [{translateX: position.x}, {translateY: position.y}]}]}/>
    </View>
  </View>;
}
const styles = StyleSheet.create({
  wrapper: {alignItems: 'center'}, label: {color: '#d9e8ee', fontSize: 11, letterSpacing: 2, marginBottom: 8},
  base: {width: 144, height: 144, borderRadius: 72, borderWidth: 1, borderColor: '#78939a88', backgroundColor: '#09171caa', alignItems: 'center', justifyContent: 'center'},
  horizontal: {position: 'absolute', width: 114, height: 1, backgroundColor: '#78939a44'},
  vertical: {position: 'absolute', width: 1, height: 114, backgroundColor: '#78939a44'},
  knob: {width: 48, height: 48, borderRadius: 24, backgroundColor: '#b8e75ccc', borderColor: '#e2ffaa', borderWidth: 2},
});
