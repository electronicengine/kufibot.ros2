// Execute both renderers against the same telemetry without a phone/emulator.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../../..');
const ts = require(path.join(root, 'KufibotMobile/node_modules/typescript'));
const app = fs.readFileSync(path.join(root, 'src/kufibot_remote/kufibot_remote/web/app.js'), 'utf8');
const start = app.indexOf('function drawDistanceMap(');
const end = app.indexOf("\nfor (const key of ['provider'", start);
const scope = {devicePixelRatio: 1, link: {state: {sensors: {distance: 2.}}}};
vm.createContext(scope);
vm.runInContext(app.slice(start, end), scope);
const jsx = fs.readFileSync(path.join(root, 'KufibotMobile/src/DistanceMap.tsx'), 'utf8');
const compiled = ts.transpileModule(jsx, {compilerOptions: {
  jsx: ts.JsxEmit.React, module: ts.ModuleKind.CommonJS, esModuleInterop: true,
}}).outputText;
const react = {createElement: (type, props, ...children) => ({type, props: props || {}, children}), Fragment: 'Fragment'};
const native = {View: 'View', Text: 'Text', StyleSheet: {create: x => x}};
const mobile = {exports: {}, require: name => name === 'react' ? react : native};
vm.createContext(mobile);
vm.runInContext(compiled, mobile);
const map = {map_id: 'm', robot_pose: [0., 0.], obstacle_points: [], boundary_paths: []};
const plan = {route_id: 'r', map_id: 'm', start_pose: [0., 0.],
  waypoints: [{x_m: 1., y_m: 1.}, {x_m: 4., y_m: 1.}],
  completed_count: 1, active_index: 1, status: 'following'};
function flatten(node) {
  if (Array.isArray(node)) return node.flatMap(flatten);
  if (!node || typeof node !== 'object') return [];
  return [node, ...node.children.flatMap(flatten)];
}
function renderWeb(route, size) {
  const texts = [];
  const context = new Proxy({fillText: (text, x, y) => texts.push({text, x, y})}, {
    get: (target, name) => name in target ? target[name] : () => {},
  });
  const canvas = {clientWidth: size, clientHeight: size, width: size, height: size,
    dataset: {}, getContext: () => context, setAttribute: () => {}};
  scope.drawDistanceMap(canvas, map, route);
  return {texts, canvas};
}
for (const large of [false, true]) {
  const size = large ? 320 : 150;
  const tree = mobile.exports.DistanceMap({map, routePlan: plan, large});
  const nodes = flatten(tree);
  const markers = nodes.filter(n => n.type === 'View' && n.children.some(c =>
    c?.type === 'Text' && c.children.length === 1 && typeof c.children[0] === 'number'));
  assert.equal(markers.length, 2);
  const web = renderWeb(plan, size);
  assert.equal(web.canvas.dataset.waypointCount, '2');
  assert.equal(web.canvas.dataset.routeStatus, 'following');
  markers.forEach((marker, i) => {
    const style = marker.props.style;
    const text = web.texts.find(t => t.text === String(i+1));
    assert.equal(style.left + style.width/2, text.x);
    assert.equal(style.top + style.height/2, text.y);
    assert(text.x > size/2 && text.y < size/2); // +x right, +y up
    assert(text.x + style.width/2 < size); // far waypoint fits viewport
  });
  assert.equal(markers[0].props.style.backgroundColor, '#69d49a');
  assert.equal(markers[1].props.style.backgroundColor, '#ffd66e');
}
for (const status of ['completed', 'cancelled', 'blocked']) {
  const route = {...plan, status};
  assert.equal(renderWeb(route, 150).canvas.dataset.routeStatus, status);
  assert(mobile.exports.DistanceMap({map, routePlan: route}).props.accessibilityLabel.includes(status));
}
for (const route of [null, {...plan, map_id: 'reset'}]) {
  assert.equal(renderWeb(route, 150).canvas.dataset.waypointCount, '0');
  assert.equal(mobile.exports.DistanceMap({map, routePlan: route}).props.accessibilityLabel, 'Mesafe haritası');
}
assert.equal(renderWeb({...plan, route_id: 'new'}, 150).canvas.dataset.routeId, 'new');
console.log('Web/mobile route coordinates, numbering, progress, retention, replacement and reset passed.');
