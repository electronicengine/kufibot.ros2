const labels = {
  happy: ['Mutlu', 'İçten bir gülümseme'], angry: ['Kızgın', 'Biraz ciddi bir itiraz'],
  funny: ['Şakacı', 'Eğlenceli ve neşeli'], serious: ['Ciddi', 'Dikkatli ve kararlı'],
  curious: ['Meraklı', 'Yeni bir şey keşfediyor'], worried: ['Endişeli', 'Biraz kaygılı'],
  surprised: ['Şaşkın', 'Beklenmedik bir sürpriz'], confident: ['Kendinden emin', 'Kararlı bir duruş'],
  greeting: ['Selam ver', 'Sıcak bir karşılama'], listening: ['Dinliyor', 'Bütün dikkati sende'],
  talking: ['Konuşuyor', 'Sözü Kufi’ye bırak'], accepting: ['Kabul et', 'Bir fikre açık'],
  rejecting: ['Reddet', 'Nazik bir hayır'], thinking: ['Düşünüyor', 'Biraz düşünme zamanı'],
  agreeing: ['Onayla', 'Evet, aynı fikirde'],
};
const emotions = new Set(['happy', 'angry', 'funny', 'serious', 'curious', 'worried', 'surprised', 'confident']);
const presets = {
  greeting: 'Merhaba! Seni gördüğüme çok sevindim.',
  thanks: 'Çok teşekkür ederim! Seninle çalışmak çok güzel.',
  introduce: 'Merhaba, ben Kufi! Birlikte keşfetmeye ve yeni şeyler öğrenmeye hazırım.',
};
const normalize = value => value.toLocaleLowerCase('tr').normalize('NFKC');
const title = motion => motion.name === motion.id ? labels[motion.id]?.[0] || motion.name : motion.name;
const duration = motion => `${(motion.duration_ms / 1000).toLocaleString('tr', {maximumFractionDigits: 1})} sn`;

function face(kind = '') {
  let eyes = '<rect x="37" y="40" width="10" height="15" rx="5"/><rect x="65" y="40" width="10" height="15" rx="5"/>';
  let mouth = '<path d="M48 64h16"/>';
  if (['happy', 'greeting', 'accepting', 'agreeing', 'funny'].includes(kind)) {
    eyes = '<path d="M35 49q7-12 14 0m14 0q7-12 14 0"/>';
    mouth = '<path d="M45 62q11 13 22 0"/>';
  } else if (['curious', 'thinking'].includes(kind)) {
    eyes = '<rect x="37" y="43" width="10" height="12" rx="5"/><rect x="65" y="37" width="10" height="18" rx="5"/>';
    mouth = '<path d="M50 65q7-5 13 0"/>';
  } else if (['surprised', 'talking'].includes(kind)) {
    mouth = '<ellipse cx="56" cy="65" rx="5" ry="7"/>';
  } else if (['angry', 'serious', 'confident', 'rejecting'].includes(kind)) {
    eyes = '<path d="m35 41 14 5m14 0 14-5"/><rect x="39" y="47" width="7" height="9" rx="3"/><rect x="66" y="47" width="7" height="9" rx="3"/>';
  } else if (kind === 'worried') {
    eyes = '<path d="m35 40 13-5m16 0 13 5"/><rect x="38" y="45" width="9" height="12" rx="4"/><rect x="65" y="45" width="9" height="12" rx="4"/>';
    mouth = '<path d="M46 68q10-10 20 0"/>';
  }
  return `<svg class="kufi-expression" viewBox="0 0 112 96" fill="none" aria-hidden="true"><path d="M56 21v-9" stroke="currentColor" stroke-width="3"/><circle cx="56" cy="10" r="4" fill="currentColor"/><rect class="face-ear" x="11" y="41" width="12" height="23" rx="5"/><rect class="face-ear" x="89" y="41" width="12" height="23" rx="5"/><rect class="face-shell" x="19" y="21" width="74" height="65" rx="23"/><rect class="face-screen" x="27" y="30" width="58" height="46" rx="15"/><g class="face-features" stroke-width="3.5" stroke-linecap="round">${eyes}${mouth}</g><path d="M43 86v5m26-5v5" stroke="currentColor" stroke-width="4" stroke-linecap="round"/></svg>`;
}

