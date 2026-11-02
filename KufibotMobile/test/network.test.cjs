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
  await new Promise(resolve => setTimeout(resolve, 15));
  assert.deepEqual(results, [[moved]]);
  assert.equal(scans, 2); // The queued foreground scan starts after the first completes.
  dispose();
  finish([original]); // An in-flight native reply after disposal is ignored.
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(removed, true);
  assert.deepEqual(errors, []);
  assert.deepEqual(results, [[moved]]);
  assert.equal(scans, 2);
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

test('shared web UI opens automatically and follows only the verified robot to its new IP', async () => {
  const React = require('react');
  const {create, act} = require('react-test-renderer');
  global.IS_REACT_ACT_ENVIRONMENT = true;
  let announce, finish;
  const opened = [], checks = [];
  const {default: MobileApp} = load('MobileApp', {
    'react-native': {View: 'View', Text: 'Text', TextInput: 'TextInput', Pressable: 'Pressable',
      Modal: 'Modal', ScrollView: 'ScrollView', StatusBar: 'StatusBar', ActivityIndicator: 'ActivityIndicator',
      StyleSheet: {create: value => value, absoluteFillObject: {}},
      Linking: {openURL: async url => opened.push(url)}, Share: {share: async () => {}}},
    'react-native-safe-area-context': {SafeAreaView: 'SafeAreaView'},
    'expo-keep-awake': {useKeepAwake: () => {}},
    './discovery': {discover: callback => { announce = callback; return () => {}; }},
    './RobotWebView': {RobotWebView: 'RobotWebView'},
    './network-api': {verifyNetworkHandoff: (handoff, candidate) => {
      checks.push({handoff, candidate});
      return new Promise(resolve => { finish = resolve; });
    }},
  });
  let root;
  const page = () => root.root.findByType('RobotWebView');
  try {
    await act(async () => { root = create(React.createElement(MobileApp)); });
    assert.equal(root.root.findAllByType('RobotWebView').length, 0);
    await act(async () => { announce([original]); });
    assert.equal(page().props.robot.host, original.host);
    assert.equal(page().props.path, '/');
    await act(async () => { page().props.onMessage({action:'connection', connected:true}); });
    await act(async () => { page().props.onMessage({action:'networkHandoff', handoff:{...pending, started:Date.now()}}); });
    await act(async () => { announce([{...moved, robotId:'another-robot'}, original]); });
    assert.equal(checks.length, 0);
    assert.equal(page().props.robot.host, original.host);
    await act(async () => { announce([moved]); });
    assert.equal(checks.length, 1);
    assert.equal(page().props.robot.host, original.host);
    await act(async () => { finish(true); });
    assert.equal(page().props.robot.host, moved.host);
    assert.equal(page().props.path, '/#connection');
    await act(async () => { page().props.onMessage({action:'browser'}); });
    assert.deepEqual(opened, ['http://192.168.5.24:8080/']);
  } finally { if (root) await act(async () => { root.unmount(); }); }
});

test('native bridge accepts only the current robot origin and strips password and control data', () => {
  const {parseNativeMessage,sameRobotOrigin}=load('web-bridge');
  const value={type:'kufibotNative',version:1,action:'networkHandoff',robot_id:'robot-a',
    handoff:'long-private-ticket-12345',ssid:'Ev Ağı',password:'must-not-leak',command:{type:'input'}};
  const result=parseNativeMessage(JSON.stringify(value),robotURL(original),original,2000);
  assert.deepEqual(result,{action:'networkHandoff',handoff:{robotId:'robot-a',ticket:value.handoff,ssid:'Ev Ağı',started:2000}});
  assert.equal(JSON.stringify(result).includes('must-not-leak'),false);
  assert.equal(parseNativeMessage(JSON.stringify(value),'http://192.168.1.1/',original),null);
  assert.equal(parseNativeMessage(JSON.stringify({...value,robot_id:'other'}),robotURL(original),original),null);
  assert.equal(parseNativeMessage('{bad json',robotURL(original),original),null);
  assert.equal(parseNativeMessage(JSON.stringify({...value,action:'sendCommand'}),robotURL(original),original),null);
  assert.equal(sameRobotOrigin(original,'http://10.42.0.1:8080.evil.example/'),false);
  assert.equal(sameRobotOrigin(original,'file:///etc/passwd'),false);
});

test('web view keeps the shared robot page local and suspends the controller when the app backgrounds', async () => {
  const React=require('react');
  const {create,act}=require('react-test-renderer');
  global.IS_REACT_ACT_ENVIRONMENT=true;
  let stateChanged,back;
  const scripts=[],messages=[],external=[];
  const state={currentState:'active',addEventListener:(_,callback)=>{stateChanged=callback;return {remove:()=>{}};}};
  const {RobotWebView}=load('RobotWebView',{
    'react-native':{View:'View',Text:'Text',ActivityIndicator:'ActivityIndicator',
      AppState:state,BackHandler:{addEventListener:(_,callback)=>{back=callback;return {remove:()=>{}};}},
      StyleSheet:{create:value=>value,absoluteFillObject:{}},Linking:{openURL:async url=>external.push(url)}},
    'react-native-webview':{WebView:'WebView'},
  });
  let root;
  try{
    await act(async()=>{root=create(React.createElement(RobotWebView,{robot:original,path:'/',active:true,
      onMessage:value=>messages.push(value),onError:()=>{}}),
      {createNodeMock:element=>element.type==='WebView'?{injectJavaScript:script=>scripts.push(script)}:null});});
    const view=root.root.findByType('WebView');
    assert.equal(view.props.source.uri,'http://10.42.0.1:8080/');
    assert.equal(view.props.onShouldStartLoadWithRequest({url:'http://10.42.0.1:8080/workflows'}),true);
    assert.equal(view.props.onShouldStartLoadWithRequest({url:'javascript:alert(1)'}),false);
    assert.equal(view.props.onShouldStartLoadWithRequest({url:'https://example.com/help'}),false);
    assert.deepEqual(external,['https://example.com/help']);
    await act(async()=>{stateChanged('background');});
    assert.ok(scripts.at(-1).includes("new Event('pagehide')"));
    await act(async()=>{stateChanged('active');});
    assert.ok(scripts.at(-1).includes("new Event('pageshow')"));
    view.props.onMessage({nativeEvent:{url:'http://192.168.1.1/',data:JSON.stringify({type:'kufibotNative',version:1,action:'wifi'})}});
    assert.deepEqual(messages,[]);
    view.props.onMessage({nativeEvent:{url:robotURL(original),data:JSON.stringify({type:'kufibotNative',version:1,action:'ready'})}});
    assert.equal(back(),true);
    assert.ok(scripts.at(-1).includes('kufibot-native-back'));
  }finally{if(root)await act(async()=>root.unmount());}
});
