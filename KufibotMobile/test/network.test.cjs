const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const ts = require('typescript');

function load(name, mocks = {}) {
  const source = path.resolve(__dirname, '../src', name);
  const file = fs.existsSync(source + '.ts') ? source + '.ts' : source + '.tsx';
  const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020,
      jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true},
  }).outputText;
  const module = {exports: {}};
  const imported = id => mocks[id] ?? (id.startsWith('./') ? load(id.slice(2), mocks) : require(id));
  vm.runInThisContext('(function(require,module,exports){' + code + '\n})', {filename: file})(imported, module, module.exports);
  return module.exports;
}

const {selectDiscovered, handoffCandidate, verifiedHandoff, robotURL} = load('discovery-state');
const original = {host: '10.42.0.1', port: 8080, name: 'Kufibot', robotId: 'robot-a', setupActive: true};
const moved = {...original, host: '192.168.5.24', setupActive: false, stationConnected: true};
const pending = {robotId: 'robot-a', ticket: 'private-ticket', ssid: 'Ev Ağı', started: 1000};

test('same robot follows its changed IP; other robots and old V1 packets do not replace it', () => {
  assert.equal(selectDiscovered(original, moved), moved);
  assert.equal(selectDiscovered(original, {...moved, robotId: 'robot-b'}), original);
  assert.equal(selectDiscovered(original, {...original, robotId: undefined}), original);
  assert.equal(selectDiscovered(null, original), original);
  const manual = {...original, robotId: undefined};
  assert.equal(selectDiscovered(manual, original), original);
  assert.equal(selectDiscovered(manual, moved), manual);
  assert.equal(robotURL(moved), 'http://192.168.5.24:8080/');
});

test('handoff requires matching identity, station success, no setup hotspot, and a live ticket', () => {
  assert.equal(handoffCandidate(pending, moved, 2000), true);
  for (const invalid of [original, {...moved, robotId: 'robot-b'}, {...moved, stationConnected: false},
      {...moved, stationConnected: undefined}, {...moved, setupActive: true}]) {
    assert.equal(handoffCandidate(pending, invalid, 2000), false);
  }
  assert.equal(handoffCandidate(pending, moved, 901000), false);
  assert.equal(verifiedHandoff(pending, {robot_id: 'robot-a', connected: true}), true);
  for (const invalid of [null, {}, {robot_id: 'robot-b', connected: true}, {robot_id: 'robot-a', connected: false}]) {
    assert.equal(verifiedHandoff(pending, invalid), false);
  }
});

test('verification uses the discovered IP and read-only ticket; wrong robots and HTTP failures are rejected', async () => {
  const {verifyNetworkHandoff} = load('network-api');
  const before = global.fetch;
  try {
    let calls = 0;
    global.fetch = async (url, options) => {
      calls++;
      assert.equal(url, 'http://192.168.5.24:8080/api/network-identity?handoff=private-ticket');
      assert.equal(options.headers, undefined);
      return {ok: true, json: async () => ({robot_id: 'robot-a', connected: true})};
    };
    assert.equal(await verifyNetworkHandoff(pending, moved), true);
    assert.equal(calls, 1);
    global.fetch = async () => ({ok: true, json: async () => ({robot_id: 'robot-b', connected: true})});
    assert.equal(await verifyNetworkHandoff(pending, moved), false);
    global.fetch = async () => ({ok: false});
    assert.equal(await verifyNetworkHandoff(pending, moved), false);
    global.fetch = async () => { throw new Error('offline'); };
    assert.equal(await verifyNetworkHandoff(pending, moved), false);
  } finally { global.fetch = before; }
});

test('Wi-Fi password goes only in the authorized POST body', async () => {
  const {networkRequest} = load('network-api');
  const before = global.fetch;
  try {
    global.fetch = async (url, options) => {
      assert.equal(url, 'http://10.42.0.1:8080/api/device-settings/network/connect');
      assert.equal(options.method, 'POST');
      assert.equal(options.headers.Authorization, 'Bearer controller-token');
      assert.equal(JSON.parse(options.body).password, 'target-private-password');
      assert.equal(JSON.stringify(options.headers).includes('target-private-password'), false);
      return {ok: true, json: async () => ({handoff: 'ticket'})};
    };
    assert.deepEqual(await networkRequest(original, 'controller-token', 'network/connect', {
      ssid: 'Ev Ağı', security: 'wpa-psk', password: 'target-private-password',
    }), {handoff: 'ticket'});
  } finally { global.fetch = before; }
});

