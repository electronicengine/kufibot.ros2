# Raspberry Pi üzerinde yerel sesli ajan

Canvas üzerinden araç bağlama ve PDF/CSV/JSON dosyalarından yerel bilgi arama için
[Local workflow ve bilgi koleksiyonları](local-workflows.md) belgesine bakın.

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
Silero VAD → Vosk veya Hailo Whisper → llama-cpp-python → Piper. Bulut tokenı veya Verasist UUID gerektirmez.
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
- `llamaModel/ufakzeka-1-q8_0.gguf`: Türkçe ufakzeka-1 sohbet modeli. Bu model
  standart llama.cpp tarafından tanınmayan `ufakzeka` ön-tokenizer'ını kullanır;
  Local AI'ın kullandığı `llama-cpp-python` bağının model kartındaki
  `llama.cpp-ufakzeka-pretok.patch` ile yeniden derlenmiş olması gerekir. Robotta
  kurulu bağ bu yama ile derlenmiştir. `qwen2` olarak yeniden adlandırma doğru
  tokenleştirmeyi bozduğu için kullanılmamalıdır.

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

Yerel LLM yanıt üretirken `local_ai/compute_active` yayınlanır. Kaynak önceliği
ayrıca `local_ai/phase` üzerinden dinleme, STT ve sentez aşamalarını kapsar;
ayrıntılar aşağıdaki ses önceliği bölümündedir. Verasist'in kamera ve algılama
davranışı değişmez.

## Mikrofon tanılama

`listening` yalnızca ilk PCM verisi alındığında yayınlanır. Yerel ajan dinlerken
beş saniyede bir `PCM OK` ve VAD konuşma olasılığını loglar.
`Local transcript (user)` tanınan metni, `thinking` yanıt üretimini gösterir.
İngilizce model seçiliyken İngilizce konuşulmalıdır; Türkçe için `tr` ve
Türkçe STT/TTS seçin. Yanıt üretilirken mikrofon yarı çift yönlü tasarım gereği
kapalıdır. Bu normal kapanıştaki ALSA EINTR mesajı kullanıcı hatası olarak
gösterilmez; beklenmedik kayıt sonlanması ve beş saniyelik veri kesilmesi
aygıt adıyla birlikte hata olarak bildirilir.

## Ses önceliği ve VAD (yerel sağlayıcı)

Local AI modelleri bellekte tutar. STT bittikten sonra LLM başlar; tamamlanan
cümleler LLM yanıtının geri kalanı üretilirken Piper ile seslendirilir.
`local_ai/phase` (JSON String) şu aşamaları taşır: `loading`, `listening`,
`transcribing`, `thinking`, `synthesizing`, `speaking`, `idle`.
İlk beş aşamada kamera, canlı video kodlama ve görsel takip durur. LLM akışında
biten ilk cümle Piper kuyruğuna hemen aktarılır; yanıtın tamamlanması beklenmez.
Piper sesi parça parça, konuşma turu boyunca açık tutulan tek ALSA akışına yazar.
LLM üretirken aşama `thinking`, üretim bitip kuyruk boşalırken `synthesizing`
olarak kalır. Bütün sentez bittiğinde kalan ses oynatılırken kamera açılır.
Yeni dinleme başlamadan tekrar durur.

Worker 750 ms aralıkla heartbeat üretir. Tüketiciler 3 saniye yeni aşama mesajı
alamazsa beklemeyi bırakır; ana ses düğümü ilk olaydan sonra 6 saniye worker çıktısı alamazsa
süreci ve alt süreçlerini kapatıp hata bildirir. İlk Python başlangıcı için
30 saniyelik üst sınır vardır; bu aralıkta ana düğüm yükleme heartbeat'ini
yeniler. Kapanışta `idle` yayınlanır.
`local_ai/compute_active` eski anlamını korur: yalnızca LLM çıkarımı sırasında
true olur. Kamera tüketicileri artık süre aşımı olan aşama kanalını kullanır.
Motor watchdog'ları ve sensör tazelik kontrolleri yavaşlatılmaz. Kamera yokken
navigasyon eski görsel veriyi “yol açık” kabul etmez.

Mimikler Local AI'da hafif yerel sınıflandırıcıyla seçilir. Embedding worker
askıya alınır; devam eden native çıkarım bitmeden yerel ses başlatılmaz.
Verasist'e geçildiğinde embedding seçimi yeniden etkinleşir.

Silero ONNX VAD tek CPU thread'i kullanır. 16 kHz / 512 örnek (32 ms) karelerde
çalışır; yaklaşık 300 ms ön tampon, 96 ms minimum konuşma ve 600 ms sessizlik
başlangıç değerleridir. Sessizlik ve çok kısa gürültü STT/LLM'ye gönderilmez.
VAD konuşma dilini seçmez; `tr` veya `en` web/mobildeki **Dil** alanından gelir.
Aynı dil Whisper decoder'a ve LLM sistem mesajına açıkça eklenir.

