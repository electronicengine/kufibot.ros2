# Sayısal harita ve toplu waypoint navigasyonu

## Kullanım

1. `read_sensor_values()` ile temiz kamera ve sayısal harita alın. Gerekiyorsa
   `look_at` ile hedef yönünü inceleyin. Arkadaki alan bilinmiyorsa önce güvenli
   yerinde dönüş ve yeni taramayla ölçün; bilinmeyen alana rota çizilmez.
2. Görüntüdeki hedefi haritanın metre koordinatlarıyla ilişkilendirin. Harita
   engel konturları içerir; nesne adlarından otomatik hedef koordinatı çıkartan
   bir yer tanıma sistemi değildir.
3. `follow_route` aracına bütün sıralı waypoint’leri tek çağrıda gönderin:

```json
{
  "map_id": "son_gözlemdeki_map_id",
  "map_revision": 42,
  "waypoints": [{"x_m": 0, "y_m": 0.8}, {"x_m": 0.6, "y_m": 0.8}]
}
```

Örnek koordinatlar yalnızca biçimi gösterir; gerçek noktaları güncel ölçülmüş
haritadan seçin. Web araç panelinde yalnızca waypoint JSON listesi girilir;
harita kimliği ve sürümü panel ekler. Rota tek seferde yayınlanır ve kullanıcı
onayı beklemeden takip edilir. Her noktaya ayrı `goto` göndermeyin.

Hedef ölçülmemiş alandaysa, bilinen serbest alan içinde bir keşif rotasının tüm
noktalarını gönderin. Rota sonunda yapılan taramadan sonra kalan yolu yine tek
liste halinde planlayın. Geçici engelde robot durur; taze ölçümlerle yolun
açıldığı doğrulanınca aynı rotaya devam eder. Engel 30 saniye kalırsa kalan rota
sonlandırılır ve yeni plan için fotoğraf/sayısal harita döner.

## Harita sözleşmesi

`navigation/distance_map` yalnızca navigasyon düğümü tarafından yayınlanır.
Uygulamalardaki `distanceMap` bu veriyi olduğu gibi taşır. Gözlemdeki `map`,
görüntü yakalandığında alınmış aynı haritanın anlık kopyasıdır; eski fotoğrafa
yeni harita yapıştırılmaz.

- `map_id`, `revision`: başlangıç koordinat sisteminin kimliği ve hücre değişim sürümü.
- `frame: startup_robot_pose`, `units: m`, `origin: [0,0]`: sabit başlangıç sistemi.
  `+x` başlangıç sağı; `+y` başlangıç önü; yön saat yönünde pozitiftir.
- `robot_pose`, `robot_heading_deg`, `pose_source`, `pose_valid`: konum ve güvenilirlik kaynağı.
  Simülasyon konumu dünya simülatöründen; fiziksel konum lidar ilerlemesi, pusula
  ve manuel sürüşte komut integrasyonundan tahmin edilir. Bu SLAM/enkoder yerelleştirmesi değildir.
- `resolution_m: 0.1`, `obstacle_points`, `boundary_paths`: ölçülmüş engel hücreleri ve konturları.
  `measured_bounds_m` gözlenen kapsamdır, kesin oda dış sınırı değildir.
- `wall_paths`, `wall_segments`: ölçümlerden çıkarılan düzgün duvar çizgileri.
  Web ve mobil bu çizimi kullanır; eski sunucularda `boundary_paths` kullanılmaya devam eder.
  Çizgi başına destekleyen ölçüm sayısı, RMS hatası ve en büyük ölçüm aralığı verilir.
  `source=inferred_from_measured_obstacles`: bunlar çıkarımdır, yeni ölçüm değildir.
- `free_cells`: yalnızca sensör ışınlarının geçtiği hücre merkezleri.
  LLM gözleminde bu liste, kayıpsız `free_cell_runs` ile değiştirilir:
  `[y_index, x_first, x_last_inclusive]`; koordinat = indeks × çözünürlük.
  Diğer hücreler bilinmeyendir. Uzun ölçümün 8 metreye kırpılması sahte duvar üretmez.
- `measured_at_monotonic_sec`, `measurement_age_sec`: ölçümün robot saatindeki zamanı/yaşı.
  Gözlemde `observation_age_sec` ayrıca bulunur; monoton saat duvar saati değildir.