test('returning from phone Wi-Fi settings scans immediately and disposal ignores pending replies', async () => {
  let listener, scans = 0, removed = false, finish;
  const appState = {currentState: 'background', addEventListener: (_, callback) => {
    listener = callback;
    return {remove: () => { removed = true; }};
  }};
  const native = {scan: () => { scans++; return new Promise(resolve => { finish = resolve; }); }};
  const {discover} = load('discovery', {
    'react-native': {AppState: appState}, expo: {requireNativeModule: () => native},
  });
  const results = [], errors = [];
  const dispose = discover(value => results.push(value), error => errors.push(error));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(scans, 0);
  appState.currentState = 'active'; listener('active');
  assert.equal(scans, 1);
  listener('active'); // No overlapping native scans.
  assert.equal(scans, 1);
  finish([moved]);
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(results, [[moved]]);
  dispose();
  assert.equal(removed, true);
  assert.deepEqual(errors, []);
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(scans, 1);
});

test('mobile form sends the selected Wi-Fi password, clears it, and offers phone Wi-Fi settings', async () => {
  const React = require('react');
  const {create, act} = require('react-test-renderer');
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const calls = [], intents = [], handoffs = [];
  const networkRequest = async (robot, token, route, value) => {
    calls.push({robot, token, route, value});
    if (route === 'network/connect') return {robot_id: 'robot-a', handoff: 'result-ticket'};
    return {networks: [{id: 'ap-1', ssid: 'Ev Ağı', security: 'WPA/WPA2',
      password_allowed: true, saved: false, supported: true, connected: false}],
      addresses: ['10.42.0.1'], hotspot: {active: true, ssid: 'kufi', robot_id: 'robot-a'}};
  };
  const {NetworkSettings} = load('NetworkSettings', {
    'react-native': {View: 'View', Text: 'Text', TextInput: 'TextInput', Pressable: 'Pressable',
      StyleSheet: {create: value => value}, Linking: {sendIntent: async intent => { intents.push(intent); }}},
    './network-api': {networkRequest},
  });
  let root;
  const props = {robot: original, token: 'owner-token', owner: true, pending: null,
    onHandoff: value => handoffs.push(value), onFailure: () => {}};
  const button = label => root.root.findAllByType('Pressable').find(node =>
    node.findAllByType('Text').map(text => text.props.children).flat().join('') === label);
  try {
    await act(async () => { root = create(React.createElement(NetworkSettings, props)); });
    await act(async () => { button('Ev Ağı · WPA/WPA2').props.onPress(); });
    await act(async () => {
      root.root.findByProps({accessibilityLabel: 'Wi-Fi parolası'}).props.onChangeText('secret-password');
    });
    await act(async () => { button('Robotu bu ağa bağla').props.onPress(); });
    assert.equal(calls.at(-1).route, 'network/connect');
    assert.equal(calls.at(-1).value.password, 'secret-password');
    assert.equal(root.root.findByProps({accessibilityLabel: 'Wi-Fi parolası'}).props.value, '');
    assert.equal(handoffs[0].robotId, 'robot-a');
    assert.equal(handoffs[0].ssid, 'Ev Ağı');
    assert.equal(handoffs[0].ticket, 'result-ticket');
    assert.equal(JSON.stringify(handoffs).includes('secret-password'), false);
    await act(async () => { root.update(React.createElement(NetworkSettings, {...props, pending: handoffs[0]})); });
    assert.equal(button('Robotu bu ağa bağla').props.disabled, true);
    await act(async () => { button('Telefonun Wi-Fi ayarlarını aç').props.onPress(); });
    assert.deepEqual(intents, ['android.settings.WIFI_SETTINGS']);
  } finally { if (root) await act(async () => { root.unmount(); }); }
});

