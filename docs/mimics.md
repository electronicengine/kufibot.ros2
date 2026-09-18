# 3D mimik editörü

Web ve mobil kumandanın sol üst menüsünden **Mimikler · 3D hareket editörü** açılır.
3D modelde bir parçaya dokunun; turkuaz halkayı sürükleyerek veya açı alanını
kullanarak seçili zaman adımındaki pozu değiştirin. Kafa dönüşü ve boyun ayrı
eklemlerdir. Eller kollarıyla birlikte hareket eder.

Zaman çizelgesinde gezinin, **Adım** ile o andaki pozu kaydedin veya **Kopyala**
ile seçili adımı 0,5 saniye sonrasına çoğaltın. Zaman alanını değiştirerek adımları
taşıyabilirsiniz. İlk adım 0 saniyede kalır. **Akıcı** geçiş servo açılarını doğrusal
olarak birleştirir; aynı pozu iki zamana koymak bekleme oluşturur. **Basamaklı**,
eski hazır mimiklerin davranışını korur. Süre sınırı 300 saniye, adım sınırı 1000'dir.

**Önizle**, duraklatma ve sürükleme fiziksel komut göndermez. Önce kaydedin;
**Robotta çalıştır** bağlı robotta veya simülasyonda o kayıt sürümünü oynatır.
Kumanda sahipliği ve kumanda modu gerekir. DUR, bağlantı kaybı, sahiplik/mod değişimi,
manuel servo veya joystick komutu oynatmayı iptal eder. Bitişte son poz korunur.
Sayfa kapanırken veya uygulama arka plana giderken oynatma durdurulur.

## Başlatma

Normal derleme ve başlatma akışı geçerlidir:

```bash
./tools/ros2_build_sim.sh
./tools/ros2_sim_launch.sh sim voice:=false
```

Simülasyon gözlemcisinin altındaki kitaplık, **Çalıştır** ve **DUR** aynı sunucuyu
kullanır. Başka bir web/mobil istemci kumandaya sahipse panel kontrolü zorla almaz;
o istemciyi kapatın veya oynatmayı o istemciden başlatın. Tek başına gözlemci için:

```bash
ros2 run kufibot_simulation sim_viewer --ros-args -p remote_url:=http://127.0.0.1:8080
```

Editör doğrudan `http://<robot-ip>:<port>/mimics` adresinden de açılabilir. Ağ dışı
CDN kullanılmaz. Web uygulamasında iframe ve mobil uygulamada WebView mevcut kumanda
bağlantısını paylaşır. Mobilde yeni `react-native-webview` bağımlılığı nedeniyle
uygulama yeniden derlenmelidir (`npm ci`, `npm run android`).

## Kitaplık ve YZ

Kullanıcı kayıtları varsayılan olarak `~/.local/share/kufibot/mimics.json` içindedir.
`KUFIBOT_MIMICS_FILE` değişkeniyle değiştirilebilir; remote_controller ve
voice_agent_node aynı dosyayı görmelidir. Fiziksel robotun mevcut C++ yapılandırma
konumu bulunursa hazır kitaplık oradan okunur. Özel konumlar için her iki düğümde
`gesture_config_file`, `motion_config_file`, `joint_angles_file` parametrelerini aynı
ayarlayın. Simülasyon başlatıcısı ikisine de paketlenmiş örnek kitaplığı verir. Eski Raspberry
Pi yapılandırmaları bulunamayan masaüstü kurulumlarında YZ de paketlenmiş kitaplığa
geçer; kullanıcı kayıtları erişilebilir kalır.

Hazır JSON dosyaları değiştirilmez. Aynı kimlikle kaydedilen düzenleme kullanıcı
katmanında hazır kaydı geçersiz kılar. Atomik dosya değişimi ve kayıt sürümü kontrolü
kullanılır. Bir başka istemci kaydı değiştirmişse kaydetme 409 döner; **Yenile** ve
**Aç** ile son sürümü yükleyin. Oynatıcı başlatılan kaydın kopyasını kullanır.

YZ düğümü kullanıcı kitaplığını saniyede bir kontrol eder. Yeni açıklamalar mimik
seçimine katılır; embedding kataloğu değiştiğinde bir sonraki seçimde güncellenir.
Embedding modeli bulunmazsa kullanıcı ad/açıklamalarında kelime eşleştirme ve mevcut
hazır sınıflandırıcı kullanılır. Kitaplık yenilemesi çalışan hareketi değiştirmez.

HTTP: `GET /api/mimics`, `GET /api/mimics/{id}`, `PUT /api/mimics/{id}`.
PUT belgesi: `id`, `name`, `description`, `revision`, `duration_ms`,
`interpolation` (`linear`/`step`) ve `keyframes` (`time_ms`, altı açıyı içeren `joints`).
Yeni kayıtta revision=0; başarılı kaydetme sürümü bir artırır.
WebSocket `/control`: `playMimic` (`id`, `revision`), `stopMimic`; mevcut `stop` da
oynatmayı sonlandırır. `state.mimic` etkin kimlik, sürüm, durum, süre, geçen zaman ve
son oynatma hatasını taşır. Servo sınırları sunucuda doğrulanır.

