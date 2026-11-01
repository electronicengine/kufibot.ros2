# Yerel Türkçe TTS modelleri

Piper seslerine ek olarak aşağıdaki modeller hem yerel sesli workflow'larda hem
**Araç çağır → Mimik ve konuşma → Robotun sesi** seçiminde kullanılabilir:

| Model | Katalog kimliği | Ses | Lisans |
| --- | --- | --- | --- |
| [Wite Tech Pocket TTS Türkçe](https://huggingface.co/wite-tech/pocket-tts-turkish) | `pocket-tts-turkish` | 24 kHz, varsayılan `male_1` | CC-BY-4.0; Wite Tech ve temel Pocket TTS için Kyutai atfı |
| [Canberk Aslan EMA Lightning](https://huggingface.co/canberkkkkkk/ema-lightning) | `ema-lightning` | 48 kHz | Apache-2.0 |

Yayıncıların model kartları, ağırlıklarla birlikte `README.md` olarak saklanır.
Kaynak sürümleri ve tüm dosyaların SHA-256 değerleri
`tools/local_voice/tts-assets.lock.json` içinde sabittir. Kurulum yalnız model
verilerini indirir; Hugging Face deposundan Python kodu çalıştırmaz.

## Kurulum

ROS, OpenCV ve MediaPipe ortamı NumPy 1.x kullanır. Pocket TTS NumPy 2 gerektirdiği
için PyTorch ve iki yeni modelin paketleri ayrı `.venv-tts` ortamına kurulur:

```bash
python3 -m venv .venv-tts
PIP_CONFIG_FILE=/dev/null .venv-tts/bin/python -m pip install --upgrade pip
PIP_CONFIG_FILE=/dev/null .venv-tts/bin/python -m pip install -r requirements-tts.txt
sudo install -d -o "$(id -un)" -g "$(id -gn)" \
  /usr/local/ai.models/pocket-tts-turkish /usr/local/ai.models/ema-lightning
python3 tools/local_voice/prepare_tts.py
```

ARM64 için hazır `normalizer-tr` wheel'i bulunmadığında kurulum Rust derleyicisi
kullanır. Bu bağımlılık EMA'nın Türkçe metin normalleştirmesi içindir.

`prepare_tts.py --model ema-lightning` veya `--model pocket-tts-turkish` yalnız
bir modeli kurar. Farklı model kökü için `--root`, farklı izole Python ortamı için
`--runtime-python` kullanılabilir. Robot kataloğu `KUFIBOT_AI_MODEL_ROOT` altındaki
model manifestlerini otomatik bulur; mevcut özel katalog kayıtları korunur.

Ardından ROS ortamında `colcon build --packages-select kufibot_interaction`
çalıştırın. Ses ajanı ve web köprüsü yeni katalog kodunu yüklemek için yeniden
başlatılmalıdır. Tarayıcıdaki ses listesini **Yenile** düğmesiyle yenileyin.

## Çalışma biçimi

Ana ses akışı Piper ile aynı PCM parçası arayüzünü kullanır. Yeni modeller
`python -I` ile izole süreçte, CPU üzerinde çalışır; model yüklendikten sonra
EMA cümlenin tamamını beklemeden PCM parçaları gönderir. Pocket, Pi 5 CPU'sunda
gerçek zamanlı çalmaya yetişmediği için her cümleyi önce hazırlayıp sonra oynatır;
bu seçim daha uzun bekler fakat hoparlör akışı kesilmez. Model çağrıları yerel
dosyaları kullanır ve `HF_HUB_OFFLINE=1` ile internetten yükleme yapmaz.
Konuşma kesildiğinde yarım kalan model akışı kapatılır; sonraki konuşmaya eski
ses parçaları taşınmaz. Konuşma sürecinin sonlandırılması model sürecini de kapsar.

Piper varsayılanları ve kayıtlı ses seçimleri kurulum sırasında değiştirilmez.
Pocket için farklı hazır ses istenirse model manifestindeki `voice` alanı
`female_1`, `female_2`, `female_3`, `male_1`, `male_2` veya `male_3` olabilir.

1 Kasım 2026 Pi 5 denemesinde, tek TTS CPU thread'iyle iki Türkçe cümle için
EMA'nın ham ilk ses parçası 0,90–0,96 saniyede geldi; 1,52–2,16 saniyelik ses
1,38–1,74 saniyede üretildi. Pocket'ın 1,84–3,35 saniyelik sesi üretmesi
9,44–20,45 saniye sürdü. İlk model yüklemeleri ayrıca EMA için 2,48, Pocket
için 9,72 saniyeydi. Bunlar GPU yayıncı ölçümleri değil, bu robotun CPU
sonuçlarıdır; aktif diğer işler süreleri değiştirebilir.
