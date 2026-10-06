# Kufibot web kumandası

Web uygulaması ROS ile aynı cihazda `remote_controller` düğümü tarafından sunulur.
Bilgisayar, Android telefon veya tablet tarayıcısından şu adres açılır:

```text
http://ROBOT_IP:8080/
```

Örneğin robotun IP adresi `192.168.1.20` ise `http://192.168.1.20:8080/`.
Robotun üzerinde `hostname -I` ile IP adresi görülebilir. Tarayıcı robotun
TCP portuna erişebilmelidir. Node.js, npm, Expo, CDN veya ayrı web sunucusu
**robot üzerinde gerekli değildir**; web dosyaları ROS paketinin içindedir.

## Kurulum ve başlatma

Proje kökünde, mevcut ROS 2 Jazzy kurulumu ve Python sanal ortamıyla:

```bash
source /opt/ros/jazzy/setup.bash
source .venv/bin/activate
python -m pip install 'aiohttp>=3.9,<4'
colcon build --symlink-install --packages-select kufibot_interfaces kufibot_interaction kufibot_remote kufibot_bringup
source install/setup.bash
./tools/ros2_launch.sh kufibot_bringup interactive_robot.launch.py remote:=true motors:=true
```

Robot yığını zaten çalışıyorsa ikinci kez başlatmayın. Kamera, sensörler ve güncel
`servo_arbiter` çalışırken yalnızca web/mobil köprüsünü başlatmak için:

```bash
source install/setup.bash
.venv/bin/python -c 'from kufibot_remote.node import main; main()'
```

Köprü zaten çalışıyorsa güncellemeden sonra mevcut `remote_controller` sürecini
veya onu başlatan launch oturumunu yeniden başlatın. `8080` portunda yalnızca bir
köprü çalışmalıdır. Web ve Expo uygulaması **aynı köprüyü** kullanır.

Yalnızca kamera/kafa için `motors:=false` kullanın. Motor düğümü yoksa soldaki
hareket joysticki pasif olur. Port, robot adı ve video kalitesi
`src/kufibot_bringup/config/interactive_robot.yaml` içindeki
`remote_controller.ros__parameters` bölümünden değiştirilebilir. Web adresindeki
portu da buna göre değiştirin; tarayıcı WebSocket adresini otomatik olarak mevcut
sayfanın IP ve portundan alır.

Web kumandası görüntüyü LAN üzerinde WebRTC ile alır; medya RTP/RTCP üzerinden
akar, kontrol ve signaling ise mevcut HTTP/WebSocket uçlarında kalır. Bu, JPEG
kuyruğu ve base64 dönüştürmesinden kaynaklanan gecikmeyi kaldırır. WebRTC için
robotun Python ortamında `aiortc==1.9.0` bulunmalıdır (projenin
`requirements.txt` dosyasında zaten vardır).

Web ve native Expo uygulaması yalnızca WebRTC kullanır; JPEG/base64 yayın ve
`/video` ucu kaldırıldı. Eski APK güncellenmelidir.
Kamera, kaynak çözünürlüğünde `camera/stream` konusunu 30 FPS'te yayınlar.
WebRTC köprüsü bu konuyu doğrudan iletir; MediaPipe, OpenCV ve ses ajanı aynı
akıştan kendi ayarlanmış aralıklarında en yeni kareyi örnekler. Böylece ağır
işleme canlı video aktarımını durdurmaz veya geriye düşürmez. `video_max_width`
yalnızca WebRTC yayın kopyasını etkiler; `0` kaynak genişliğini korur. Robot
gerçek zamanda encode edemiyorsa `video_max_width` veya `video_fps` değeri
düşürülebilir.
MediaPipe yüz/el çıkarımı ve takip komutları yalnızca güncel
`remote/applied_mode=ai` bildirimi varken çalışır. Kumandaya dönüşte eski hedef
temizlenir. Model, landmark dönüşümü ve takip eşikleri korunur.
Bağımsız tracking test launch'u bu nedenle arbiter'i YZ modunda başlatır.

## Ekran ve kontroller

Mobil uygulamadaki gibi tam ekran kamera, üst köşelerde akım/gerilim/pusula/mesafe,
sol altta hareket, sağ altta kafa joysticki, kol sürgüleri ve göz anahtarları vardır.
KUMANDA ve YZ MODU üst ortadan seçilir. Menüde bağlantı adresi, yenileme ve yardım
bulunur. Ekran masaüstü, yatay telefon ve dikey telefon boyutlarına uyarlanır.

| Girdi | İşlev |
| --- | --- |
| Fare veya dokunmatik joystick | Hareket / kafa; iki parmakla iki joystick aynı anda kullanılabilir |
| W A S D | İleri / sol / geri / sağ |
| Yön tuşları | Kafayı çevir |
| Boşluk / DUR | Kumanda joystick girdilerini sıfırla |
| Kol sürgüleri | Sürgü bırakıldığında ilgili ekleme hedef gönder |
| Tam ekran | Tarayıcının desteklediği cihazlarda tam ekran görünümü |

