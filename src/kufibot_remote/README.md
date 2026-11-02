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
Karşılama, sesli oturum bağlantısı, görüşme ve veda boyunca web/mobil video bağlantıları
sunucudan kapatılır ve yeni `/offer` isteklerine `503` döner. `videoPaused`
telemetrisi yeniden denemeleri durdurur; sesli oturum bitince görüntü otomatik
açılır. Kamera kaynağı, yüz/el takibi ve kamera analizi çalışmaya devam eder;
köprünün araç/workflow kare önbelleği bu sırada 5 Hz'de güncellenir.
Kamera, kaynak çözünürlüğünde `camera/stream` konusunu 30 FPS'te yayınlar.
WebRTC köprüsü bu konuyu doğrudan iletir; MediaPipe, OpenCV ve ses ajanı aynı
akıştan kendi ayarlanmış aralıklarında en yeni kareyi örnekler. Böylece ağır
işleme kare kuyruğu oluşturmaz. `video_max_width`
yalnızca WebRTC yayın kopyasını etkiler; `0` kaynak genişliğini korur. Robot
gerçek zamanda encode edemiyorsa `video_max_width` veya `video_fps` değeri
düşürülebilir.
MediaPipe yüz/el çıkarımı ve takip komutları yalnızca güncel
`remote/applied_mode=ai` bildirimi varken çalışır. Kumandaya dönüşte eski hedef
temizlenir. Model, landmark dönüşümü ve takip eşikleri korunur.
Bağımsız tracking test launch'u bu nedenle arbiter'i YZ modunda başlatır.

## Ekran ve kontroller

Telefonda kamera, **Kumanda / Sesli ajan / Araçlar** mod seçimi, hareket ve kafa
joystickleri ile yatay hız ayarı ana ekrandadır. **Durdur** alt kenardan erişilir.
**Detaylar** alttan açılan panelde kol sürgülerini, göz anahtarlarını, sensörleri,
haritayı ve yön kontrollerini gösterir; panel açılırken manuel hareket sıfırlanır.
Panelde de Durdur düğmesi vardır. Sesli ajan modunda joysticklerin yerine görüşme
durumu, son algılanan metin ve görüşmeyi bitirme düğmesi görünür. Serbest gezinme
anahtarı Detaylar içindedir. Yatay telefonda kamera iki joystickin arasında yer
alır. Masaüstünde ayrıntılı kontrol yerleşimi kullanılır. Ekran boyutu değişirken
aynı kontroller taşınır; ek WebSocket veya yeni bir kumanda oturumu açılmaz.

**Araçlar → Mimik ve konuşma** ekranında kitaplıktan bir mimik seçip robota
söyleteceğiniz metni yazabilirsiniz. **Söyle ve oynat**, mimiği ilk sesle birlikte
başlatır; **Yalnızca mimik** metni okumadan hareketi oynatır. **Mimik olmadan konuş**
seçimiyle yalnız ses gönderebilirsiniz. **Durdur**, sesi ve mimiği birlikte keser.
Konuşma robotta kurulu yerel ses modeliyle, robotun hoparlöründe üretilir;
AI oturumu veya mikrofon gerekmez. Bluetooth hoparlör bağlı değilse açık hata gösterilir.
Pencereyi kapatmak oynatmayı durdurmaz; DUR, mod değişikliği ve kontrol kaybı durdurur.
Önceki gezinme araçları aynı pencerenin **Hareket araçları** sekmesindedir.
Piper, Pocket TTS Türkçe ve EMA Lightning kurulumu için
[yerel TTS belgesine](../../docs/local-tts.md) bakın.

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
Tarayıcı, bağlantı açık görünse bile gerçekten çözülen video karelerini izler.
Beş saniye yeni kare gelmezse yalnız kamera bağlantısını kapatıp bir saniye sonra
yeniden kurar. Bağlantı veya video oynatma hatalarında da otomatik tekrar dener;
kontrol sahipliği, AI modu ve çalışan workflow korunur. Sayfayı yenilemek gerekmez.

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
`test_web_video_recovery.py`, başarısız ilk kamera bağlantısını, bağlantı açıkken
donan videoyu, oynatma hatasını ve kameranın kaybolup geri gelmesini ayrıca sınar.
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

