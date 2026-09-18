# Çift yönlü ses ve konuşmaya kamera ekleme

Robot, PipeWire WebRTC yankı gidericisinin temizlenmiş girişinden sürekli ses gönderir.
Robot konuşurken yeterince güçlü kullanıcı sesiyle söz kesilebilir; düşük
seviyeli sesler oynatma sırasında ve yankı kuyruğunda bastırılır.
Web ve mobil **Sesli Ajan Ayarları** içindeki
**Konuşmalarıma kamera görüntüsü ekle** seçeneği varsayılan olarak kapalıdır.
Kaydedilen tercih robotta `~/.config/kufibot/ai.json` dosyasında tutulur.

## Ses kurulumu

```bash
python3 tools/setup_voice_aec.py --restart
```

Kurulum MI BT 18I için `buffer.play_delay = 180/1000` uygular. Bu, hoparlörü
180 ms daha geç çalmak için değil, yankı gidericinin referansını Bluetooth'tan
mikrofona ulaşan sesle hizalamak içindir. Başka bir ses aygıtında yeniden
ölçülmelidir; kablolu aygıt için başlangıç değeri
`python3 tools/setup_voice_aec.py --play-delay-ms 0 --restart` olabilir.
Eski aracın ürettiği yapılandırma otomatik yükseltilir; elle özelleştirilmiş
dosyalar korunur. WebRTC yüksek geçiren filtre ve gürültü bastırma açık,
otomatik kazanç kapalı tutulur. Bunlar motorun mevcut varsayılanlarıdır;
bu düzeltmede yankıyı iyileştiren değişiklik referans gecikmesidir.

Robot profili ayrıca AEC sonrasında SpeexDSP uyarlamalı gürültü filtresini
kullanır. Sistem bağımlılığı `sudo apt install libspeexdsp1` ile kurulur.
`mic_noise_suppression_db: -25.0` gürültü bastırmanın üst sınırıdır;
ölçülmüş veya garanti edilmiş 25 dB azalma anlamına gelmez. `0.0` filtreyi
kapatır. Daha negatif değerler konuşmayı da bozabileceğinden dinleyerek
ayarlanmalıdır. Kütüphane yoksa filtre açık oturum açık hata verir.
Filtre 10 ms çerçeveler üzerinde çalışır; robot konuşurken mikrofonu susturmaz.
Sabit uğultuyu azaltır; ani servo tıkırtıları ve mekanik titreşimlerin tamamen
giderilmesi garanti edilmez. Mikrofonu servo/gövde titreşiminden fiziksel olarak
ayırmak da gerekebilir. Bu ek filtre uzak ses oturumunda kullanılır; yerel
Vosk işçisinin işleme yolu değişmemiştir.

### 11 Eylül 2026 robot üzerinde akustik ölçüm

Yerel Piper ile aynı Türkçe test cümlesi üretildi. Ham kamera mikrofonu ve
AEC çıkışı eşzamanlı 48 kHz mono kaydedildi; konuşma bölümleri referansla
çapraz korelasyon üzerinden hizalandı. RMS değerleri PCM16 birimindedir.
Kayıtlar `/tmp/kufibot-aec-{raw,clean}.wav`, test cümlesi
`/tmp/kufibot-aec-probe.wav` altında yereldir; son koşu önceki kayıtları değiştirir.

| Koşul | Ham RMS | AEC RMS | Seviye azalması |
| --- | ---: | ---: | ---: |
| İlk ayar, mikrofon/hoparlör %100, gecikme 0 ms | 12667 | 9783 | 2.2 dB |
| Mikrofon %60, hoparlör %65, gecikme 0 ms | 1718 | 1278 | 2.6 dB |
| Aynı seviyeler, referans gecikmesi 180 ms | 1723 | 127 | 22.7 dB |
| 180 ms, test sinyali iki kat genlikte | 3405 | 229 | 23.5 dB |

