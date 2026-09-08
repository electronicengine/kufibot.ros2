# Kufibot Android kumandası

Expo / React Native uygulaması, kökteki Java `KufibotController` uygulamasının
kamera üstüne bindirilmiş yatay arayüzünü temel alır: üst köşelerde akım, gerilim,
pusula ve mesafe; solda hareket, sağda kafa joysticki; kol sürgüleri ve göz anahtarları.
Üst ortadan **KUMANDA / YZ MODU** seçilir. Menüden bulunan robotlar seçilebilir veya
`192.168.1.20:8080` biçiminde IPv4 adresi girilebilir.

## Robot tarafı

Proje kökünde, mevcut ROS 2 Jazzy kurulumu ve `.venv` ile:

```bash
source /opt/ros/jazzy/setup.bash
source .venv/bin/activate
python -m pip install 'aiohttp>=3.9,<4'
colcon build --symlink-install --packages-select kufibot_interfaces kufibot_interaction kufibot_remote kufibot_bringup
source install/setup.bash
./tools/ros2_launch.sh kufibot_bringup interactive_robot.launch.py remote:=true motors:=true
```

Yalnızca kamera/kafa için `motors:=false` kullanın. Motor düğümü bulunmazsa soldaki
joystick pasif görünür. Normal başlatmada `remote` ve `motors` varsayılan olarak
açıktır; robot Kumanda modunda başlar. YZ modu seçilene kadar sesli ajan oturumu
ve otomatik YZ davranışları başlamaz. Mevcut robot yığını zaten çalışıyorsa ikinci kez başlatmayın; güncel
`servo_arbiter` çalışırken köprüyü ayrıca `.venv/bin/python` ile çalıştırabilirsiniz:

```bash
source install/setup.bash
.venv/bin/python -c 'from kufibot_remote.node import main; main()'
```

`interactive_robot.yaml` içindeki `remote_controller` parametreleri robot adı,
TCP portu ve keşif portunu belirler. Telefon UDP **8888** üzerinden keşif yapar;
bu port iki tarafta aynı kalmalıdır. Kontrol `/control` WebSocket, video
`/offer` HTTP signaling + WebRTC RTP üzerinden akar. Mod değişiminin gerçekten
uygulanması için güncellenmiş `servo_arbiter` gereklidir.

## Android uygulaması

WebRTC native bağımlılığı eklendi: eski APK ve Expo Go ile çalışmaz. Yeni APK
kurulmalıdır. Expo 55 için WebRTC 124.0.7 ve config plugin 14.0.0 sabittir.
`npm ci` sonrası `npx expo prebuild --platform android` ve
`npx expo run:android` kullanın; mevcut UDP keşif modülünü koruyun.


Node.js **22.13+**, npm, JDK 17 ve Android SDK kurulu bir geliştirme bilgisayarında:

```bash
cd KufibotMobile
npm ci
npm run android
```

Sonraki geliştirme oturumları: `npm start`. UDP yerel Android modülü kullandığından
**Expo Go yerine development build** gerekir. `npm run android` bağlı Android
telefona geliştirme uygulamasını derler/yükler. USB hata ayıklamasını etkinleştirin.
Metro için telefon ve geliştirme bilgisayarı da erişebilir bir ağda olmalıdır.

Metro gerektirmeyen kurulabilir APK için EAS hesabınızla:

```bash
npx eas-cli build --platform android --profile preview
```

Bu komut derlemeyi EAS hizmetine gönderir. EAS kullanmadan Android SDK bulunan
bilgisayarda `npx expo run:android --variant release` ile yerel release derlemesi
alınabilir. Android çıktıları prebuild ile üretilir, depoya eklenmez.

## Bağlantı ve kontrol davranışı

- Telefon ve robot aynı IPv4 LAN/Wi-Fi ağında olmalıdır. Uygulama üç saniyede bir
  UDP broadcast gönderir ve ilk bulunan robota bağlanır. Menüde diğer bulunan
  robotlar seçilebilir. AP/client isolation ve broadcast engeli varsa IP ile
  bağlantı kullanılabilir; TCP 8080 erişilebilir olmalıdır.