### Robotun ses aygıtları ve Wi-Fi bağlantısı

**Bağlantı Ayarları** robot üzerindeki mikrofonları, hoparlörleri ve yakındaki
Wi-Fi ağlarını listeler. Değişiklikler kumanda sahipliği ve aynı kaynaklı bir
istek gerektirir. Ses seçimleri `~/.config/kufibot/devices.json` dosyasına
yalnızca kullanıcı tarafından okunabilir şekilde kaydedilir; yeni yerel/Verasist
görüşmelerinde, uyanma dinleyicisinde ve araç sekmesindeki konuşmada kullanılır.
`KUFIBOT_DEVICE_SETTINGS` ile dosya yolu değiştirilebilir. Bluetooth listesinde
yalnızca robotla önceden eşleştirilmiş ses çıkışları bulunur. Bağlı olmayan bir
hoparlör seçilip kaydedildiğinde bağlantı denenir. AEC kullanılıyorsa fiziksel
giriş/çıkış yönlendirmesi yeni oturumun başında mevcut DSP ayarları korunarak
güncellenir; gerekirse kullanıcının PipeWire servisleri yeniden başlatılır.

Wi-Fi için sistemde NetworkManager, `nmcli` ve `python3-dbus`; Bluetooth için
BlueZ gerekir. Ağ işlemleri önce servis kullanıcısının D-Bus yetkileriyle çalışır.
Yetki yetersizse mevcut `sudo -n` yetkisiyle izole sistem Python yardımcısı
denenir; paket yeni sudo/polkit izni oluşturmaz. Yetkisi olmayan kurulumlarda
yönetici NetworkManager erişimini ayrıca yapılandırmalıdır.

Wi-Fi parolası yalnızca HTTP gövdesi, yardımcı sürecin standart girdisi ve
NetworkManager D-Bus çağrısında taşınır; komut satırına, durum yanıtına veya web
depolamasına yazılmaz. Başarılı bağlantı NetworkManager tarafından kalıcı
kaydedilir ve otomatik bağlantıda öncelik kazanır. Ağ değişimi 100 saniyelik
NetworkManager checkpoint’i ile korunur; başarısızlıkta eski bağlantı geri
yüklenir, süreç kapanırsa zaman aşımında geri dönüş denenir. Kumanda WebSocket’i
koptuğunda bağlantı işi devam eder. Ağ değişirken sayfa kopabilir: istemciyi de
seçilen ağa bağlayıp `robot.local:8080` veya robotun yeni IP’sini açın.
İnternet erişimi gerekmez; yerel ağ bağlantısı ve IPv4 adresi başarı sayılır.
Yeni kurumsal/WEP ağ profilleri bu arayüzden oluşturulmaz; hazır profiller
kullanılabilir. Geri dönüş, ağın hâlâ erişilebilir olmasına bağlıdır.

### Bağlantısız açılış, kurulum hotspotu ve robotun yeni adresi

Robot üzerinde `python3-dbus`, NetworkManager, `dnsmasq` ve `avahi-daemon`
kurulu olmalıdır. Ağ kurtarma hizmetini **mevcut Wi-Fi bağlantısını kesmeden**
kurmak için proje kökünde:

```bash
sudo /usr/bin/python3 tools/install_network_service.py
```

Hizmet bağımsız olarak açılışta başlar. Robot Wi-Fi istasyonu olarak bağlıyken
ağa dokunmaz. Başlangıçta veya bağlantı kaybından sonra 45 saniye bağlantısız
kalırsa 2.4 GHz parolasız kurulum ağı açar. Varsayılan ağ adı `kufi` olur;
mevcut kurulumun adını değiştirmek için kurucuya `--ssid kufi` eklenir.
Eski parolalı kurulumu parolasız yapmak için
`sudo /usr/bin/python3 tools/install_network_service.py --open-hotspot` kullanılır.
Bu işlem kayıtlı hotspot profilinin güvenlik ayarını kaldırır ve yeni açılacak
hotspotları da parolasız yapılandırır. Bağlantı Ayarları ekranında **Parolasız ağ**
gösterilir. Robotun bağlanacağı ev/iş Wi-Fi ağının parolası kendi ağ seçimi
formunda girilmeye devam eder. Kurulum yapılandırması `/etc/kufibot/network.json`
dosyasında saklanır; mevcut Wi-Fi bağlantısı kesilmeden güncellenir.