İlk kurulumda VAD dosyasını hazırlayın; çalışma sırasında indirme yapılmaz:

```bash
.venv/bin/python tools/local_voice/prepare_assets.py
```

Hedef model dizini yazılabilir olmalıdır. Model paketi kurulumunu robotu kuran
kullanıcı yapar; kurulu dosyalar eksikse Local AI açık bir hata verir.
VAD eşiği, ön tampon, minimum konuşma, sessizlik ve maksimum konuşma uzunluğu
`local_vad_*` / `local_max_utterance_sec`; LLM thread, batch thread, context ve
yanıt sınırı `local_llm_*`; Piper thread sayısı `local_tts_threads` ROS
parametreleridir. Başlangıç değerleri `interactive_robot.yaml` içindedir.
Ayarlar bir sonraki yerel oturum başlangıcında okunur.

## İsteğe bağlı Hailo-8L Whisper

Vosk kurulumları varsayılan olarak korunur. Hailo seçimi Vosk'u otomatik
kaldırmaz veya değiştirmez. HailoRT 4.20 / Hailo-8L üzerinde encoder ve decoder
ayrı HEF'lerle çalışır; bu yol Hailo-10H GenAI API'sini kullanmaz.

```bash
.venv/bin/python -m pip install -r requirements-voice-hailo.txt
.venv/bin/python tools/local_voice/prepare_assets.py --variant tiny --variant base
```

Kurulum URL'leri ve SHA256 değerleri `tools/local_voice/assets.lock.json` içinde
sabitlenmiştir. Hailo kaynak kodunun commit'i ve model dosyalarının özetleri
paket manifestine yazılır. Worker oturum başında dosya bütünlüğünü doğrular.
Paketler `whisper-tiny-hailo8l` ve `whisper-base-hailo8l` dizinlerinde otomatik
keşfedilir. Özel katalog kullanılıyorsa STT kaydı şöyle eklenir:

```json
{"id":"whisper-tiny-hailo8l","kind":"stt","backend":"hailo_whisper",
 "languages":["tr","en"],"path":"whisper-tiny-hailo8l",
 "label":"Whisper tiny · Hailo-8L"}
```

`backend` bulunmayan eski STT kayıtları Vosk kabul edilir. Eksik/uyumsuz paketler
seçilemez. Hailo donanım/çıkarım hatası buluta veya Vosk'a sessiz geçiş yapmaz.
Hailo CPU ön işleminde NumPy/SciPy ve offline `tokenizers` kullanılır;
PyTorch/Transformers yüklenmesi gerekmez.

HEF encoder penceresi 5/10 saniye olsa da decoder kapasitesi 24/32 token ile
sınırlıdır. Uzun konuşmalar en fazla 3 saniyelik, 250 ms örtüşmeli parçalara
ayrılır. Kapasite aşımında en fazla iki seviyeli daha kısa parça denemesi
uygulanır; sonuç sessizce kırpılmaz. Sadece pencere sınırındaki en fazla üç
eşleşen kelime birleştirilir; konuşma içindeki tekrarlar genel olarak silinmez.
VAD aşamasında ses tamponu sınırlıdır ve her native Hailo çağrısının zaman aşımı
vardır. `local_hailo_timeout_sec` pencere başına toplam süre sınırıdır.

## Tekrarlanabilir ölçüm

Canlı görüşmede `local_ai/metrics` JSONL olayları model yükleme, konuşma sonu,
endpoint, STT sonucu, ilk LLM token'ı, LLM bitişi, TTS ve oynatma zamanlarını;
worker CPU süresi, RSS/swap ve Pi sıcaklığını içerir. Hailo STT sonuç olayı ayrıca
ön işleme, encoder ve decoder sürelerini taşır. Her worker'ın ayrı session ID'si
vardır; yeniden başlatılan oturumlar karıştırılmaz.

```bash
source tools/_ros2_env.sh
python tools/local_voice/capture_metrics.py /tmp/voice-metrics.jsonl
# Görüşme bittikten sonra Ctrl-C:
python tools/local_voice/summarize_metrics.py /tmp/voice-metrics.jsonl
```

Ana ölçüt konuşma sonundan ilk PCM'nin ALSA'ya yazılmasına kadar p50/p95'tir.
Bu, hoparlörden akustik sesin çıktığı an değildir; ALSA/aygıt tampon gecikmesi
ayrı ölçüm gerektirir. Konuşma sonu canlı VAD tahminidir; insan işaretli kayıtlar
endpoint doğruluğunun ayrıca ölçülmesini sağlar.