test('mobile form prevents a viewer from changing Wi-Fi and reports a failed connection after returning to setup', async () => {
  const React = require('react');
  const {create, act} = require('react-test-renderer');
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const failures = [];
  const {NetworkSettings} = load('NetworkSettings', {
    'react-native': {View: 'View', Text: 'Text', TextInput: 'TextInput', Pressable: 'Pressable',
      StyleSheet: {create: value => value}, Linking: {sendIntent: async () => {}}},
    './network-api': {networkRequest: async () => ({status: {
      state: 'error', message: 'Parola yanlış. Önceki ağa dönüldü.'}})},
  });
  let root;
  try {
    await act(async () => { root = create(React.createElement(NetworkSettings, {
      robot: original, owner: false, pending, onHandoff: () => {}, onFailure: message => failures.push(message),
    })); });
    const scan = root.root.findAllByType('Pressable').find(node =>
      node.findAllByType('Text').some(text => text.props.children === 'Wi-Fi ağlarını tara'));
    assert.equal(scan.props.disabled, true);
    assert.deepEqual(failures, ['Parola yanlış. Önceki ağa dönüldü.']);
  } finally { if (root) await act(async () => { root.unmount(); }); }
});

test('controller follows a verified network handoff and opens the discovered numeric web address', async () => {
  const React = require('react');
  const {create, act} = require('react-test-renderer');
  global.IS_REACT_ACT_ENVIRONMENT = true;
  let announce, panel, connected, finish;
  const opened = [], checks = [];
  const stub = () => null;
  const {default: App} = load('../App', {
    'react-native': {View: 'View', Text: 'Text', TextInput: 'TextInput', Pressable: 'Pressable',
      Modal: 'Modal', ScrollView: 'ScrollView', StatusBar: 'StatusBar', Switch: 'Switch',
      StyleSheet: {create: value => value, absoluteFill: {}},
      Linking: {openURL: async url => opened.push(url)}, Share: {share: async () => {}}},
    'react-native-safe-area-context': {SafeAreaProvider: 'SafeAreaProvider', SafeAreaView: 'SafeAreaView'},
    'react-native-webrtc': {RTCView: 'RTCView'},
    '@react-native-community/slider': {__esModule: true, default: stub},
    'expo-keep-awake': {useKeepAwake: () => {}},
    './src/discovery': {discover: callback => { announce = callback; return () => {}; }},
    './src/discovery-state': load('discovery-state'),
    './src/network-api': {verifyNetworkHandoff: (handoff, candidate) => {
      checks.push({handoff, candidate});
      return new Promise(resolve => { finish = resolve; });
    }},
    './src/useRobot': {useRobot: robot => {
      connected = robot;
      return {state: robot ? {owner: true, sensors: {}, joints: {}, workflowToken: 'owner',
        mode: 'remote', appliedMode: 'remote'} : null,
      send: () => {}, stop: () => {}, input: () => {}, subscribeWorkflow: () => () => {}};
    }},
    './src/NetworkSettings': {NetworkSettings: props => { panel = props; return null; }},
    './src/CalibrationChart': {CalibrationChart: stub}, './src/HeardText': {HeardText: stub},
    './src/Joystick': {Joystick: stub}, './src/DistanceMap': {DistanceMap: stub},
    './src/MimicEditor': {MimicEditor: stub}, './src/AiSettingsPanel': {AiSettingsPanel: stub},
    './src/VerasistAudioSettings': {VerasistAudioSettingsPanel: stub},
    './src/WorkflowEditor': {WorkflowEditor: stub},
  });
  let root;
  try {
    await act(async () => { root = create(React.createElement(App)); });
    await act(async () => { announce([original]); });
    assert.equal(connected.host, original.host);
    const connectionPage = root.root.findAllByType('Pressable').find(node =>
      node.findAllByType('Text').some(text => text.props.children === 'Bağlantı Ayarları'));
    await act(async () => { connectionPage.props.onPress(); });
    await act(async () => { panel.onHandoff({...pending, started: Date.now()}); });
    await act(async () => { announce([{...moved, robotId: 'another-robot'}, original]); });
    assert.equal(checks.length, 0);
    assert.equal(connected.host, original.host);
    await act(async () => { announce([moved]); });
    assert.equal(checks.length, 1);
    assert.equal(connected.host, original.host);
    await act(async () => { finish(true); });
    assert.equal(connected.host, moved.host);
    assert.equal(panel.pending, null);
    const open = root.root.findAllByType('Pressable').find(node =>
      node.findAllByType('Text').some(text => text.props.children === 'IP ile web arayüzünü aç'));
    assert.equal(open.props.disabled, false);
    await act(async () => { open.props.onPress(); });
    assert.deepEqual(opened, ['http://192.168.5.24:8080/']);
  } finally { if (root) await act(async () => { root.unmount(); }); }
});