- İlk bağlanan telefon kontrolü alır; diğerleri izleyicidir. Kontrol sahibi
  ayrıldıktan sonra menüden **Kumandayı devral** seçilebilir.
- Köprü başlangıçta kumanda modundadır. YZ modu servo arbiter üzerinden mevcut
  görsel takip ve sesli asistan hareketlerini etkinleştirir; sesli ajan oturumu
  bu geçişte başlar. Kumanda moduna dönülünce oturum ve otomatik YZ davranışları
  durur. Telefon mikrofonu aktarımı ve otonom navigasyon bu sürümde yoktur.
- Kafa joysticki açı değişim hızını kontrol eder (en çok 45°/s); bırakılınca son
  hedefte kalır. Eklem sınırları mevcut robot sınırlarıyla aynıdır. Sağ/sol yönü
  `headLeftRight`, dikey yön `neck` üzerinden uygulanır. Kollar sürgü bırakılınca
  hedef alır. Servo durumları donanımın hesapladığı konumdur; fiziksel enkoder
  ölçümü değildir.
- Hareket en çok 0.25 m/s ve 1 rad/s komutlanır; motor düğümünün mevcut alt/üst
  hız sınırları ayrıca uygulanır. 500 ms giriş kesintisi, bağlantı kopması veya
  uygulamanın arka plana geçmesi hareketi durdurur. İki saniye heartbeat
  kesilince kontrol sahipliği bırakılır. Yeniden bağlantıda eski joystick
  girişleri yürütülmez. **DUR**, kumanda joystick girdilerini sıfırlar; fiziksel
  acil durdurma veya YZ servo hareketlerini iptal etme işlevi değildir.
- Köprü/telefon kaybolduğunda kumanda modundan kendiliğinden YZ'ye geçilmez.
  Arbiter son servo hedefini tutar; motor düğümünün bağımsız 500 ms watchdog'u
  köprü çökmesinde tekerlekleri durdurur.
- Kamera yalnızca WebRTC ile alınır ve native RTCView'de çizilir.
  Varsayılan yayın 480 piksel genişlikte, hedef 15 FPS'tir; JPEG/base64 yoktur.
  MediaPipe yalnızca YZ modunda çalışır. Üç saniyeden eski sensörler `—`,
  robot kamerası güncel değilse görüntü bekleme ekranı olarak gösterilir. Arbiter yanıtı yoksa joystickler etkinleşmez.
- Bu sürüm güvenilen yerel ağ içindir: TLS, parola/eşleştirme ve internet üzerinden
  erişim içermez. Portları internete yönlendirmeyin. Mevcut Java istemcisinin
  protokolüyle uyumluluk amaçlanmaz; yalnızca arayüzü referans alınmıştır.

## Doğrulama

```bash
# Proje kökünde; ROS mesaj paketleri derlenmiş olmalı
source install/setup.bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest src/kufibot_remote/test src/kufibot_interaction/test/test_remote_arbitration.py -q
# Mobil dizininde
npm run typecheck
npx expo install --check
npx expo export --platform android
```

WebSocket testi yalnızca localhost portu açar, ROS testleri donanımı çalıştırmaz.
Telefonda kabul kontrolü: otomatik keşif, kamera, sensörler, iki joysticke birlikte
basma, bırakma, YZ/kumanda geçişi, Wi-Fi kesintisi, arka plana alma ve ikinci
telefonun kontrol alamaması. Fiziksel joystick yönlerini ilk kullanımda düşük
hızla doğrulayın.

## Web kumandası

Aynı köprü tarayıcıya da arayüz sunar: `http://ROBOT_IP:8080/`.
Web sürümü Expo veya UDP tarayıcı desteği gerektirmez. Mobil ve tarayıcı tek kontrol
sahipliğini paylaşır. [Web kurulum ve kullanım rehberi](../src/kufibot_remote/README.md).