Karşılaştırma manifesti bir JSON listedir. Her kayıt `id`, `language`,
`scenario`, `wav`, `text` (referans), `speech_end_sec`, `source` içerir. WAV
16 kHz mono PCM16 olmalı; yolu manifeste göre çözülür. `benchmark_stt.py`
`baseline`, `vosk`, `hailo-tiny`, `hailo-base` seçenekleriyle aynı kayıtları
çalıştırır ve hipotez, kelime hataları, sessizlikte yanlış tetikleme, işlem
süresi, CPU, RAM, RTF ve ses zaman çizgisindeki endpoint'leri kaydeder.
Bu offline STT ölçümü uçtan uca konuşma gecikmesi olarak yorumlanmamalıdır.

```bash
export PYTHONPATH="$PWD/src/kufibot_interaction:${PYTHONPATH:-}"
export OPENBLAS_NUM_THREADS=1
python tools/local_voice/make_smoke_corpus.py /tmp/voice-corpus
python tools/local_voice/benchmark_stt.py /tmp/voice-corpus/manifest.json \
  --backend vosk --language tr --output /tmp/vosk-tr.jsonl
python tools/local_voice/benchmark_llm.py \
  --model /usr/local/ai.models/llamaModel/dolphin3.gguf \
  --threads 4 --batch-threads 4 --output /tmp/llm-4.jsonl
```

`make_smoke_corpus.py` Piper ile sentetik test sesi üretir. Bu testler donanım ve
regresyon kontrolüdür; gerçek kullanıcı konuşması üzerindeki doğruluğun yerine
geçmez. Hailo ancak her dilde gerçek kayıtlarda doğruluk gerilemeden süre veya
CPU kazancı doğrulanırsa önerilen model yapılmalıdır.

Bütün worker akışını mikrofon/hoparlörü açmadan test etmek için
`replay_turn.py --wav ... --language tr --backend vosk` kullanılabilir. WAV
mikrofon hızında okunur, gerçek LLM ve Piper çalışır, çıktı ALSA `null` aygıtına
gider. JSONL çıktısı `summarize_metrics.py` ile özetlenir. Bu ölçüm yazılım
akışının gecikmesidir; fiziksel ses aygıtı veya gerçek arka plan ROS yükü
ölçüldüğü iddia edilmemelidir.

Heartbeat küçük bir yardımcı süreçten gelir; native model yükleyicisinin GIL'i
tutması heartbeat'i kesmez. Yardımcı süreç modeller yüklenmeden önce oluşturulur,
worker öldüğünde Linux parent-death sinyaliyle kapanır ve tüm alt süreçler aynı
process group içinde durdurulur. Böylece ölen bir worker'ın heartbeat yardımcısı
kamerayı süresiz durduramaz.


### LLM cümle akışından erken seslendirme

Cümle tamponu `.` / `!` / `?` / `…` sonrasında boşluk veya satır sonunu görünce
cümleyi aktarır. Ondalık sayılar, baş harfler ve yaygın Türkçe/İngilizce
kısaltmalar (`Dr.`, `Prof.`, `örn.`, `Mr.` vb.) ortadan bölünmez. Yanıt sonunda
noktalama işareti olmayan son parça da bir kez seslendirilir. Konuşma geçmişi
ve son transcript, LLM'den gelen özgün tam metni korur.

Piper ayrı bir thread üzerinde çalışır. Kuyruk en fazla iki bekleyen cümle
tutar; dolduğunda üretici bekler, cümleler atılmaz. CPU başlangıç ayarları
3 LLM üretim thread'i, 4 prompt thread'i ve 1 Piper thread'idir. Ses oynatma
ilk PCM ile başlar; mikrofon ancak bütün cümleler ve oynatma tamamlanınca açılır.
LLM/Piper/ALSA hatasında kuyruk iptal edilir ve oynatma süreci kapatılır; eski
cümleler sonraki konuşmaya taşınmaz. `local_ai/compute_active` LLM yanıt akışı
bitene kadar true kalır. Konuşma animasyonu ilk sesle başlar ve tur sonunda biter.

`sentence_ready`, `tts_sentence_start` ve `tts_sentence_end` ölçüm olayları da
üretilir. `playback_start`, artık `llm_end` olayından **önce** oluşabilir.
Aynı model, sabit seed/temperature ve aynı prompt ile eski tam yanıt bekleme
yolunu karşılaştırmak için:

```bash
export PYTHONPATH="$PWD/src/kufibot_interaction:${PYTHONPATH:-}"
export OPENBLAS_NUM_THREADS=1
python tools/local_voice/benchmark_sentence_stream.py \
  --output /tmp/sentence-stream.json > /tmp/sentence-stream-events.jsonl
```

Bu karşılaştırma gerçek LLM ve Piper'ı kullanır; ALSA `null` ile ölçülen ilk PCM
zamanı fiziksel hoparlörden duyulan sesin zamanı değildir.