Kurulum ağında robot `10.42.0.1/24` adresindedir. DHCP ve yalnızca kurulum
arayüzüne bağlı DNS sunucusu cihazları bu adrese yöneltir. HTTP ağ kontrol
istekleri `http://10.42.0.1/setup` adresine yönlendirilir; destekleyen telefonlar
“ağa giriş yap” ekranını açar. Otomatik açılmazsa bu adresi tarayıcıda açın.
Kurulum ağı internet sağlamaz; telefonda “bu ağa bağlı kal” gerekebilir.
HTTPS trafiği taklit edilmez ve sahte sertifika sunulmaz. Güvenilir TLS
sertifikası isteyen DHCP Option 114 API’si ilan edilmez; standart HTTP captive
portal tespiti kullanılır. Cihazın otomatik açılma davranışı garanti edilmez.

Bağlantı Ayarları’ndan hedef Wi-Fi’yi seçin. Sürücü hotspot açıkken taramayı
desteklemiyorsa **Ağ adını elle gir** kullanılabilir. Bağlantı isteği kabul
edilince istemciyi de hedef ağa geçirin. İstek boyunca ağ gözcüsü ve tarama
işlemleri aynı kilidi kullanır; gözcü devam eden bağlantıyı kesmez. Başarısız
istek checkpoint ile kurulum ağına/önceki ağa döner. Geri dönülecek bir ağ
bulunmazsa gözcü hotspotu yeniden açar. Hotspot kullanıcı ayar yaparken
kendiliğinden kapanıp başka bir kayıtlı ağı denemez.

Robot ayrıca `http://kufi.local/` sabit adresini
Avahi ile o anki Wi-Fi IP’sine yayımlar. IP ezberlemek gerekmez. `80` portundaki
köprü HTTP ve WebSocket trafiğini yalnızca yerel web portuna aktarır; varsayılan
`8080` portu da çalışır. Ağ değişiminde sayfa kısa ömürlü, yalnızca bağlantı
durumunu okuyabilen bir anahtarla bu sabit adı yoklar. Robot kimliği ve başarılı
istasyon bağlantısı doğrulanınca bu adrese gider. Wi-Fi parolası tarayıcı
depolamasına yazılmaz; yeniden açılma anahtarı en fazla 15 dakika geçerlidir.
HTTPS sayfası HTTP yoklaması yapamaz; bu durumda **Robotu aç** bağlantısını
kullanın. mDNS’nin engellendiği/istemci izolasyonlu ağlarda yönlendiricinin DHCP
istemci listesindeki robot IP’sini kullanmak gerekir; IP bağlantı ekranında da
gösterilir. Fiziksel telefonun Wi-Fi ağı tarayıcı tarafından değiştirilemez.
Yerel adı mevcut kuruluma uygulamak için
`sudo /usr/bin/python3 tools/install_network_service.py --hostname kufi.local`
kullanılır; Wi-Fi bağlantısı ve kayıtlı parolalar korunur. Aynı yerel ağda birden
fazla robot varsa her birine `--hostname` ile farklı bir `.local` adı verin.

Debian’ın boş yapılandırmalı genel `dnsmasq` hizmeti kurulurken loopback’e
bağlanır; kurulum ağı ayrı, arayüze bağlı dnsmasq süreci kullanır. Özel dinleme
veya DHCP yapılandırması varsa kurucu üzerine yazmayı reddeder. Ağ hizmeti
root’a ait `/usr/local/lib/kufibot-network/` kopyasından çalışır; ROS veya web
yeniden başlasa da ağ gözcüsü devam eder. Kodu güncellerken kurucuyu yeniden
çalıştırın. Kontrol: `systemctl status kufibot-network.service`.

