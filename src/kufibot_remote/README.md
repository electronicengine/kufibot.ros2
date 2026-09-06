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
hareket joysticki pasif olur. Port ve robot adı
`src/kufibot_bringup/config/interactive_robot.yaml` içindeki
`remote_controller.ros__parameters` bölümünden değiştirilebilir. Web adresindeki
portu da buna göre değiştirin; tarayıcı WebSocket adresini otomatik olarak mevcut
sayfanın IP ve portundan alır.

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

Kumanda modunda YZ servo hareketleri engellenir. YZ modu mevcut görsel takip ve
sesli asistan hareketlerini açar; sesli asistan robotun mikrofonunda çalışmaya
devam eder. DUR, YZ servo hareketlerini iptal eden fiziksel acil durdurma değildir.

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
| `WS /video` | JPEG karelerinin base64 aktarımı; kontrol soketinden bağımsız |
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
