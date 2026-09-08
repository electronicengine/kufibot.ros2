# Raspberry Pi üzerinde yerel sesli ajan

Web ve mobil bağlantı menüsündeki **Sesli Ajan Ayarları** bölümünden
**Verasist AI** veya **Local AI** seçilir. Local AI için dil ve o dili destekleyen
STT, LLM, TTS modellerini seçin. Aynı bölümdeki **Sistem mesajı** alanına yerel
ajanın rolünü, üslubunu ve kurallarını yazıp **Ayarları kaydet ve uygula**
düğmesine basın.
Ardından ana ekrandan **YZ modu** seçin. Ayarları kaydetmek mikrofonu tek
başına açmaz; YZ modu seçili sağlayıcıyı başlatır. YZ modu zaten açıksa ayar değişikliği
mevcut görüşmeyi kapatıp seçilen sağlayıcıyı başlatır. Kumanda modu sesi durdurur.
Ayar kaydetmek tek başına robotun hareket modunu değiştirmez.

Local AI, robotun ALSA mikrofon/hoparlör aygıtlarını kullanır:
Vosk → llama-cpp-python → Piper. Bulut tokenı veya Verasist UUID gerektirmez.
Modeller indirildikten sonra çıkarım yereldir. Web/telefon yalnızca ayar ve durum
aktarır; ses robotta işlenir. Yanıt üretilirken ve okunurken mikrofon kapalıdır
(yarı çift yönlü; araya girme yok). Yerel sohbet kamera veya Verasist robot
araçlarını kullanmaz; mevcut konuşma animasyonları korunur.

## Kurulum ve modeller

ROS2 düğümlerini çalıştıran Python ortamında:

```bash
.venv/bin/python -m pip install vosk==0.3.45 'piper-tts>=1.3,<2'
# llama-cpp-python ana requirements.txt içinde zaten mevcut.
source install/setup.bash
colcon build --symlink-install --packages-select kufibot_interaction kufibot_remote
```

Normal başlatma için `./tools/ros2_launch.sh` kullanın; bu sarmalayıcı ROS2
giriş noktalarının sanal ortamdaki Vosk/Piper bağımlılıklarını bulmasını sağlar.

`arecord` ve `aplay` (alsa-utils) gerekir. Mevcut `mic_device` ve
`speaker_device` ROS parametreleri her iki sağlayıcı için ortaktır.

Varsayılan `/usr/local/ai.models` düzeni otomatik tanınır:

- `trRecognizeModel/`: Vosk small Turkish (eski düz dizin ve `am/final.mdl` düzeni).
- `engRecognizeModel/`: İngilizce Vosk.
- `trSpeechModel/*.onnx`, `engSpeechModel/*.onnx`: yanında `.onnx.json` bulunan Piper sesleri;
  dil bu JSON içindeki `language.code` alanından alınır.
- `llamaModel/turkish_ytu.gguf` ve `llamaModel/dolphin3.gguf`: mevcut sohbet modelleri.
  `mxbaiV1.gguf` embedding modeli olduğu için sohbet listesine alınmaz.

STT önerilen model: [vosk-model-small-tr-0.3](https://alphacephei.com/vosk/models).
Piper yükleme/sentez API'si: [resmi Python belgesi](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/API_PYTHON.md).

Başka modelleri eklemek veya desteklenen dilleri özelleştirmek için
`/usr/local/ai.models/catalog.json` oluşturun. Bu dosya otomatik listeyi tamamen
geçersiz kılar. Model kimlikleri benzersiz olmalı; yollar JSON dosyasına göre
bağıl veya mutlak olabilir. Örnek:

```json
[
  {"id":"vosk-tr-small", "kind":"stt", "languages":["tr"], "path":"trRecognizeModel", "label":"Vosk Small Türkçe"},
  {"id":"chat", "kind":"llm", "languages":["tr","en"], "path":"llamaModel/dolphin3.gguf"},
  {"id":"fettah", "kind":"tts", "languages":["tr"], "path":"trSpeechModel/fettah.onnx"}
]
```

LLM modeli sohbet şablonu içeren GGUF olmalıdır; dil desteğini modelin kendi
belgelerine göre kaydedin. Model boyutu ve cevap süresi RPi RAM/CPU kapasitesine
bağlıdır. Çalışan süreç CPU üzerinde 4'e kadar iş parçacığı, 2048 token bağlamı,
160 token yanıt sınırı ve sınırlı konuşma geçmişi kullanır.

Ortam değişkenleri (voice ve remote süreçlerine aynı değerleri verin):

- `KUFIBOT_AI_CATALOG`: özel katalog JSON yolu.
- `KUFIBOT_AI_MODEL_ROOT`: otomatik keşif kökü; varsayılan `/usr/local/ai.models`.
- `KUFIBOT_AI_SETTINGS`: kalıcı ayar dosyası; varsayılan `~/.config/kufibot/ai.json`.

Ayar dosyası atomik yazılır ve yeniden başlatmada okunur. Yeni dosyalar listeye
periyodik olarak yansır. Kurulu olmayan veya seçilen dili desteklemeyen modeller
kaydedilemez. Model yükleme/ALSA/çıkarım hataları menüde gösterilir; buluta otomatik
geçiş yapılmaz. Verasist'e dönmek için sağlayıcıyı seçip uygulayın; token ve
trigger UUID için mevcut Verasist yapılandırması geçerlidir.

Sistem mesajı yalnızca Local AI işçisine verilir; Verasist iş akışının mesajını
veya talimatlarını değiştirmez. En fazla 6000 karakterdir ve seçili model/dil ile
birlikte kalıcı olarak saklanır. Önceki ayar dosyalarına varsayılan kısa Kufibot
sistem mesajı otomatik eklenir.

ROS2 kanalları:
`voice_session/set_ai_settings` (JSON String istek),
`voice_session/ai_settings` (ayar, model listesi ve hata),
`voice_session/state` ve `voice_session/transcript`.
WebSocket `setAiSettings` komutu yalnızca kumanda sahibi tarafından gönderilebilir.

Yerel LLM yanıt üretirken `local_ai/compute_active` yayınlanır. Bu sırada USB
kamera yakalama/yayınlama, MediaPipe algılama ve web-mobil canlı görüntü kodlama
durur; LLM yanıtı ürettiğinde otomatik yeniden başlar. Ses dinleme zaten bu
yarı çift yönlü aşamada kapalıdır. Verasist bu sinyali kullanmaz; kamera ve
algılama davranışı değişmez.

## Mikrofon tanılama

`listening` yalnızca ilk PCM verisi alındığında yayınlanır. Yerel ajan dinlerken
beş saniyede bir `PCM OK`, RMS seviyesi ve Vosk kısmi metnini loglar.
`Local transcript (user)` tanınan metni, `thinking` yanıt üretimini gösterir.
İngilizce model seçiliyken İngilizce konuşulmalıdır; Türkçe için `tr` ve
Türkçe STT/TTS seçin. Yanıt üretilirken mikrofon yarı çift yönlü tasarım gereği
kapalıdır. Bu normal kapanıştaki ALSA EINTR mesajı kullanıcı hatası olarak
gösterilmez; beklenmedik kayıt sonlanması ve beş saniyelik veri kesilmesi
aygıt adıyla birlikte hata olarak bildirilir.