export function initPerformance(link) {
  const $ = id => document.getElementById(id);
  let motions = [], selected = null, filter = 'all', loaded = false;
  let voicesAvailable = false, pending = false, pendingId = '', localError = '', loading = false;
  let preparedVoice = null;
  const isBusy = () => pending || ['preparing', 'playing'].includes(link.state?.performance?.state);

  function prepareVoice() {
    if (!link.state?.owner || link.state.mode !== 'tools') {
      preparedVoice = null;
      return;
    }
    const voice = $('performance-voice').value;
    if (!isBusy() && voice !== preparedVoice && link.send({type: 'preparePerformance', voice}))
      preparedVoice = voice;
  }

  function selectTab(name) {
    for (const value of ['performance', 'navigation']) {
      const active = value === name;
      $(`${value}-tab`).setAttribute('aria-selected', String(active));
      $(`${value}-tab`).tabIndex = active ? 0 : -1;
      $(`${value}-panel`).hidden = !active;
    }
  }
  for (const name of ['performance', 'navigation']) {
    $(`${name}-tab`).addEventListener('click', () => selectTab(name));
    $(`${name}-tab`).addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 'performance' : event.key === 'End' ? 'navigation' : name === 'performance' ? 'navigation' : 'performance';
      selectTab(next);
      $(`${next}-tab`).focus();
    });
  }

  function renderCards() {
    const query = normalize($('expression-search').value.trim());
    const visible = motions.filter(m => (filter === 'all' || emotions.has(m.id) === (filter === 'emotion')) &&
      normalize(`${title(m)} ${m.description}`).includes(query));
    const list = $('expression-list');
    list.replaceChildren();
    for (const motion of visible) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'expression-card';
      button.dataset.mimicId = motion.id;
      button.setAttribute('aria-pressed', String(selected?.id === motion.id));
      button.setAttribute('aria-label', `${title(motion)}, ${duration(motion)}`);
      button.title = motion.name === motion.id ? labels[motion.id]?.[1] || motion.description : motion.description;
      button.innerHTML = `${face(motion.id)}<span class="expression-check" aria-hidden="true">✓</span>`;
      const name = document.createElement('strong');
      name.textContent = title(motion);
      const time = document.createElement('span');
      time.className = 'expression-time';
      time.textContent = duration(motion);
      button.append(name, time);
      button.disabled = isBusy();
      button.addEventListener('click', () => { selected = motion; renderCards(); render(); });
      list.append(button);
    }
    $('expression-empty').hidden = visible.length > 0;
    $('expression-empty').textContent = !loaded ? 'Mimikler yükleniyor…' : motions.length ? 'Aramana uygun mimik bulunamadı.' : 'Henüz mimik yok. Mimik Editörü’nden bir mimik ekleyebilirsin.';
    $('expression-preview-face').innerHTML = face(selected?.id);
    $('expression-selected').textContent = selected ? title(selected) : 'Mimik seçilmedi';
    $('expression-duration').textContent = selected ? `${duration(selected)} süren hareket` : 'Yalnızca konuşma da gönderebilirsin.';
  }

  function render() {
    const state = link.state;
    const busy = isBusy();
    const ready = state?.owner && state.mode === 'tools';
    const text = $('performance-text').value.trim();
    $('performance-count').textContent = `${$('performance-text').value.length} / 1000`;
    $('performance-connection').textContent = !state ? 'Robot bağlantısı bekleniyor' : state.owner ? 'Robot bağlı' : 'İzleyici modu';
    $('performance-connection').classList.toggle('connected', !!state);
    $('performance-play').disabled = !ready || busy || loading || (!text && !selected) || (!!text && !voicesAvailable);
    $('performance-play-label').textContent = text ? selected ? 'Söyle ve oynat' : 'Söyle' : 'Mimiği oynat';
    $('performance-mimic').disabled = !ready || busy || !selected;
    $('performance-stop').disabled = !ready || !busy;
    $('performance-text').disabled = busy;
    $('performance-voice').disabled = busy || !voicesAvailable;
    $('expression-clear').disabled = busy || !selected;
    $('performance-refresh').disabled = busy || loading;
    for (const button of $('expression-list').querySelectorAll('button')) button.disabled = busy;
    const status = state?.performance || {};
    const messages = {preparing: 'Ses hazırlanıyor…', playing: 'Kufi sahnede.', completed: 'Tamamlandı.', stopped: 'Oynatma durduruldu.', error: 'Oynatılamadı.'};
    $('performance-status').textContent = localError ? 'Gönderilemedi.' : !ready ? !state ? 'Robota bağlanılıyor…' : !state.owner ? 'Kontrol başka bir cihazda.' : 'Araç çağır moduna geç.' : pending ? 'Robota gönderiliyor…' : messages[status.state] || 'Sahne senin.';
    $('performance-detail').textContent = localError || status.error || (busy ? 'Durdur ile sesi ve hareketi birlikte kesebilirsin.' : 'Mimik ve konuşma birlikte başlayacak.');
    $('performance-state-dot').dataset.state = localError ? 'error' : busy ? 'playing' : status.state || 'idle';
    $('performance-panel').classList.toggle('is-performing', busy);
  }

  async function load() {
    if (loading) return;
    loading = true;
    render();
    const results = await Promise.allSettled(['/api/mimics', '/api/performance'].map(async path => {
      const response = await fetch(path, {cache: 'no-store', signal: AbortSignal.timeout(8000)});
      if (!response.ok) throw new Error('Robot bilgileri yüklenemedi. Yenile ile tekrar deneyin.');
      return response.json();
    }));
    if (results[0].status === 'fulfilled') {
      motions = results[0].value;
      selected = motions.find(m => m.id === selected?.id) || null;
      loaded = true;
      localError = '';
    } else { loaded = true; localError = results[0].reason.message; }
    if (results[1].status === 'fulfilled') {
      const previous = $('performance-voice').value;
      const voices = results[1].value.voices;
      voicesAvailable = voices.length > 0;
      $('performance-voice').replaceChildren(new Option('Otomatik ses', ''));
      for (const voice of voices) $('performance-voice').append(new Option(`${voice.name} (${voice.languages.join(', ')})`, voice.id));
      if (voices.some(v => v.id === previous)) $('performance-voice').value = previous;
      $('performance-voice-help').textContent = voicesAvailable ? 'Ses robotun üzerinde üretilir; bir AI oturumu açılmaz.' : 'Konuşma için robotta bir ses modeli kurulu olmalı. Mimikleri oynatabilirsin.';
    } else {
      voicesAvailable = false;
      $('performance-voice-help').textContent = 'Ses listesi yüklenemedi. Yenile ile tekrar dene.';
    }
    loading = false;
    $('expression-list').setAttribute('aria-busy', 'false');
    renderCards(); render();
    prepareVoice();
  }

  function play(mimicOnly = false) {
    if (isBusy() || !link.state?.owner || link.state.mode !== 'tools') return;
    const text = mimicOnly ? '' : $('performance-text').value.trim();
    if (!text && !selected) return;
    localError = '';
    pendingId = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
    pending = link.send({type: 'perform', request_id: pendingId, text, mimic_id: selected?.id || '',
      ...(selected ? {revision: selected.revision} : {}), voice: $('performance-voice').value});
    if (!pending) localError = 'Robot bağlantısı yok. Bağlantı kurulduğunda tekrar dene.';
    render();
  }
  $('performance-play').addEventListener('click', () => play());
  $('performance-mimic').addEventListener('click', () => play(true));
  $('performance-stop').addEventListener('click', () => link.send({type: 'stopPerformance'}));
  $('performance-text').addEventListener('input', render);
  $('performance-voice').addEventListener('change', prepareVoice);
  $('performance-refresh').addEventListener('click', load);
  $('expression-search').addEventListener('input', renderCards);
  $('expression-clear').addEventListener('click', () => { selected = null; renderCards(); render(); });
  for (const button of document.querySelectorAll('[data-expression-filter]')) button.addEventListener('click', () => {
    filter = button.dataset.expressionFilter;
    for (const item of document.querySelectorAll('[data-expression-filter]')) item.setAttribute('aria-pressed', String(item === button));
    renderCards();
  });
  for (const button of document.querySelectorAll('[data-speech-preset]')) button.addEventListener('click', () => {
    if (isBusy()) return;
    $('performance-text').value = presets[button.dataset.speechPreset];
    $('performance-text').focus();
    render();
  });
  link.addEventListener('state', () => {
    if (link.state?.performance?.request_id === pendingId || !link.state?.owner) pending = false;
    render();
    prepareVoice();
  });
  link.addEventListener('error', ({detail}) => {
    if (pending && detail) { pending = false; localError = String(detail); render(); }
  });
  renderCards(); render();
  return {open() { selectTab('performance'); load(); }, selectTab};
}
