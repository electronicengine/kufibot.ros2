# WSL üzerinde Verasist ile sesli robot simülasyonu

Simülasyon, bilgisayarın mikrofon/hoparlörünü Verasist WebRTC oturumuna bağlar.
YZ; simülasyon kamerasını, mesafe ve yön verilerini okuyabilir, eklemleri ve
navigasyon araçlarıyla sanal robotu kontrol edebilir. Verasist servisine ağ
bağlantısı gerekir; bu mod çevrimdışı yerel model çalıştırmaz.

## Hazırlık

ROS 2 Jazzy ve proje bağımlılıkları kurulu olmalıdır. Proje kökündeki
`verasist-sdk` başlatıcı tarafından Python yoluna eklenir.

```bash
sudo apt-get install pulseaudio-utils alsa-utils
./tools/ros2_build.sh
```

Proje kökündeki `.env` dosyasına kendi değerlerinizi yazın; mevcut dosyayı
üzerine kopyalamayın:

```dotenv
VERASIST_API_URL=https://VERASIST-SUNUCUNUZ
VERASIST_API_KEY=API-ANAHTARINIZ
VERASIST_TRIGGER_UUID=IS-AKISINIZIN-TRIGGER-UUID-DEGERI
```

Önceki `VERASIST_API_ENDPOINT` ve `VERASIST_API_TOKEN` adları da desteklenir;
birlikte tanımlanırsa önceki adlar önceliklidir. UUID verilmezse simülasyon
YAML dosyasındaki değer kullanılır. Kendi Verasist iş akışınızın UUID'sini girin.

## Çalıştırma

```bash
./tools/ros2_sim_launch.sh sim
```

1. Windows tarayıcısından `http://localhost:8080` adresini açın ve kumandaya bağlanın.
2. Sesli Ajan Ayarları'nda **Verasist AI** seçip uygulayın.
3. **YZ modu** seçin. Bu seçim mikrofonu açıp Verasist oturumunu başlatır.
   Bağlantı kurulduktan sonra hareket görevleri için **Serbest gezinme** düğmesini açın.
4. Bilgisayar mikrofonuna konuşun: “Merhaba”, “Önündeki engel ne kadar uzakta?”,
   “Çevrene bak, güvenliyse biraz ileri git.” Hareketi simülasyon penceresinde izleyin.
5. **Kumanda modu** seçildiğinde ses oturumu kapanır; navigasyon bağlantısı kesilir.

İş akışındaki asistan robot görevlerinde sunulan cihaz araçlarını kullanmalıdır.
Araçlar görüşme başında SDK ile kaydedilir. Sesli yanıt tek başına bir hareket
komutu değildir; modelin navigasyon araçlarını çağırması gerekir.

## WSL sesi

`PULSE_SERVER` varsa simülasyon `pulse:default` kullanır; `parec` mikrofonu,
`pacat` hoparlörü WSLg üzerinden bağlar. Ses tarayıcıdan değil WSL'den alınır.
Windows mikrofon gizlilik izinlerini ve varsayılan giriş/çıkış aygıtını kontrol edin.
Kulaklık kullanabilirsiniz; varsayılan olarak asistan konuşurken mikrofon susturulur.

```bash
pactl info
pactl list short sources
pactl list short sinks
./tools/ros2_sim_launch.sh sim audio_device:=pulse:default
```

Giriş/çıkış ayrı seçilecekse `.env` içine `MIC_ALSA_DEVICE=pulse:KAYNAK_ADI`
ve `SPEAKER_ALSA_DEVICE=pulse:CIKIS_ADI` yazın. Bu ortam değişkenlerinin isimleri
eski sürümlerle uyumluluk için korunmuştur; `pulse:` öneki de kabul edilir.
ALSA için `audio_device:=default` veya uygun aygıt adı kullanılabilir.
Ses olmadan çalıştırmak için `sim voice:=false` kullanın.

Oturum hataları web arayüzündeki ses durumunda ve başlatma terminalinde görünür.
`Microphone capture ended` giriş aygıtını; HTTP yetkilendirme hataları API
anahtarı/iş akışı erişimini; ICE bağlantı hataları ağ/WebRTC erişimini kontrol
etmeniz gerektiğini gösterir. Sanal kamera verisi Verasist'e gönderilir.

## Gerçek Raspberry Pi ile ortak kontrol

İki başlatıcı da aynı `voice_agent_node`, `NavigationTools`, `navigation_node`,
`servo_arbiter` ve uzaktan kumanda düğümünü çalıştırır. Verasist araçları ve ROS
komut arayüzleri ortaktır. Simülasyon gerçek `DcMotorNode` ve `ServoNode` kodunu
kullanır; I2C/PWM sürücüsünün yerini sanal sürücü alır. Kamera ve mesafe/yön
ölçümleri sanal dünyadan üretilir.

RPi için `./tools/ros2_launch.sh`, bilgisayar için
`./tools/ros2_sim_launch.sh sim` kullanılır. WSLg otomatik ses seçimi yalnızca
simülasyon başlatıcısındadır. RPi mikrofonu `plughw:2,0`, hoparlörü `default`
ALSA aygıtlarını kullanmayı sürdürür; PulseAudio kurulumu gerektirmez.