- `planning`: robot yarıçapı, yan açıklık, fren/tepki payı ve waypoint toleransı.

Duvar haritası zaman aşımıyla veya kamera görüşünden çıkınca silinmez.
Önceden serbest ölçülmüş hücrede beliren engeller geçici katmana alınır;
`dynamic_obstacle_points` bu hücreleri aynı sabit koordinatlarda verir.
Kamera canlı yakın insan/hayvan gördüğünde eşleşen LiDAR dönüşü de geçici sayılır.
Bu ilişkilendirme yalnızca kutu LiDAR'ın görüntüdeki konumunu
(`lidar_image_x/y`, varsayılan 0.5/0.5) kapsıyor ve ölçüm zamanları 100 ms içinde
eşleşiyorsa yapılır; yandaki kedi uzaktaki duvarın sınıfını değiştirmez.
Geçici hücreyi temizlemek için en az üç yeni ışının en az 0.3 saniye boyunca
hücrenin 25 cm ötesine geçmesi gerekir. Diğer engeller için eşik 20 ışın ve
3 saniyedir. Tekrar engel dönüşü veya bir saniyeden uzun ölçüm boşluğu temizleme
kanıtını sıfırlar. Tek kaçırılan ölçüm duvarı silmez; temizleme doğrulanana kadar
engel arkasındaki alan serbest ilan edilmez. Sadece süre geçmesi alanı açmaz.
Yakın hücre sınırındaki santimetrelik ölçüm dalgalanmaları mevcut en yakın
engel hücresine sabitlenir. Geçici hücreler duvar konturuna bağlanmaz; web ve
mobilde kırmızı noktalarla gösterilir. `obstacle_points` planlama için iki
katmanın birleşimidir. Bu sınıflama nesnenin gerçekten hareket ettiğini kanıtlamaz.

Harita bellekte her hücre sınıfı için en çok 16384 hücre tutar. Serbest hücre
önbelleğinden çıkarılanlar bilinmeyen olur; engel hücreleri kapasite nedeniyle
asla çıkarılmaz. Engel kapasitesi dolarsa `occupied_capacity_reached=true`
döner ve yeni rota kabul edilmez; duvarlar görünür kalır.
Görev/oturum değişimi ve simülasyon konum kaynağının geç bağlanması haritayı sıfırlamaz. Navigasyon düğümünü
yeniden başlatmak yeni harita kimliği oluşturur; eski kimlikli rotalar reddedilir.

### Ölçüm ve konum eşleştirmesi

Duvar tamamlama önce komşu statik ölçümleri uzamsal bileşenlere ayırır.
RANSAC aykırı noktaları ayıklar; ortogonal en küçük kareler çizgiyi yeniden
hesaplayarak çapraz duvarlardaki hücre basamaklarını yumuşatır. En az dört
ölçüm ve 45 cm destek gerekir; çizgiye dik hata toleransı 6.5 cm, ölçümler
arasındaki en büyük tamamlanabilir aralık 35 cm'dir. Büyük boşluklarda ve
ışının serbest olduğunu ölçtüğü hücrelerde çizgi bölünür. Uçlar son ölçümden
öteye uzatılmaz. Geçici engeller ve desteksiz tek noktalar duvara dönüştürülmez.
Köşeler/ayrı doğrultular ayrı çizgiler olarak çıkarılır; kalan nesnelerin ham
konturları korunur. Bu yöntem büyük, hiç ölçülmemiş duvarları tahmin edemez.

Çıkarım `obstacle_points` veya `free_cells` içine yazılmaz; navigasyonun serbest
alan doğrulaması değişmez. Büyük haritalarda işlem motor/sensör ROS iş parçacığının
dışında, tek çalışan ve tek bekleyen sonuçla yapılır. Sonuç yalnızca aynı ölçüm
kümesi için kullanılır; güncelleme sırasında ham konturlar gösterilir.
`wall_reconstruction.status` hazır (`ready`) veya güncelleniyor (`updating`) bilgisini verir.
Yöntem referansı: https://pointclouds.org/documentation/group__sample__consensus.html

