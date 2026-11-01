// Move the original controls into a phone layout; keep one binding and one socket.
const $ = id => document.getElementById(id);
const compactQuery = '(max-width: 760px), (max-width: 1100px) and (max-height: 550px)';

export function initMobileControl(link, {onLayout, openTools}) {
  const media = matchMedia(compactQuery);
  const panel = $('mobile-robot-panel');
  const controller = document.querySelector('.controller');
  let placements = [], mounted = false;
  const specs = [
    ['#menu-open', 'mobile-brand-slot'], ['.top-center .connection', 'mobile-connection-slot'],
    ['.mode-switch', 'mobile-mode-slot'], ['#mode-notice', 'mobile-state-slot'], ['#claim', 'mobile-state-slot'],
    ['.live-heading', 'mobile-live-slot'], ['.speed-control', 'mobile-speed-slot'],
    ['.wake-heard', 'mobile-heard-slot'], ['#navigation-toggle', 'mobile-navigation-slot'],
    ['#navigation-status', 'mobile-navigation-slot'],
    ['label.eye-control', 'mobile-eyes-slot'], ['.sensor', 'mobile-sensors-slot'],
    ['label[for="leftArm"], #leftArm', 'mobile-left-arm-slot'],
    ['label[for="rightArm"], #rightArm', 'mobile-right-arm-slot'],
    ['#head-direction, #head-reference, #prepare-mapping', 'mobile-orientation-slot'],
    ['#distance-map', 'mobile-map-slot'],
  ];
  function syncLayout() {
    if (!mounted) return;
    if (panel.open) panel.close();
    if (media.matches) for (const {node, destination} of placements) destination.append(node);
    else for (const {node, anchor} of placements) anchor.after(node);
    onLayout();
  }
  function close() { if (panel.open) panel.close(); }
  $('mobile-details').addEventListener('click', () => {
    if (!media.matches) return;
    link.stopManualInput(); panel.showModal(); onLayout();
    $('mobile-details-close').focus({preventScroll: true});
  });
  $('mobile-details-close').addEventListener('click', close);
  panel.addEventListener('close', () => { link.stopManualInput(); onLayout(); });
  panel.addEventListener('click', event => {
    const rect = panel.getBoundingClientRect();
    if (event.target === panel && (event.clientY < rect.top || event.clientX < rect.left || event.clientX > rect.right)) close();
  });
  for (const id of ['mobile-stop', 'mobile-panel-stop']) $(id).addEventListener('click', () => link.stop());
  $('mobile-end-voice').addEventListener('click', () => link.send({type: 'stopVoice'}));
  $('mobile-open-tools').addEventListener('click', openTools);
  media.addEventListener('change', syncLayout);

  function render(state) {
    controller.dataset.mode = state?.mode || 'remote';
    const owner = Boolean(state?.owner && link.active);
    $('mobile-stop').disabled = $('mobile-panel-stop').disabled = !owner;
    $('mobile-end-voice').disabled = !owner || state?.mode !== 'ai';
    $('mobile-open-tools').disabled = !owner || state?.mode !== 'tools';
    const phrase = state?.aiConfig?.settings?.activation?.phrase || 'Kufi';
    const labels = {preparing:'Hazırlanıyor', wake_listening:`“${phrase}” demeni bekliyor`,
      greeting:'Seni karşılıyor', connecting:'Bağlanıyor', connected:'Görüşme açık', listening:'Dinliyor',
      thinking:'Düşünüyor', speaking:'Konuşuyor', farewell:'Görüşme kapanıyor', disabled:'Dinleme kapalı',
      error:'Bağlantıyı kontrol et', suspended:'Dinleme duraklatıldı', idle:'Görüşme bekleniyor'};
    const voice = state?.voiceStatus?.state;
    $('mobile-voice-state').textContent = labels[voice] || (voice ? 'Görüşme bekleniyor' : 'Ajan durumu bekleniyor');
    const distance = state?.sensors?.distance;
    $('mobile-clearance').textContent = Number.isFinite(distance) ? `${distance.toFixed(1)} m` : '— m';
    $('mobile-navigation-section').hidden = state?.mode !== 'ai';
    $('mobile-connection-slot').title = state?.owner ? 'Bağlı · kontrol sende' : 'Robot bağlantısı';
  }
  return {get compact() { return media.matches; }, get panelOpen() { return panel.open; }, close, render,
    mount() {
      if (mounted) return;
      for (const [selector, target] of specs) for (const node of document.querySelectorAll(selector)) {
        const anchor = document.createComment('desktop control'); node.before(anchor);
        placements.push({node, anchor, destination: $(target)});
      }
      mounted = true; syncLayout();
    }};
}