İlk kayıtta ham örneklerin yaklaşık %0.62'si kırpılıyordu. Son yüksek genlikli
testte ham tepe 24960, AEC tepe 11149 oldu; PCM16 taşması görülmedi.
Bu değerler yalnız robotun test cümlesi için toplam seviye azalmasıdır,
çift konuşma başarısı veya her ortam için yankı giderme garantisi değildir.
SpeexDSP bu karşılaştırmaya dahil değildir; tablo PipeWire AEC çıktısını ölçer.
Gerçek servo hareketi sırasında kullanıcı konuşması ayrıca dinlenmelidir.

Robotta aşağıdaki seviyeler uygulandı; WirePlumber aygıt seviyelerini saklar:

```bash
pactl set-source-volume alsa_input.usb-Generic_HD_camera_20181212000000-02.mono-fallback 60%
pactl set-sink-volume bluez_output.04_57_91_5A_8E_B7.1 65%
```

35 ilgili test geçti; etkileşim ve bringup paketleri yeniden derlendi.
Teknik dayanaklar: [PipeWire 1.2.7 AEC uygulaması](https://github.com/PipeWire/pipewire/blob/1.2.7/src/modules/module-echo-cancel.c),
[WebRTC motor seçenekleri](https://github.com/PipeWire/pipewire/blob/1.2.7/spa/plugins/aec/aec-webrtc.cpp),
[SpeexDSP işleme API'si](https://github.com/xiph/speexdsp/blob/master/include/speex/speex_preprocess.h).

Bu komut yalnızca `~/.config/pipewire/pipewire.conf.d/60-kufibot-aec.conf` dosyasını
oluşturur. Kamera mikrofonunu `kufibot_aec_source`, MI BT 18I hoparlörünü
`kufibot_aec_sink` üzerinden eşler. `--restart` kullanıcının ses servislerini
restart eder; çalışan ses akışları kesilir. Normal açılışta yapılandırma kalıcıdır.

`interactive_robot.yaml` bu iki aygıtı kullanır. `MIC_ALSA_DEVICE` veya
`SPEAKER_ALSA_DEVICE` ortam değişkenleri varsa YAML seçimini geçersiz kılabilir;
AEC kullanırken bu değişkenler de aynı sanal aygıtları göstermelidir.
İlk bağlantıda hoparlör veya kamera mikrofonu yoksa görünür hata verilir.
Devam eden görüşmede USB kamera/mikrofon ya da hoparlör kaybolursa mevcut
WebRTC oturumu kapatılmaz ve yeni görüşme açılmaz. Ses aygıtları saniyede bir
kontrol edilir; kamera kendi yeniden bağlanma akışını sürdürür. Gerekirse yalnızca
yerel kayıt/çalma süreçleri yeniden açılır; aynı ses track'i ve RTP zaman damgası
akışı korunur. Kesinti sırasında mikrofon sessizlik gönderir, kullanılamayan
hoparlöre ait ses kareleri atılır. Aygıtlar döndüğünde aynı konuşma devam eder;
kesinti sırasında duyulmayan ses tekrar oynatılmaz. Bu davranış USB kopmasının
fiziksel nedenini gidermez ve sunucunun ayrıca kapattığı oturumu geri getirmez.
Tekrar bağlandıktan sonra AI oturumu yeniden başlatılabilir.

```bash
pactl list short sources
pactl list short sinks
wpctl status
```

`wpctl status` içindeki AEC capture kamera mikrofonuna, AEC playback gerçek
Bluetooth hoparlöre bağlı olmalıdır. Sanal aygıtların tek başına görünmesi
akustik yolun çalıştığını kanıtlamaz. Bluetooth'a önceden teslim edilmiş ses
uygulama tarafından geri çekilemez; kesilme gecikmesi gerçek cihazla ölçülmelidir.

Geri alma:

```bash
python3 tools/setup_voice_aec.py --remove --restart
```

Ardından robot ses aygıtları önceki ALSA ayarlarına alınmalıdır. Kurulum aracı,
başka içerikle değiştirilmiş yapılandırmayı silmez.

## Kamera davranışı

- Açıkken konuşma başlangıcındaki güncel kamera karesi alınır; aynı turun ara
  transkriptleri ikinci bir fotoğraf üretmez.
- Kamera ayarı tek başına değiştiğinde ses oturumu yeniden başlamaz.
- Kapalıyken otomatik fotoğraf gönderilmez. Açık kamera talepleri ve navigasyon
  araçları mevcut şekilde çalışır. Yerel sağlayıcı görüntü desteklemediğinden
  kontrol yerel modda devre dışıdır.
- Görüntü yoksa, eskiyse, hız sınırına takılırsa veya yükleme başarısızsa sesli
  istek devam eder ve ayar ekranında bilgi gösterilir. Final konuşma sınırından
  itibaren bekleme en fazla iki saniyedir. Eski turun görüntüsü yenisine eklenmez.

Robotun doğrulanan sağlayıcısı Gemini Live (`gemini-3.1-flash-live-preview`).
Bu robot profili için Google'ın otomatik VAD'si kapatılır; mevcut yerel Silero VAD
konuşma başlangıç/bitişini yönetir. Ses, upstream Pipecat'ın pre-roll tamponuyla
`activity_start` penceresinde gönderilir. Fotoğraf aynı pencerede `video` olarak
iletilir, ardından `activity_end` gönderilir. Yeni konuşma önceki beklemeyi iptal
eder ve yanıtı keser. Kamera kapalıyken ek bekleme yapılmaz.

Standart STT/LLM/TTS iş akışlarında görüntü aynı kullanıcı mesajının içeriğine,
LLM context frame'i ilerlemeden önce eklenir. Diğer realtime sağlayıcılarda henüz
bu zamanlama desteği yoktur; açma isteği açık hata verir, yanıltıcı başarı bildirilmez.

Protokol: SDK 0.1.8 `on_voice_event`, `configure_camera_turns`,
`complete_camera_turn` ve `send_image(turn_id=...)` sağlar. Sunucu tur kimliğini
üretir. Yeni `rtf-bot-interrupted` olayı oynatıcı kuyruğunu temizler; eski
`rtf-bot-stopped-speaking` olayı korunur. Kullanıcı sesi yeni yanıt boyunca da
aktarılır. Kamera açık kullanım için eşleşen sunucu güncellemesi gereklidir.

Referanslar: [PipeWire AEC](https://pipewire.pages.freedesktop.org/pipewire/page_module_echo_cancel.html),
[Gemini activity sinyalleri](https://ai.google.dev/api/live).

## Sunucu değişiklikleri ve yayına geçiş

Sunucu çalışması `verasist-kufibot-voice` worktree'sinde,
`codex/kufibot-voice-camera` dalındadır. Değişiklik commit'i `ac02ee9`; başlangıç commit'i `3ec160d`.
Asıl SDK bu dalın `sdk/python` dizininde, robot kopyası `verasist-sdk` içindedir.
`ac02ee9`, `main` dalına fast-forward birleştirildi ve `origin/main` üzerine
pushlandı. Canlı `/root/verasist` checkout'u güncellendi; API image'ı yeniden
derlenip `docker compose up -d --no-deps --no-build api` ile yayına alındı.
Container `healthy`; hem localhost hem `https://app.verasist.ai/api/v1/health`
`status: ok` döndürüyor. Container içindeki Gemini/kamera dosyalarının SHA-256
değerleri main kaynaklarıyla eşleşiyor. Geri dönüş image etiketi:
`local/verasist-api:rollback-before-ac02ee9`.

Yayına geçerken önce sunucu dalı gözden geçirilip normal deployment süreciyle
API sürümü değiştirilmelidir. Sonra robot SDK 0.1.8 ve ROS paketleri kurulmalıdır:

```bash
.venv/bin/python -m pip install --no-deps ./verasist-sdk
source /opt/ros/jazzy/setup.bash
source .venv/bin/activate
colcon build --symlink-install --base-paths src --packages-select kufibot_interaction kufibot_remote kufibot_bringup
```

Sunucu için geri alma, önceki API image/commit'ine dönüş ve etkilenen oturumların
yeniden başlatılmasıdır; veritabanı migration'ı yoktur. Kamera seçeneği eski
sunucuda açık bırakılmamalıdır. Robot aygıt yapılandırmasının geri alması yukarıdadır.

## Doğrulama

Robot ses/kamera/ayar testleri ve Chromium arayüz testi başarılıdır. Mobil
`node node_modules/typescript/bin/tsc --noEmit` kontrolü başarılıdır.
Geniş robot test koşusunda 123 test geçti; `test_legacy_and_user_refresh`
(mimik kol açısı 65 yerine 40) başlangıç commit'inde de aynı şekilde başarısız.

Sunucuda ilgili 78 test ve robotta kurulu SDK üzerinde 25 test başarılıdır.
Sunucu testleri ayrı worktree'de `.env.test` kullanılarak çalıştırıldı; gerçek
LLM servisleri testlerde çağrılmadı. Gemini testleri görüntünün activity_end'den
önce gittiğini, söz kesmenin eski görüntüyü iptal ettiğini ve mevcut reconnect/
araç sonucu davranışının korunduğunu doğrular.

Akustik kabul hâlâ gerçek cihazda yapılmalıdır: 10 ardışık tur, yalnız robot
konuşurken yanlış transkript olmaması, 10 söz kesmenin en az 9'unda başarı ve
hedef bir saniyeden kısa duyulabilir durma; ayrıca Bluetooth kopup bağlanması.
İlk doğrulamada Bluetooth bağlantısı `br-connection-profile-unavailable` hatası
verdiğinden bu ölçümler tamamlanmış sayılmaz.

## Eski başlatma ortamıyla hoparlör düzeltmesi

`tools/_ros2_env.sh`, çalışma dizininde `.env` yoksa
`/home/kufi/workspace/kufibot.cpp/live_voice_session/.env` dosyasını yükler.
Robotta bu dosyadaki eski `MIC_ALSA_DEVICE=plughw:2,0` ve
`SPEAKER_ALSA_DEVICE=pulse` değerleri AEC YAML ayarlarını geçersiz kılıyordu.
Bu iki değer AEC source/sink olarak güncellendi; diğer ortam ayarları korundu.
Ham ALSA capture, PipeWire'ın aynı kamera mikrofonunu açmasını engelliyordu.

Ayrıca Bluetooth açılışında aşırı kısa kalan 500 ms yazma sınırı 5 saniyeye
çıkarıldı. Yazma beklemesi oynatıcı kilidinin dışında tutulur: söz kesme
Bluetooth'u beklemeden oynatıcıyı sonlandırabilir. `pulse` aygıt takma adı artık
ALSA eklentisi yerine doğrudan `pacat`/`parec` kullanır. Ses aygıtlarının etkin
adları oturum başlangıcında loglanır. Yeni ortam ve kod için çalışan launch
kapatılıp tekrar açılmalıdır.

Bağlı MI BT 18I üzerinde 4 saniyelik gerçek çıkış testi 201 çerçeveyi oynatma
hatası olmadan işledi; kullanıcı test tonunu duyduğunu doğruladı. Bu test akustik yankı giderme kabulünün yerine geçmez.

## Konuşma sonundaki yankı ve söz kesme koruması

`tools/2779.wav` 70.019 saniyelik, 16 kHz mono bir görüşme kaydıdır; kullanıcı
iki tarafın birlikte kaydedildiğini doğruladı. Yerel Vosk transkriptinde son
bölümde “ev içinde ... dolaşabilir” ifadeleri tekrarlanıyor. Bu tekrar tek başına
akustik yankıyı kanıtlamaz. Karışık kayıttan ayrı mikrofon/hoparlör sinyalleri
ve güvenilir değişken gecikme ölçümü elde edilemedi; kayıt bu nedenle saf
mikrofonmuş gibi filtre ayarı veya yankı başarı ölçümü için kullanılmadı.

Önceki oynatıcı son ses paketinden 350 ms sonra konuşma bayrağını kapatıyordu.
Bu bayrak artık mikrofon korumasının tek belirleyicisi değildir. Oynatıcı,
PCM sürelerinden yazılan sesin tahmini bitişini izler. Mikrofon koruması bu
bitişten sonra `mic_playback_echo_tail_sec: 1.2` kadar sürer. Uzayan Bluetooth
yazması boyunca koruma devam eder; yazma tamamlandığında süre uzatılır.
Söz kesme oynatıcıyı durdursa da hoparlöre teslim edilmiş ses için koruma korunur.
Bu bir donanım gecikme ölçümü değildir; Bluetooth'ta bunun da ötesindeki gecikme
veya çok güçlü kalan yankı hâlâ yanlış tetiklemeye yol açabilir.

Robot profilinin yeni seçenekleri:

| Parametre | Varsayılan robot ayarı | Etki |
| --- | ---: | --- |
| `mic_barge_in_rms` | 3000 | Robot konuşurken AEC ve gürültü filtresi sonrasında PCM16 RMS alt sınırı; 0 korumayı kapatır |
| `mic_barge_in_start_sec` | 0.18 | Pencerenin en az %75'inde eşik aşılmalı; son çerçeve de güçlü olmalı |
| `mic_playback_echo_tail_sec` | 1.2 | Kuyruktaki sesin tahmini bitişinden sonraki koruma süresi |

Koruma öncesinde ek bir enerji kapısı yoktu (`mic_noise_gate_rms: 0.0`). Yeni
eşik yalnız oynatma ve yankı kuyruğu sırasında uygulanır; bu pencerenin dışında
sessiz konuşma değiştirilmeden geçer. Kabul edilen söz kesmenin başını korumak
için sürekli yaklaşık 170 ms mikrofon tamponu tutulur. Bu, sunucunun kendi VAD
beklemesine ek gecikme getirir. Kabul edilen konuşmada 400 ms bırakma süresi kısa
heceler arası boşlukları korur. Reddedilen aralıklarda RTP zaman damgaları
atlamadan sıfır PCM gönderilir. `barge_suppressed` mikrofon logunda bastırılan
çerçeve sayısını gösterir.

Bu robot tarafındaki enerji korumasıdır; sunucunun VAD güven eşiği veya diğer
istemcilerin söz kesme davranışı değiştirilmedi. Yüksek sesli yankıyı kullanıcı
sesinden kesin olarak ayıramaz. Gerçek kullanımda söz kesmek zorlaşırsa RMS eşiği
azaltılmalı; önce kullanıcı ve robot kanalları ayrı kaydedilerek karşılaştırılmalıdır.
Yeni ayarlar için ses düğümü/launch yeniden başlatılmalıdır.

Doğrulama: 48 ilgili test geçti; etkileşim ve bringup paketleri yeniden derlendi.
Testler gecikmiş yankının konuşma bayrağı kapandıktan sonra bastırılmasını,
kuyruktaki PCM süresini, Bluetooth yazması uzarken korumayı, söz kesmenin ilk
hecesinin korunmasını, tek tıkırtının reddedilmesini ve RTP sürekliliğini kapsar.
Bu koşunun ilk gerçek cihaz kontrolünde Bluetooth hoparlör bağlantısı kapalıydı;
yeni eşik için fiziksel çift konuşma kabul testi tamamlanmış sayılmaz.