Kumanda modunda YZ servo hareketleri engellenir; sesli ajan oturumu ve otomatik
YZ davranışları çalışmaz. YZ modu seçildiğinde bunlar robotun mikrofonunu
kullanarak başlar; tekrar Kumanda moduna dönülünce durur. DUR, YZ servo
hareketlerini iptal eden fiziksel acil durdurma değildir.

İlk bağlanan tarayıcı veya telefon kontrol sahibidir; diğerleri izleyici olur.
Sahip ayrılınca **Kumandayı devral** düğmesiyle kontrol alınır. Sekme gizlenince veya
kapanınca soketler kapatılır ve kontrol bırakılır. Pencere odağı kaybolunca,
menü açılınca, joystick bırakılınca ve bağlantı kopunca kumanda girdileri sıfırlanır.
Geri dönüldüğünde yeniden bağlantı kurulur; eski tuş veya joystick girdileri yürütülmez.

500 ms hareket komutu kesintisi ve iki saniye heartbeat kesintisi robot köprüsünde
ayrıca denetlenir. Köprü kaybolursa motor düğümünün kendi watchdog'u durdurmayı
sağlar; arbiter kendiliğinden YZ moduna geçmez. Üç saniyeden eski sensörler `—`,
ikiden fazla saniyedir yenilenmeyen kamera bekleme ekranı olarak gösterilir.

## Sunulan uçlar

| Adres | İşlev |
| --- | --- |
| `GET /` | Web kumandası |
| `GET /assets/app.js`, `connection.js`, `style.css` | Paketle birlikte gelen yerel web dosyaları |
| `WS /control` | Mevcut Expo protokolü: sahiplik, mod, joystick, eklem komutları ve sensör durumu |
| `POST /offer` | Web kumandasının WebRTC SDP signaling ucu |
| `UDP 8888` | Expo Android otomatik keşfi; web tarayıcısında gerekli değildir |

WebSocket bağlantıları tarayıcıda sayfanın kendi adresinden açılır. Farklı bir
web sitesinden gelen `Origin` reddedilir; yerel mobil istemciler desteklenir.
Bu sürüm güvenilen yerel ağ içindir; parola/eşleştirme, TLS ve internet erişimi
sağlamaz. Portu internete yönlendirmeyin.

## Testler

```bash
source install/setup.bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest src/kufibot_remote/test -q
```

HTTP varlıkları, geçersiz dosya yolları, WebSocket Origin kontrolü, komutlar,
bağlantı kopması, kamera kodlama ve hareket sınırları test edilir.

İsteğe bağlı gerçek tarayıcı testi için geliştirme ortamında Playwright ve sistem
Chromium kurulmalıdır (`.venv/bin/python -m pip install playwright`). Test gerçek
robotu kullanmaz; localhost üzerinde test kamera görüntüsü ve sensörler üretir.
Kamera, fare/klavye kontrolü, modlar, kol/göz komutları, odak/sekme kaybı, kontrol
sahipliği, yeniden bağlanma ve telefon ekran boyutları Chromium ile doğrulanır.
Playwright veya Chromium yoksa yalnızca bu tarayıcı testi atlanır.

### Web arayüzünü uygulama olarak yükleme

Web menüsündeki **Uygulamayı yükle** düğmesi, destekleyen tarayıcılarda
kurulum penceresini açar. iPhone/iPad üzerinde Safari → Paylaş → Ana Ekrana
Ekle yolunu kullanın. Uygulama tam ekran açılmayı ister; bunu desteklemeyen
platformlar bağımsız uygulama penceresine döner. Başlık/tema ve açılış
arka planı `#111319` rengindedir; sistem çubuklarının son görünümünü işletim
sistemi belirler.

PWA kurulumu ve service worker için güvenilir HTTPS gerekir (`localhost`
geliştirme istisnasıdır). `http://<robot-ip>:<port>` üzerinden uzaktan
doğrudan PWA kurulumu desteklenmez; aşağıdaki yerel HTTPS kurulumu kullanılmalıdır.
Uygulama kabuğu çevrimdışı açılabilir; robot kontrolü için bağlantı gerekir.

### İnternetsiz, yerel ağdan PWA kurulumu

Robotun proje dizininde çalıştırın (Python 3 ve sistemde `openssl` gerekir):

```bash
python3 tools/setup_local_https.py
```

Komut robotun adını ve mevcut IP adreslerini sertifikaya ekler. İsterseniz
adresleri açıkça verin: `python3 tools/setup_local_https.py 192.168.1.20 robot.local`.
`robot.local` yalnızca ağınızda mDNS çözümlemesi varsa çalışır; IP adresi de kullanılabilir.
Dosyalar `~/.config/kufibot/https/` altında tutulur. Her robot kendi CA anahtarını
oluşturur; bu dosyalar repoya eklenmez. Komut sistemin sertifika güven deposunu değiştirmez.