Haritanın başlangıç koordinatları değişmez. Lidar uç noktası, sabit haritadaki
robot konumuna gövde açısıyla döndürülmüş sensör ileri/yan ofseti ve
pusula + kafa açısı + lidar açı kalibrasyonuyla hesaplanan ışın eklenerek bulunur.
İlerleme her bacağın yerleşmiş pusula yönünde küçük artışlarla bir kez entegre
edilir; son pusula açısı geçmiş ilerlemeyi yeniden döndürmez. Manuel sürüşte
bir önceki aralıkta uygulanan komut kullanılır ve ofset ışından önce güncellenir.

Lidar ve eklem ROS zaman damgaları monoton ölçüm zamanına çevrilir. Harita için
pusula/kafa geçmişinden yakın zamanlı açı seçilir veya iki örnek arasında
interpolasyon yapılır; pusulada 359°–0° geçişi korunur. Motor kontrolünün hareketli
ortalama pusula filtresi harita ışınına gecikme olarak taşınmaz.
`map_sensor_skew_sec` (varsayılan 0.10 s) aşılırsa veya konum geçersizse ölçüm
haritaya eklenmez. Fiziksel robotta motorlu dönüş ve yerleşme sırasında duvar
ölçümü bekletilir; `mapping_status` gerekçeyi ve atlanan ölçüm sayısını taşır.
Bunlar fiziksel konum tahminidir; encoder/SLAM yerelleştirmesinin yerini almaz.

Konturlar sabit bir büyük görüntüden değil, dolu hücrelerin açıkta kalan
kenarlarından çıkarılır. Uzak bir ölçüm mevcut duvarları gizlemez; kapı
boşlukları ve delikler korunur. Yalnızca dolu hücreler değiştiğinde kontur yeniden hesaplanır.

## Yürütme ve durum

ROS `navigation/follow_route` bir `FollowRoute` action’ıdır: oturum/görev/istek
kimlikleri, `map_id`, `map_revision` ve `geometry_msgs/Point[] waypoints` alır
(`x/y` metre, `z=0`). Sonuç JSON, feedback ise durum/ilerleme/aktif indeks içerir.
Eski `NavigateStep` arayüzü korunur; iki action aynı hareket kilidini kullanır.

1–64 sonlu waypoint, güncel harita kimliği, geçerli oturum ve kalibrasyon gerekir.
Eski sürüm güncel haritada tekrar doğrulanır; gelecek sürüm veya başka harita
reddedilir. Bütün segmentler gövde, açıklık ve fren payıyla ölçülmüş serbest
hücrelerde olmalıdır. Robotun mevcut fiziksel ayak izi altındaki hücreler bu
kontrolde bilinir; başka bilinmeyen alanlar muaf değildir.

`waypoint_tolerance_m` varsayılanı 0.15 m, `route_timeout_sec` varsayılanı 1800 s.
Uzun segmentler/dönüşler mevcut goto sınırlarına bölünür; toplam en çok 256 iç
hareket adımı uygulanır. LLM köprüsü en çok 1820 s bekler; rota süresini bunun
üstüne çıkarmayın. Canlı sensör, yol değişikliği, ilerleyememe, DUR ve bağlantı
denetimleri bütün rotayı durdurur. Taze tarama mümkün değilse hata sonucu döner,
eski görüntü yeni gözlem gibi sunulmaz.

### Hareketli engel ve konuşmayla müdahale

`obstacle_wait_sec=30`, `obstacle_clear_sec=0.75`: LiDAR durma mesafesi,
ani yaklaşan LiDAR dönüşü, kamera tehlikesi veya kalan koridorun kapanması
motorları hemen sıfırlar. Yol, taze sensörler ve kamera ile 0.75 saniye açık
kaldığında ilerleme korunarak mesafe referansı yeniden kurulur; hareketli
nesnenin menzil değişimi robot hareketi sayılmaz. Beklemenin süresi dolarsa
`blocked/obstacle_wait_timeout` ve yeni gözlem döner. DUR, yetki kaybı, sensör
eskimesi veya kalibrasyon kaybından sonra otomatik devam edilmez.