DNS'in `.local` isimlerini yanlış çözdüğü ağlarda Android uygulamasındaki
**Bağlantı Ayarları** ekranı kullanılabilir. Ağ hizmeti UDP **8889** üzerinden
kalıcı robot kimliğini ve bağlantı durumunu duyurur; uygulama robotun yeni
IP'sini yanıt paketinin kaynak adresinden öğrenir. Ağ geçiş anahtarı ve aynı
robot kimliği yeni IP üzerinden doğrulanınca uygulama yeniden bağlanır,
sayısal web adresini gösterir ve paylaşır. Eski ROS keşfi UDP **8888** üzerinden
çalışmaya devam eder. Telefonun da hedef Wi-Fi'ye geçirilmesi gerekir;
uygulama için internet gerekmez. [Mobil kurulum adımları](../../KufibotMobile/README.md#dns-ve-internet-olmadan-wi-fi-kurulumu).

#### Mevcut ağı kesmeden doğrulama ve manuel geçiş testi

Tarayıcı, HTTP köprüsü, API ve ağ yardımcısını birlikte sınamak için:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q \
  src/kufibot_remote/test/test_network_rescue.py \
  src/kufibot_remote/test/test_network_e2e.py \
  src/kufibot_remote/test/test_network_setup_browser.py \
  src/kufibot_remote/test/test_device_settings.py \
  src/kufibot_remote/test/test_device_settings_browser.py
```

Bu testlerde fiziksel ağ işlemleri yalıtılır. Yanlış parola sonrası kurulum
ağına dönüş, yeniden deneme, işlem sürerken tarayıcının çevrimdışı kalması ve
gerçek HTTP kimlik doğrulamasından sonra sabit yerel adrese geçiş sınanır.
Gerçek dnsmasq ile DHCP adres dağıtımını ve captive DNS yanıtlarını ayrı bir
ağ ad alanında sınamak için:

```bash
sudo -n unshare --net .venv/bin/python tools/network_namespace_smoke_test.py
```

Bu araç yalnızca loopback içeren yeni bir ağ ad alanında çalışır; iki sanal
arayüz oluşturur ve gerçek DHCP DISCOVER/OFFER/REQUEST/ACK alışverişi yapar.
Fiziksel Wi-Fi bağlantısını değiştirmez. dnsmasq, lease klasörünün sahibi olan
`dnsmasq` kullanıcısının birincil grubuyla çalışır; Debian'da bu grup genellikle
`nogroup` olur.

Gerçek telefon ve Wi-Fi sürücüsüyle son kontrol kullanıcı tarafından yapılır:

1. Hedef Wi-Fi adını/parolasını hazırlayın ve bu adımları telefonda açık tutun.
2. Kaydedilmiş ağları koruyarak robotu bağlantısız duruma geçirmek için robotun
   terminalinde `sudo nmcli device disconnect wlan0` çalıştırın. Bu adım robotun
   mevcut Wi-Fi bağlantısını ve o bağlantı üzerinden çalışan ajanı keser.
3. Yaklaşık 45–50 saniye sonra telefondan parolasız **kufi** ağına bağlanın.
   İnternet olmadığı uyarısında bu ağa bağlı kalın.
4. `http://10.42.0.1/setup` adresini açın. Hedef ağı listeden seçin; listelenmiyorsa
   **Ağ adını elle gir** seçeneğini kullanın.
5. Önce yanlış parolayla deneyin. Yaklaşık 45 saniyelik bağlantı denemesi ve
   geri dönüş sonrasında **kufi** ağına yeniden bağlanıp aynı kurulum adresini
   açabilmelisiniz; ekranda başarısızlık/önceki ağa dönüş bilgisi görünmelidir.
6. Doğru parolayla yeniden bağlanın. Ekranda gösterilen sabit adresi kontrol
   edin; bu robotta `http://kufi.local/` olmalıdır.
7. Telefonu da hedef Wi-Fi'ye geçirin ve tarayıcı sekmesine dönün. Robot
   doğrulanınca arayüz yeniden açılmalıdır; gerekirse **Robotu aç** bağlantısına
   dokunun. Bağlantı Ayarları'nda hedef SSID'yi ve yeni IP adresini kontrol edin.
8. Robotu yeniden başlatın. Hedef ağa otomatik bağlanmalı ve aynı sabit yerel
   adresten açılmalıdır. mDNS engelliyse yönlendiricinin DHCP listesindeki IP'yi
   kullanın.

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
