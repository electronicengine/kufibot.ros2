import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import type { DistanceMapData, RoutePlan } from './useRobot';

export function DistanceMap({map, routePlan, large = false, distance}: {map?: DistanceMapData | null; routePlan?: RoutePlan | null; large?: boolean; distance?: number | null}) {
  const size = large ? 320 : 150;
  const points = map?.obstacle_points ?? [];
  const robot = map?.robot_pose ?? [0, 0];
  const route = routePlan?.map_id === map?.map_id ? routePlan : null;
  const routePoints = route ? [route.start_pose, ...route.waypoints.map(p => [p.x_m, p.y_m])] : [];
  const extent = Math.max(2, ...[...points, ...routePoints].flatMap((p: number[]) => [Math.abs(p[0]), Math.abs(p[1])]),
    Math.abs(robot[0]), Math.abs(robot[1])) * 1.15;
  const point = (p: number[]) => ({left: size / 2 + p[0] / extent * size / 2,
    top: size / 2 - p[1] / extent * size / 2});
  const r = point(robot);
  const nearest = points.length ? Math.min(...points.map((p: number[]) => Math.hypot(p[0]-robot[0], p[1]-robot[1]))) : null;
  const barMetres = Math.max(1, Math.ceil(50 / (size / (2*extent))));
  return <View accessibilityLabel={route ? `Rota: ${route.status}, ${route.completed_count}/${route.waypoints.length} nokta` : 'Mesafe haritası'} style={[styles.mapSurface, {width: size, height: size}]}>
    {[.5, 1].map(f => <View key={f} style={[styles.mapRing, {width: size * f, height: size * f,
      borderRadius: size * f / 2, left: size * (1-f) / 2, top: size * (1-f) / 2}]}/>) }
    {(map?.boundary_paths ?? []).flatMap((path: number[][], pi: number) =>
      path.slice(1).map((p: number[], i: number) => {
        const a = point(path[i]), b = point(p);
        const length = Math.hypot(b.left-a.left, b.top-a.top);
        const angle = Math.atan2(b.top-a.top, b.left-a.left);
        return <View key={`${pi}-${i}`} style={{position: 'absolute',
          left: (a.left+b.left-length)/2, top: (a.top+b.top)/2-1,
          width: length, height: 2, backgroundColor: '#ff9981',
          transform: [{rotate: `${angle}rad`}]}}/>;
      }))}
    {route && routePoints.slice(1).map((target, index) => {
      const a = point(routePoints[index]), b = point(target);
      const length = Math.hypot(b.left-a.left, b.top-a.top);
      const angle = Math.atan2(b.top-a.top, b.left-a.left);
      const done = index < route.completed_count;
      const active = index === route.active_index && route.status === 'following';
      const color = done ? '#69d49a' : active ? '#ffd66e' : '#67d9ed';
      const radius = active ? 9 : 7;
      return <React.Fragment key={`${route.route_id}-${index}`}>
        <View style={{position: 'absolute', left: (a.left+b.left-length)/2,
          top: (a.top+b.top)/2-1.5, width: length, height: 3,
          backgroundColor: color, opacity: done ? .65 : 1, transform: [{rotate: `${angle}rad`}]}}/>
        <View style={{position: 'absolute', left: b.left-radius, top: b.top-radius,
          width: radius*2, height: radius*2, borderRadius: radius, backgroundColor: color,
          alignItems: 'center', justifyContent: 'center'}}>
          <Text style={{fontSize: 10, fontWeight: 'bold', color: '#101824'}}>{index+1}</Text>
        </View>
      </React.Fragment>;
    })}
    <View style={[styles.mapStart, {left: size/2-3, top: size/2-3}]}/>
    <View style={[styles.mapRobot, {left: r.left-4, top: r.top-4}]}/>
    <Text style={{position: 'absolute', top: 3, left: 3, color: '#fff', fontSize: 9}}>
      {nearest === null ? 'Sınır ölçümü bekleniyor' : `Kayıtlı sınır ≈ ${nearest.toFixed(2)} m`}{'\n'}
      {route ? `Rota · ${{following: 'İlerliyor', completed: 'Tamamlandı', blocked: 'Engellendi', cancelled: 'İptal', error: 'Durdu'}[route.status]} · ${route.completed_count}/${route.waypoints.length}\n` : ''}
      {typeof distance === 'number' && Number.isFinite(distance) ? `Lidar yönü: ${distance.toFixed(2)} m` : 'Lidar: — m'}
    </Text>
    <View style={{position: 'absolute', bottom: 8, left: 8, width: barMetres*size/(2*extent), borderBottomWidth: 2, borderColor: '#fff'}}>
      <Text style={{color: '#fff', fontSize: 10}}>{barMetres} m</Text>
    </View>
  </View>;
}

const styles = StyleSheet.create({
  mapSurface: {overflow: 'hidden', backgroundColor: '#101824cc', borderRadius: 5},
  mapRing: {position: 'absolute', borderColor: '#78919e55', borderWidth: 1},
  mapStart: {position: 'absolute', width: 6, height: 6, borderRadius: 3, backgroundColor: '#67d9ed'},
  mapRobot: {position: 'absolute', width: 8, height: 8, borderRadius: 4, backgroundColor: '#ffd66e'},
});
