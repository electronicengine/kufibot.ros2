# Navigasyon araçlarının simülasyon testleri

Görsel mutfak demosunu tek komutla açın:

```bash
./tools/navigation_sim_demo.sh
# kısa adla aynı panel:
./tools/navigation_sim.sh
```

Varsayılan modda **LLM rolü sizdedir**; otomatik araç çağrısı yapılmaz.
Hedef panelde gösterilir. Sağdaki menüden aracı seçin, parametrelerini girin
ve **Çağır** düğmesine basın. `read_sensor_values` anlık lidar/kamera taraması
ve görüntü verir; ardından gözlemlere göre doğrudan `goto` veya `look_at`
çağırın. `goto`, `look_at` ve `read_sensor_values` ilk çağrıda gereken iç ROS
görevini kendileri oluşturur. `request_id`, görev kimliği ve yer etiketi panelde
yer almaz. Sayılarda virgül veya nokta kullanılabilir.

`goto` başlangıçta kafa taraması veya fotoğraf çekmez; yalnızca dönüş/açısal
hareket ve ileri mesafeyi uygular. Hareket sonunda ise geldiği noktada tek yönlü
bir kamera/lidar karesi alır ve birikmiş haritayla birlikte sonuçta paylaşır.
Hareket boyunca ileri lidar engel kontrolü sürer; engelde robot durur ve son
kareyle `blocked` sonucu verir. Daha geniş çevre ölçümü gerektiğinde açıkça
`read_sensor_values` çağırın. `look_at` da seçilen yöndeki son kamera/lidar
kareyi ve aynı birikmiş haritayı sonucunda paylaşır.

Her açık sensör taramasının fotoğrafındaki mesafe haritası görevin ilk robot
konumuna sabitlenir. Robotun pusulayla ölçülen yönü ve lidar mesafesi değişiminden
ölçülen ilerlemesiyle sonraki taramaların engel noktaları bu ortak haritaya eklenir.
Aktif görev boyunca yeni gelen ön lidar ölçümleri de, ayrı bir araç çağrısı
gerekmeden, aynı haritaya sürekli eklenir.
Turkuaz nokta başlangıcı, sarı robotu, kırmızı noktalar birikmiş lidar engellerini
gösterir. Noktalar 10 cm'lik ortak dünya hücrelerinde tutulur: aynı sınır tekrar
ölçülürse yeni bir harita veya kopya nokta oluşmaz, o hücre güncellenir. Harita
ölçüm tabanlı tahmindir; çarpışma güvenliği yine canlı lidar denetimiyle yapılır.

`read_sensor_values` kafa önce sektörün başlangıcına geldikten sonra `-90°`den
`+90°`ye tek, sürekli bir servo hareketiyle tarar; 10° değeri durak noktası değil,
güvenlik kapsamı için izin verilen en büyük açı boşluğudur. Kafa dönerken her yeni
lidar zaman damgası, gerçek açı en az 0,25° değişmişse haritaya işlenir. Böylece
örnekleme lidarın gerçek yayın hızını aşmaz; servo hedefte sabitken aynı ölçüm
tekrar tekrar eklenmez. Tüm örnekler servo sınırları içindeki `-90°…+90°` sektörüyle
ve taze sensör denetimiyle sınırlıdır.

Ev planı, robotun yolu, kafa/lidar yönü, canlı kamera ve araca teslim edilen
fotoğraflar birlikte gösterilir. Fotoğraf düğmeleriyle eski görüntülere bakın.
Teslim edilen fotoğrafa tıklayınca panelin üstünde büyük görünümü açılır;
fotoğrafa tekrar tıklayarak veya `Esc` ile kapatın.
Soldaki çağrı geçmişinden kayıt seçin; tam JSON üzerinde fare tekerleğiyle
kaydırın veya kopyalayın. **Yanıtım** alanına kendi cevabınızı yazıp kaydedin;
bu metin olay kaydına eklenir ve robot komutu olarak çalıştırılmaz.

Sonuç geldikten sonra **Yeni oturum** iç görevi güvenle sonlandırır ve yeniden
yetki açar; robotun konumunu sıfırlamaz. Manuel panelde insanın düşünme süresi için görev
bekleme sınırı bir saattir; sensör tazeliği ve bağlantı kontrolleri değişmez.
Esc veya pencereyi kapatmak düğümleri durdurur.

Bu demo gerçek ROS topic/service/action iletişimini, servo yöneticisini ve
simülasyon motor sürücüsünü kullanır. AI servisinin yerini sizin araç
çağrılarınız alır; API anahtarı gerekmez. Ayrı ROS alanı ve namespace ile açılır.
Kumanda heartbeat'i `remote/command` üzerinden servo yöneticisine gönderilir;
`remote/applied_mode` değerini yalnızca servo yöneticisi yayınlar. ROS executor
ayrı iş parçacığında çalışır; pencere ve araç beklemeleri heartbeat'i kesmez.