`cancel_navigation()` uzun hareket kilidini beklemeden ROS iptali gönderir ve
sonuç gelene kadar bekler. Yeni `follow_route` önce eski action'ın durduğunu
doğrular, sonra yeni listeyi normal harita doğrulamasından geçirir. Geçersiz
JSON eski rotayı iptal etmez; geometrik olarak reddedilen yeni rotada robot
durmuş kalır. Sıradan konuşma rotayı kendiliğinden iptal etmez; LLM kullanıcı
isteğine göre iptal veya yeni rota aracını çağırır. SDK araç çağrılarını ayrı
asenkron görevlerde yürüttüğünden ses oturumu rota boyunca açık kalır.

### Kamera koruması

`obstacle_detector_node`, MediaPipe Tasks ObjectDetector VIDEO API'si ve paketle
gelen EfficientDet-Lite0 INT8 modeliyle varsayılan 5 FPS çalışır. Servo komutu
üretmez ve kafa takibinin mod kilidine bağlı değildir. En yeni kare işlenir;
kuyrukta eski görüntüler birikmez. `perception/navigation_obstacles` görüntünün
ROS zamanını, sınıfları, kutuları ve tehlike durumunu taşır. Eski/tekrarlı sonuçlar
kabul edilmez. Üretim başlatıcısında `visual_safety_required=true`: model
yüklenmezse veya sonuç bir saniyeden eskiyse otonom hareket durur. Model çalışma
anında ağdan indirilmez. Şematik simülasyon kamerası için bu zorunluluk varsayılan
kapalıdır; kamera koruma testleri aynı ROS konusuna ölçüm gönderir.
Simülatör motor adaptörü iki tekerlek güncellemesini tek hız yayını olarak
uygular; dur–devam geçişindeki yarım PWM güncellemeleri sahte dönüş üretmez.

Kamera tek başına metre mesafesi veya güvenilir nesne hızı üretmez. Yakınlık
kuralı normalize görüntüde `corridor_left=0.25`, `corridor_right=0.75`,
`near_bottom=0.70`, en az 0.12 kutu yüksekliğidir; sınıflama eşiği 0.45'tir.
Kamera montajı ve gerçek robotla bu alanın doğrulanması gerekir. LiDAR her
sınıf için mesafe korumasını sürdürür. Ham kare farkı, robotun kendi hareketini
nesne hareketi sayacağı için kullanılmaz. Görsel sınıflar gözlem haritasının
`visual_obstacles` alanına eklenir; derinliksiz kutulardan sahte harita engeli çizilmez.

API: https://ai.google.dev/edge/mediapipe/solutions/vision/object_detector/python

`navigation.route_plan` rota kimliği, harita kimliği/sürümü, başlangıç, tüm
noktalar, aktif indeks, tamamlanan sayı, durum ve neden içerir. Durumlar:
`following`, `waiting_obstacle`, `completed`, `blocked`, `cancelled`, `error`.
Beklerken `obstacle_wait_remaining_sec` geri sayımı da yayınlanır. Web/mobil küçük ve
büyük haritalarında yeşil tamamlanan yolu, sarı aktif hedefi, turkuaz kalan yolu
gösterir. Bitiş/iptal sonrasında rota kalır; yeni kabul edilen rota eskisinin
yerini alır. Oturum kapanışı veya harita kimliği değişimi çizimi temizler.

## Doğrulama ve çalıştırma

Arayüz değişikliği nedeniyle ROS çalışma alanını yeniden derleyin ve çalışan
düğümleri yeniden başlatın:

```bash
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install --packages-select kufibot_interfaces kufibot_navigation kufibot_interaction kufibot_remote
source install/setup.bash
python3 -m pytest -q src/kufibot_navigation/test src/kufibot_interaction/test/test_navigation_tools.py src/kufibot_remote/test/test_route_rendering.py
npm --prefix KufibotMobile run typecheck
```

ROS testlerini fiziksel robot süreçlerinden ayrı `ROS_DOMAIN_ID` ile çalıştırın.
`test_ros_route.py` gerçek action iletişimiyle köşe dönülen iki noktalı yolu,
feedback’i, temiz JPEG’i ve iptali sınar. Deterministik testler ayrıca engel,
bilinmeyen alan, sensör kaybı, toplu doğrulama ve rota değişimini kapsar.
Arayüz testi iki platformda aynı veri için koordinatları ve görünüm ömrünü karşılaştırır.
