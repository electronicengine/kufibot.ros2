# Kufibot Android uygulaması

Uygulama robotu yerel ağda otomatik bulur ve robotun güncel web arayüzünü
uygulama içinde açar. Kumanda, canlı kamera, sesli ajan, araçlar, workflowlar,
kayıtlar, bağlantı ayarları, kalibrasyon ve mimik editörü web ile aynı ekranlardır.
Telefon ve robot arasında internet veya `kufi.local` adının çözülmesi gerekmez.

## Kullanım

1. Telefonu robotla aynı Wi-Fi ağına bağlayıp uygulamayı açın. Robot bulunduğunda
   arayüz otomatik açılır. İlk kurulumda robotun parolasız **kufi** ağı kullanılır.
2. Menüden **Bağlantı Ayarları** ekranına gidin. Robotun hedef Wi-Fi ağını seçin
   veya adını elle girin; parolayı yazıp bağlanın.
3. **Telefonun Wi-Fi ayarlarını aç** ile telefonu da hedef ağa geçirin.
4. Uygulamaya dönün. Aynı robotun yeni IP'si ve bağlantının başarıyla tamamlandığı
   kısa ömürlü ağ geçiş anahtarıyla doğrulanır; arayüz yeni adreste açılır.
5. **Uygulama bağlantısı → Web adresini paylaş** ile sayısal adresi bilgisayara
   aktarabilirsiniz. **Robotları bul** başka bir robot seçmek veya IP girmek içindir.

Yanlış parolada robot önceki ağa/hotspota döner. Telefonu tekrar **kufi** ağına
geçirip bağlantı ayarlarından yeniden deneyin. Ağ geçişi bellekte 15 dakika takip
edilir. Uygulama tamamen kapatılırsa robot tekrar keşfedilebilir; önceki geçiş
anahtarı uygulama belleğinde tutulmaz.

Uygulama hem dikey hem yatay kullanımı destekler. Arka plana geçildiğinde veya
robot seçme ekranı açıldığında web kumandası duraklatılır ve kumanda sahipliği
bırakılır. WebSocket kontrolünü yalnızca ortak web arayüzü yürütür. Yeni web
özellikleri robot tarafı güncellendiğinde uygulamada da görünür.

## Robot tarafı

Mevcut projeyi kurup ROS başlatma ayarlarını uygulayın; web kontrolcüsü açık
olmalıdır. Ağ kurtarma ve kimlikli keşif hizmetini mevcut Wi-Fi'yi kesmeden kurun:

```bash
sudo /usr/bin/python3 tools/install_network_service.py
```

Keşif UDP **8889** üzerinden yapılır; eski robotların UDP **8888** yanıtları da
kullanılabilir. Kalıcı robot kimliği ve ağ geçişi doğrulaması için yeni ağ hizmeti
gerekir. Kontrol ve arayüz varsayılan olarak TCP **8080**, kamera WebRTC kullanır.
Android interneti olmayan kurulum ağında da yerel Wi-Fi üzerinden iletişim kurar.

Farklı IP aralıkları ve ağ maskeleri Android'in Wi-Fi bilgilerinden otomatik
öğrenilir. Cihaz izolasyonu yerel iletişimi; UDP broadcast engeli otomatik keşfi
engelleyebilir. Yalnızca keşif engelliyse bilinen IP ile bağlantı denenebilir.

## Expo ile kurulabilir APK

Node.js **22.13+** yeterlidir; bulut derlemesi için yerel Android SDK veya JDK
kurulmaz. `preview` profili Expo Go ve Metro gerektirmeyen APK üretir.

```bash
cd KufibotMobile
npm ci
npx eas-cli@27.2.0 login --device
npx eas-cli@27.2.0 build --platform android --profile preview
```

Bu robotta kurulu Node/EAS araçlarını kullanmak için proje kökünden:

```bash
./KufibotMobile/tools/eas.sh login --device
./KufibotMobile/tools/eas.sh build --platform android --profile preview
```

Proje `@verasist/kufibot-controller` olarak bağlıdır. `.easignore` ve robot için
hazırlanan `eas.sh`, yalnızca mobil kaynakları yükler; ROS kaynakları, modeller,
kayıtlar, ortam dosyaları ve yerel derleme önbellekleri gönderilmez. Yerel
`modules/kufibot-discovery/android/src` kaynakları pakete dahildir. Üst düzey
`android` dizini dışarıda bırakılır; EAS bunu Expo yapılandırmasından yeniden üretir.

## Geliştirme ve doğrulama

```bash
cd KufibotMobile
npm ci
npm run typecheck
npm run test:network
npx expo install --check
```

Android SDK/JDK bulunan bir geliştirme bilgisayarında `npx expo run:android`
yerel native derleme ve yükleme yapar. UDP modülü nedeniyle Expo Go kullanılamaz.
ARM Linux üzerinde paketleme kontrolü için Hermes bytecode üretmeden
`npx expo export --platform android --no-bytecode` kullanılabilir; bu komut APK
üretmez. Standart Hermes derleyicisi x86-64 olduğundan bu cihazda APK bulutta alınır.

Proje kökünde ortak arayüz ve ağ geçişi testi:

```bash
source /opt/ros/jazzy/setup.bash
source install/setup.bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q \
  src/kufibot_remote/test/test_native_web_app.py \
  src/kufibot_remote/test/test_network_setup_browser.py \
  src/kufibot_remote/test/test_mobile_control.py
```

Mimik ve workflow iframe'leri ortak web kumandasını kullanır. Eski native
editör köprüsü yalnızca açıkça `?native=1` ile açılan bağımsız editörlerde devreye
girer; WebView içinde iç içe editörler için ikinci bir kumanda bağlantısı açılmaz.