## Model kaynağı ve kalibrasyon

Kaynak: kullanıcının verdiği `Wall-E_Assembly_NotForPrinting.stl`.
Kaynak dosya değiştirilmedi. GLB ve rig, `kufibot_interaction/model` içinde paketlenir;
web/mobil ve Panda3D aynı dosyaları kullanır. Kaynak SHA-256 değeri `rig.json` içindedir.
STL renk ve eklem bilgisi taşımadığı için sarı/gri malzemeler ve servo bağlantıları
ayrıca tanımlanmıştır. Model bu çalışma için kullanıcı tarafından sağlanmıştır.

Ölçek genişliği mevcut simülasyondaki 32 cm'ye getirir ve STL oranlarını korur;
sonuç yükseklik yaklaşık 37 cm'dir. Altı servo için sınırlar/nötr açıları mevcut
robot tanımlarından alınır; eski mimiklerdeki boyun 30° başlangıcı korunur, genel
nötr değer 60° olarak kalır. Montaj açısı (`assembly_deg`) servo nötründen ayrı
saklanır. `pivot_m`, `axis`, `multiplier` ve `assembly_deg` gerçek robotla
kalibre edilene kadar görsel hareket eşleşmesi yaklaşık kabul edilmelidir.
Simülasyonun lidar/kamera ölçüm kalibrasyonu bu görsel değişiklikle değiştirilmez.

Model yaklaşık 132 bin üçgen ve 2,7 MB'dir. Palet makaraları kendi merkezlerinde
döner. Model yeniden üretimi (girdi dosyası değiştirilmez, çıktı dosyaları yenilenir):

```bash
.venv/bin/pip install -r tools/robot-model-requirements.txt
.venv/bin/python tools/build_robot_model.py \
  /home/ybulb/workspace/test_blender/Wall-E_Assembly_NotForPrinting.stl \
  src/kufibot_interaction/kufibot_interaction/model
```

Pivot veya parça sınıflandırması değiştirilecekse önce üretim betiğini güncelleyin;
GLB içindeki pivot konumlarıyla rig tanımı birlikte üretilir. İşaret, sınır ve montaj
ofsetleri rig tanımından her iki görüntüleyiciye uygulanır. Kaynağın koordinat
sistemi glTF Y-up/metreye çevrilirken üçgen yönleri de düzeltilir.
Three.js 0.180.0 yerel olarak paketlenmiştir; MIT lisansı web/THREE-LICENSE.txt'dedir.

## Doğrulama

Otomatik testler kayıt doğrulaması, sürüm çakışması, doğrusal/basamaklı zamanlama,
YZ kitaplığı yenilemesi, sahiplik, iptal, GLB hiyerarşisi ve altı eklemin sınırlarını
kapsar. Chromium testi gerçek WebGL üzerinde kayıt/önizleme/oynatma/DUR akışını ve
390 piksel düzenini sınar. ROS testleri için ortamı yükleyin:

```bash
source /opt/ros/jazzy/setup.bash
source install/setup.bash
.venv/bin/python -m pytest src/kufibot_remote/test src/kufibot_interaction/test src/kufibot_simulation/test
```

Fiziksel robotla her eklemin nötrü, hareket yönü, güvenli minimum/maksimumu ve servo
komutuna karşı gerçek açısı ayrıca doğrulanmalıdır. Gerçek Android cihazındaki
WebView/dokunma akışı ve mekanik hareket doğruluğu masaüstü tarayıcı testiyle
kanıtlanmış sayılmaz.

Servo/model referansları: başlangıç pozu sağ kol 15°, sol kol 170°, boyun
10°, baş dönüşü 90°, sağ göz 170°, sol göz 0° olarak kullanılır.
`neutral_deg` başlangıç açısıdır; `assembly_deg` ise STL'nin dönüş uygulanmamış
pozuna karşılık gelen servo açısıdır. Boyun 0° karşıya bakar; mevcut 0,35
boyun mekanizma oranı korunmuştur (kesin fiziksel oran henüz ölçülmedi).
Baş dönüşü 90° merkezli ±90°'dir. Sol göz 0° ve sağ göz 170° düz;
sol göz 40° ve sağ göz 140° aşağı inik konumdadır.
STL'nin ileri uzanan kolları, sol 170° / sağ 10° referansında yataydan
37,5° aşağı döndürülür; bu, bildirilen 30–45° eğim aralığının yaklaşık
orta değeridir. Bu yüzden kolların `assembly_deg` değerleri sol 132,5°,
sağ 47,5°'dir. Bu eşleme fiziksel ölçümle doğrulanmış tam kalibrasyon değildir.
Model tekrar üretilirken aynı değerler `tools/build_robot_model.py` ile korunur.