## Robot fiziksel referansı

Simülasyonun çarpışma ve sensör modeli `robot_teknik_olculer.svg` çizimindeki
yaklaşık referansa göre ayarlıdır: dış genişlik 32 cm, toplam yükseklik 32 cm,
sensör merkezi yerden 28,5 cm, iki göz merkezi arası 6 cm'dir. Kamera robotun
sol gözünde (`-3 cm`), lidar sağ gözünde (`+3 cm`) bulunur. Dünya simülatörü
raycast ve kamera görüntüsünü bu ayrı merkezlerden üretir; navigasyonun
birikmiş haritası da lidarın sağ ofsetini hesaba katar.

Çizim derinlik ve kesin CAD geometrisi vermediği için 2B çarpışma modeli 32 cm
çaplı dairesel bir zarf (`body_radius_m: 0.16`) kullanır. Bu varsayım
`src/kufibot_simulation/config/simulation.yaml` dosyasında açıkça yer alır.
Üç boyutlu izleyicide de aynı dış zarf, paletli gövde ve mavi kamera / turuncu
lidar gözleri metre ölçeğinde çizilir.

Yerinde dönüş kontrolü kalibre edilmiş dairesel gövde için gövde yarıçapı,
açıklık payı ve lidar montaj uzaklığını kullanır. İleri hareketin fren mesafesi
yan duvarlara uygulanmaz. Dönüş sırasında da aynı açıklık kontrol edilir;
dar geçit ve eksik tarama kapsamı dönüşü engellemeye devam eder.

JPEG fotoğrafları, gözlem metaverileri ve tüm çağrı/sonuçlar terminalde yazılan
`/tmp/kufibot-kitchen-*` dizinine kaydedilir. Pencere kapanırken son ekran
`summary.png` olarak kaydedilir. İsteğe bağlı kullanım:

```bash
./tools/navigation_sim_demo.sh --output-dir /tmp/mutfak-demo
./tools/navigation_sim_demo.sh --goal "Koridordan geçerek mutfağa git"
./tools/navigation_sim_demo.sh --auto
./tools/navigation_sim_demo.sh --auto --headless
./tools/navigation_sim_demo.sh --auto --exit-when-done
```

İsteğe bağlı otomatik rota daha önce gerçek ROS iletişimi ile doğrulandı:
yaklaşık 4,5 dakikada mutfağa ulaştı, 10 fotoğraf teslim edildi ve son konum
(-3.48, 3.08) m oldu. Panelin çizimi ve bitiş ekranı ekran dışı SDL sürücüsüyle
kontrol edildi; Ctrl+C ile tarama sırasında temiz kapanış da denendi.
Dönüş açıklığı düzeltmesinin ardından ilgili 109 regresyon testi geçti.

Manuel panel testi (`test_navigation_demo_ui.py`) gerçek ROS düğümleri ve
ekran dışı SDL ile pencere tıklamalarını ve metin girişini çalıştırır:
açılışta otomatik çağrı olmaması, sensör taraması, 30 cm ilerleme,
fotoğraf teslimi, geçersiz sayı reddi, JSON sonucu, kullanıcı yanıtı,
yeni oturum ve kapanış kaydı doğrulanır. Araç köprüsü testleriyle
birlikte 5 test geçti. Aynı ortamda tekrar çalıştırmak için:

```bash
source tools/_ros2_env.sh
ROS_LOG_DIR=/tmp/kufibot-test-logs .venv/bin/python -m pytest \
  src/kufibot_simulation/test/test_navigation_demo_ui.py \
  src/kufibot_interaction/test/test_navigation_tools.py -q
```

`kufibot_simulation.py` adlı tek bir dosya yerine `src/kufibot_simulation`
paketi kullanılıyor. Gerçek pencere/simülasyon entegrasyon testi:
`src/kufibot_simulation/test/test_navigation_demo_ui.py`.

Çalıştırma (depo kökünden, ROS 2 Jazzy ve kurulmuş workspace ile):

```bash
source /opt/ros/jazzy/setup.bash
source install/setup.bash
ROS_LOG_DIR=/tmp/kufibot-test-logs .venv/bin/python -m pytest \
  src/kufibot_simulation/test/test_navigation_demo_ui.py \
  src/kufibot_navigation/test \
  src/kufibot_interaction/test/test_navigation_tools.py -q
```

Testler kayıtlı `read_sensor_values`, `goto` ve `look_at` araçlarını çağırır.
Araç köprüsü gerçek ROS servislerini
ve NavigateStep action sunucusunu kullanır. Navigasyon çıktıları gerçek motor
sürücüsünün simülasyon uyarlamasından geçer; servo limitleri ve hareket hızı
ServoNode üzerinden uygulanır. Dünya simülatörü ev planında konumu entegre
eder, lidar mesafelerini raycast ile ve kamera görüntülerini renderer ile üretir.
Görüntü gönderimi için dış ses/AI servisi yerine teslimat kaydı kullanılır;
JPEG ve gözlem onayı da denetlenir.