Çalışan launch sürecini durdurup `./tools/ros2_launch.sh` ile yeniden başlatın.
HTTP ve mobil keşif `8080` üzerinde çalışmaya devam eder; HTTPS ayrıca `8443`
üzerinde açılır. ROS parametreleri `https_port` (0: kapalı) ve
`https_directory` ile değiştirilebilir. Sertifika yoksa HTTPS açılmaz.

Telefon/bilgisayarda `http://ROBOT_IP:8080/pwa-setup` adresini açın.
Sayfa Android, iOS, Windows, macOS ve Linux için sertifika yükleme adımlarını
ve `https://ROBOT_IP:8443/` bağlantısını gösterir. Her cihazda kök sertifikaya
bir kez güven verdikten sonra HTTPS arayüzündeki **Uygulamayı yükle** düğmesini
kullanın. iOS'ta profil yüklemeye ek olarak Sertifika Güven Ayarları altında
tam güven verilmelidir. Sertifika uyarısını geçmek tek başına yeterli değildir.

Sadece `root-ca.crt` paylaşılır; `root-ca.key` ve `server.key` robotta kalır.
Kurulum komutunun gösterdiği SHA-256 parmak izi sertifika ayrıntılarıyla
karşılaştırılabilir. PWA için internet, dış DNS veya reverse proxy gerekmez.
Robotun IP adresi değiştiğinde veya bir yıllık sunucu sertifikası dolmadan
aynı komutu tekrar çalıştırıp launch sürecini yeniden başlatın. Mevcut CA
korunduğu için istemci cihazlarda yeniden sertifika kurulumu gerekmez.
Birden fazla robot/simülatör aynı makinedeyse farklı HTTPS portları kullanın
veya simülatör için `https_port:=0` ayarlayın.

### Kalibrasyon açı kapsamı ve kayıt

Web ve mobil Kalibrasyon sayfası 10° genişliğinde 36 dilimi, her dilimde
üç ölçüm göstergesiyle gösterir. Her dilimde en az üç geçerli sensör okuması
ve `calibration_samples` toplam hedefi (varsayılan 500) sağlanmadan kalibrasyon
kaydedilmez. Üç ölçüm ayrı okumadır; üç ayrı tur zorunlu değildir.
Aday merkez/ölçek değiştikçe oturum ölçümlerinin açı dağılımı yeniden hesaplanır;
ilerleme bu sırada değişebilir. Bu kapsam göstergesi bağımsız bir yön doğruluğu
ölçümü değildir; robot yatay, metal ve mıknatıslardan uzakta çevrilmelidir.

Son başarılı sonuç sensörün `calibration_file` dosyasında
(varsayılan `~/.ros/kufibot_hmc5883l_calibration.json`) katsayılarla birlikte
saklanır: UTC kayıt tarihi, toplam ölçüm sayısı, açı dilimi sayaçları,
son açı/ham ölçüm, minimum/maksimumlar ve ofset/ölçek değerleri.
Kalibrasyon sayfasındaki **Son kaydedilen kalibrasyon** bölümü yeniden
bağlanınca veya robot yeniden başlayınca da bu kaydı gösterir. Yeni bir
kalibrasyon önceki başarılı kaydı tamamlanıp diske yazılana kadar değiştirmez.
Son başarılı kayıt tutulur; tüm geçmiş oturumların arşivi tutulmaz.
Eski katsayı dosyaları desteklenir, ancak geçmişte saklanmamış tarih ve açı
kapsamı üretilemez; sayfa bu durumda yeni kalibrasyon gerektiğini belirtir.

### Verasist SDK araçlarını yayımlama

Web ve mobilde **Verasist Ses → SDK araçları** bölümüne hedef **Workflow UUID**
(görüşmenin trigger UUID’sinden farklıdır) girip **Mevcut araçları yayımla**
düğmesine basın. Kumanda sahipliği gerekir. Ses ajanı çalışıyor olmalıdır;
aktif görüşme gerekmez. Ses ajanının ortamında workflow organizasyonuna yetkili
`VERASIST_API_KEY` veya `VERASIST_API_TOKEN` tanımlı olmalıdır.

Yayın, robotun sensör/kamera/eklem ve navigasyon araçlarının şemalarını gönderir;
hedef workflow’un SDK araç kataloğunun tamamını değiştirir. Aktif görüşmeyi veya
navigasyonu yeniden başlatmaz. Başarıda yayımlanan araç sayısı gösterilir.
Verasist workflow sayfasını yenileyin; yayımlanmış araçlar varsa **SDK araçları**
bölümü görünür. Araçları ilgili node’larda seçin. Bağlantı zaman aşımında tekrar yayımlamadan
önce kataloğu kontrol edin; istek sunucuda tamamlanmış olabilir.
