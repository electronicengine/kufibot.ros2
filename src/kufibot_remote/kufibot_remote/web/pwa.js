(() => {
  const button = document.getElementById('pwa-install');
  const help = document.getElementById('pwa-install-help');
  const fullscreenButton = document.getElementById('fullscreen-toggle');
  const fullscreenLabel = document.getElementById('fullscreen-label');
  const fullscreenHelp = document.getElementById('fullscreen-help');
  const standalone = matchMedia('(display-mode: standalone)');
  const fullscreen = matchMedia('(display-mode: fullscreen)');
  let installPrompt;
  const installed = () => standalone.matches || fullscreen.matches || navigator.standalone === true;
  const fullscreenSupported = document.fullscreenEnabled !== false
    && typeof document.documentElement.requestFullscreen === 'function';
  const updateFullscreen = () => {
    const active = document.fullscreenElement !== null;
    fullscreenButton.setAttribute('aria-pressed', String(active));
    fullscreenLabel.textContent = active ? 'Tam ekrandan çık' : 'Tam ekran yap';
    fullscreenHelp.hidden = true;
  };
  const update = () => {
    button.hidden = installed();
    if (installed()) help.hidden = true;
  };
  fullscreenButton.disabled = !fullscreenSupported;
  if (!fullscreenSupported) {
    fullscreenButton.title = 'Bu tarayıcı tam ekran modunu desteklemiyor.';
    fullscreenHelp.textContent = 'Tam ekran için uygulamayı yükleyip ana ekrandan açın.';
    fullscreenHelp.hidden = false;
  } else {
    fullscreenButton.addEventListener('click', async () => {
      try {
        if (document.fullscreenElement) await document.exitFullscreen();
        else await document.documentElement.requestFullscreen();
      } catch (error) {
        fullscreenHelp.textContent = `Tam ekran açılamadı: ${error.message}`;
        fullscreenHelp.hidden = false;
      }
    });
  }
  update();
  updateFullscreen();
  standalone.addEventListener('change', update);
  fullscreen.addEventListener('change', update);
  document.addEventListener('fullscreenchange', updateFullscreen);
  document.addEventListener('fullscreenerror', () => {
    fullscreenHelp.textContent = 'Tarayıcı tam ekran isteğini kabul etmedi.';
    fullscreenHelp.hidden = false;
  });
  window.addEventListener('beforeinstallprompt', event => {
    event.preventDefault();
    installPrompt = event;
    update();
  });
  window.addEventListener('appinstalled', () => {
    installPrompt = null;
    button.hidden = true;
    help.hidden = true;
  });
  button.addEventListener('click', async () => {
    if (installPrompt) {
      const prompt = installPrompt;
      installPrompt = null;
      button.disabled = true;
      try {
        await prompt.prompt();
        const choice = await prompt.userChoice;
        if (choice.outcome === 'accepted') button.hidden = true;
      } catch {
        help.textContent = 'Tarayıcı menüsündeki uygulama yükleme seçeneğini kullanabilirsiniz.';
        help.hidden = false;
      } finally {
        button.disabled = false;
      }
      return;
    }
    if (!window.isSecureContext) {
      help.replaceChildren('Yerel ağdan yüklemek için robotun sertifikasını bir kez kur: ');
      const link = document.createElement('a');
      link.href = '/pwa-setup';
      link.textContent = 'Yerel ağ kurulumu';
      link.style.color = '#9bb8ff';
      help.append(link);
      help.hidden = false;
      return;
    }
    const ios = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    help.textContent = ios
        ? 'Safari’de Paylaş → Ana Ekrana Ekle seçeneğini kullanın. Gösteriliyorsa “Web Uygulaması Olarak Aç” seçeneğini etkinleştirin.'
        : 'Tarayıcı menüsünden “Uygulamayı yükle” veya “Ana ekrana ekle” seçeneğini kullanın. Seçenek görünmüyorsa Chrome veya Edge ile açın.';
    help.hidden = false;
  });
  if (window.isSecureContext && 'serviceWorker' in navigator) {
    navigator.serviceWorker.register('/service-worker.js').catch(error => console.warn('Kufibot PWA:', error));
  }
})();