Hızlı ve tekrarlanabilir çalıştırma için saat 50 ms adımlarla ilerler; sensör
ve aktüatör topic bağlantıları yerel callback bağlantılarıdır. ROS servis/action
trafiği gerçektir. Servo arbiter, uzaktaki AI sağlayıcısı, konuşma tanıma ve
modelin görüntüden hedef seçmesi bu testlerin kapsamına girmez. Testlerde
hedef adımlarını test kodu belirler; sonuçlar simülatörün gerçek konumuyla
karşılaştırılır. Bu sonuçlar fiziksel robot kalibrasyonu yerine geçmez.

Kapsanan senaryolar:

- Başlangıçta 19 yönlü tarama, mesafe doğruluğu, JPEG teslimatı ve onayı.
- 60 cm ilerleme, ardından 90° dönüp 40 cm ilerleme; konum hatası en çok 4 cm.
- Sağ/sol küçük dönüşler, -90° ve 180° dönüşler; yön hatası en çok 2°.
- `look_at(-60)` görüntüsünün gerçekten -60° yönüne ait olması.
- Dönüşten sonraki yeni yolun taranması ve kapalıysa ilerlemeden durma.
- Duvara giden komutun engellenmesi ve aynı request_id ile tekrar hareket edilmemesi.
- Hareket sırasında kamera/lidar kaybında durma.
- Hareket sırasında beliren engelde çarpışmadan durma ve yeni gözlem teslimi.
- Yetki kaybının nedeninin iletilmesi ve görev iptali.
- Sektör taraması, yer işaretlerinin durum sorgusunda korunması ve önceki
  görevin görüntüsünün yeni hareket izni olarak kullanılmadan teslimi.
- Geçersiz görev kimliğinin hareket olmadan açık hata üretmesi.
- Oturma odasından koridor ve mutfak kapılarından geçerek mutfağa varış;
  son konumun hedefin 12 cm yakınında olması ve tüm rota boyunca çarpışmama.
- Açık koridorda kısa/uzun adımların kabulü; dar geçit, ön engel ve eksik yan
  tarama kapsamının hareketi engellemesi.

Düzeltmeler: ROS yaw/pusula işaret dönüşümü; simülasyonda 20 Hz döngüye uygun
ince dönüş hızı; dönüşten sonra yol doğrulama taraması; tek yöne bakışta doğru
kamera açısı. Yetki ve task denetimleri hareketten önce `Navigator.submit`
içinde yapılır; bu hatalar artık nedeni gizleyen ROS goal rejection yerine
`not_authorized`/`invalid_task` action sonucu olarak döner. Eşzamanlı action
istekleri için tek işlem sınırı korunur.

Yol kontrolü, her lidar ışınının robotun geçeceği dikdörtgen alandan çıkış
mesafesini hesaplar. Yanda gövde yarıçapı ve açıklık payı, önde istenen yol
ve duruş payı aranır. Tüm ön yarım daireyi kapsayan sık tarama zorunludur.
Bu, açık kapının kenarına öndeki engelle aynı mesafe koşulunu uygulayan ve
geçilebilir koridorları reddeden eski kontrolü düzeltir. Seyrek lidar
örneklemesi fiziksel ortamın kusursuz bir haritası değildir.

Tüm depo testleri için NumPy 1.x, MediaPipe 0.10.21 ve OpenCV 4.11 uyumlu
sürümleri `requirements.txt` içinde sabitlenmiştir. NumPy 2, sistemin
Matplotlib/ROS ikili modülleriyle uyumsuzdu; kurulu yeni MediaPipe ise
izleme node'unun kullandığı `solutions` API'sini içermiyordu. İfade motoru
testleri başka bir bilgisayardaki C++ deposuna ihtiyaç duymadan, aynı JSON
biçimini kullanan küçük test kataloğuyla çalışır.

Tüm testleri çalıştırmak için yukarıdaki ortam kurulumundan sonra:

```bash
ROS_LOG_DIR=/tmp/kufibot-test-logs .venv/bin/python -m pytest -q
```

ROS/WebSocket testleri yerel soket açabilen bir ortam gerektirir.

Son doğrulama: tüm depo çalıştırmasında **236 geçti, 2 atlandı** (143 saniye).
Atlananlar Playwright kurulu olmadığı için tarayıcı testi ve yerel GGUF
modeli/yapılandırması olmadığı için model entegrasyon testidir. Navigasyonun
16 simülasyon senaryosunun tamamı geçti. `pip check` bağımlılık çakışması
bulmadı; MediaPipe yüz/el algılama bileşenlerinin başlatılması da doğrulandı.
