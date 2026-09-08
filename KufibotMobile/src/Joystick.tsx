import React, { useEffect, useRef, useState } from 'react';
import { GestureResponderEvent, StyleSheet, Text, View } from 'react-native';

type Props = { label: string; disabled: boolean; fourWay?: boolean; onChange: (x: number, y: number) => void };
export function Joystick({ label, disabled, fourWay = false, onChange }: Props) {
  const [position, setPosition] = useState({x: 0, y: 0});
  const props = useRef({disabled, fourWay, onChange});
  props.current = {disabled, fourWay, onChange};
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
    let x = dx / scale, y = dy / scale;
    // Movement is intentionally digital: each of the four sectors commands
    // the matching direction at maximum motor power.
    if (props.current.fourWay && (x || y)) [x, y] = Math.abs(x) >= Math.abs(y)
      ? [Math.sign(x), 0] : [0, Math.sign(y)];
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
      {fourWay && <><Text style={[styles.direction, styles.up]}>İLERİ</Text><Text style={[styles.direction, styles.down]}>GERİ</Text>
        <Text style={[styles.direction, styles.left]}>SOL</Text><Text style={[styles.direction, styles.right]}>SAĞ</Text></>}
      <View style={[styles.knob, {transform: [{translateX: position.x}, {translateY: position.y}]}]}/>
    </View>
  </View>;
}
const styles = StyleSheet.create({
  wrapper: {alignItems: 'center'}, label: {color: '#e9effd', fontSize: 11, letterSpacing: 2, marginBottom: 8},
  base: {width: 144, height: 144, borderRadius: 72, borderWidth: 1, borderColor: '#4d70d388', backgroundColor: '#111319dd', alignItems: 'center', justifyContent: 'center'},
  horizontal: {position: 'absolute', width: 114, height: 1, backgroundColor: '#4d70d344'},
  vertical: {position: 'absolute', width: 1, height: 114, backgroundColor: '#4d70d344'},
  direction: {position: 'absolute', color: '#aebfdb', fontSize: 8, letterSpacing: 1},
  up: {top: 12}, down: {bottom: 12}, left: {left: 9}, right: {right: 9},
  knob: {width: 48, height: 48, borderRadius: 24, backgroundColor: '#4b6fd4cc', borderColor: '#bdd0f9', borderWidth: 2},
});