Ortak başlatıcı proje kökündeki `.env` dosyasını öncelikli kullanır. Dosya yoksa
eski RPi konumu `/home/kufi/workspace/kufibot.cpp/live_voice_session/.env`
kullanılır. SDK kaynağında da proje içindeki dizin yoksa eski RPi dizinine dönülür.
`VERASIST_ENV_FILE` ve `VERASIST_SDK_SRC` açıkça ayarlanırsa bu yollar önceliklidir.
WSL'ye özel ses aygıtlarını içeren `.env` dosyasını RPi'ye taşımayın.

Kontrol yazılımının ortak olması fiziksel davranışın birebir aynı olduğu
anlamına gelmez: gerçek motor/sensör kalibrasyonu ve gecikmeleri farklıdır.
Gerçek robot yapılandırmasındaki `calibrated: false` ve
`navigation_calibrated: false` kilitleri korunur; kalibrasyon tamamlanmadan
YZ navigasyon hareketi kabul edilmez. Simülasyonun hız/kalibrasyon değerlerini
RPi yapılandırmasına kopyalamayın. Sanal ortam şu anda gerçek yüz/el algılamasını,
batarya ölçümünü ve donanıma özel ifade kataloğunu bütünüyle taklit etmez.


## Navigasyon iptal edilirse

`goto`, `look_at` veya `read_sensor_values` ilk çağrıda gerekli iç görevi
kendiliğinden oluşturur; model görev veya istek kimliği vermez. Tarama iptal
olursa sonuç `ok` değil `cancelled`/`error` olur. Yetki kaybında `authority_reason` alanı
`stop_requested`, `owner_heartbeat_timeout`, `navigation_state_stale` gibi
nedeni gösterir; aktif görevin yetki değişimi terminale de yazılır.
Asistan geçersiz görevle devam etmemeli veya `obs_001` gibi gözlem kimlikleri
uydurmamalıdır. Bağlantı düzeldikten sonra serbest gezinmeyi yeniden açın ve
yeniden `read_sensor_values`, `goto` veya `look_at` çağırın.

YZ modunda görünür kumanda penceresinin odağını kaybetmesi veya boyutunun
değişmesi tek başına navigasyonu iptal etmez. Kumanda modunda bu olaylar
manuel hareketi durdurur. DUR düğmesi, kumandanın kapanması/gizlenmesi,
bağlantı veya heartbeat kaybı navigasyonu durdurmaya devam eder.

Görüntü aktarımında `Rate limited` yanıtı alınırsa kamera ve navigasyon aynı
sıralı gönderim kuyruğunu kullanır (en az iki saniye ara, sınırlı artan beklemeli
tekrar). Bu açık sunucu reddi tek başına serbest gezinmeyi kapatmaz. Birleşik tarama
görüntüsü kabul edilmeden gözlem hareket için onaylanmaz. Tekrarlar da
reddedilirse araç `image_rate_limited` ile iç görev/gözlem kimliklerini
korur; asistan yeniden `read_sensor_values` çağırarak güncel aktarımı
tamamlayabilir. Başarılı iletilmiş görüntüler tekrar gönderilmez.
Belirsiz ROS zaman aşımı, gerçek bağlantı kaybı ve DUR komutu korumaları sürer.


## Karar başına tek birleşik görüntü

Navigasyon, 180° kafa taramasıyla mesafeleri toplar; tarama bitince kamerayı
öne çevirip güncel tek kare alır. Kareye yarı saydam, tepeden görünüşlü bir
mesafe haritası işlenir. Harita 8 metreye kadar ölçümleri, 2/4/6/8 metre ölçek halkalarıyla gösterir. Haritada yukarı robotun önü, sağ robotun sağıdır;
pusula açısı ayrıca yazılır. Kırmızı noktalar ölçülen engellerdir; sarı halkalar
haritanın 800 cm görüntüleme sınırının ötesindeki ölçümlerdir. Ölçülmeyen
boşluklar serbest kabul edilmez. Bu bir metrik haritadır, kamera piksellerine
hizalanmış bir derinlik görüntüsü değildir.

LLM'ye bir JPEG ve kısa ölçüm açıklaması gönderilir: ön sensör mesafesi,
sensörün gövdeye göre konumundan hesaplanan tahmini gövde boşluğu, gerekli
durma payı ve aynı navigasyon denetiminin kabul ettiği en büyük sınırlı ileri
adım. Tek ışındaki gövde boşluğu “bu kadar ilerlersen kesin çarparsın” anlamına
gelmez; robot genişliği, ölçülmeyen alanlar ve hareketli engeller önemlidir.
Ölçümler tarama boyunca sırayla alınır; tarama süresi ve görüntü yönü de
aktarılır. RPi kalibrasyon kilidi ve canlı sensör denetimleri korunur.

Varsayılan olarak her konuşma turunda fotoğraf gönderilmez. Hareket sonrası
yeniden planlama veya açık `read_sensor_values` isteği bir
birleşik görüntü üretir. Navigasyon dışında görsel soru için `analyze_camera`
kullanılır. Aynı gözlem tekrar istendiğinde başarıyla gönderilmiş JPEG yeniden
gönderilmez. Eski davranışı özellikle isteyen kurulumlar
`camera_attach_to_every_user_turn: true` ayarını açabilir.
