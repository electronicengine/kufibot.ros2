# Local Agent workflow ve bilgi koleksiyonları

Web arayüzünde **☰ Menü → Workflowlar** sayfasından kayıtlı workflow'ları
listeleyin, **Yeni workflow oluştur** veya bir kaydın **Düzenle** düğmesini açın.
Bu giriş sağlayıcı seçiminden bağımsızdır. Alternatif olarak
**Ses Ajanı → Local AI → Local Workflow düzenle** veya mobilde
**Local Workflow düzenle** düğmesini açın. Canvas iki arayüzde aynı paketi kullanır.
Robotun `/workflows` adresi tek başına da açılabilir. Bağımsız sayfa kendi kontrol
bağlantısını kullanır; ana ekran ve mobil içindeki editör mevcut sahipliği paylaşır.

Node ekleyin, bağlantı noktalarını sürükleyin ve node'a tıklayarak ayarlarını açın.
Araç kaynağı ve bilgi koleksiyonu node'larını ajana bağlamak erişim verir;
Araç Adımı ise akış o node'a geldiğinde aracı doğrudan çalıştırır.
Koşul çıkışları `true/false`, araç çıkışları `success/error` olarak bağlanmalıdır.
Ajan çıkışlarına **Geçiş tetikleme ifadeleri** yazın; her satıra örnek bir
kullanıcı cümlesi girin. Araç kaynağı ve bilgi koleksiyonu düğümlerindeki
**Tetikleme ifadeleri** de aynı şekilde çalışır. Boş ifadeler otomatik seçilmez;
araç adlarından, modelden veya kodda tutulan hazır komutlardan tetikleyici türetilmez.

Başlangıç, Ajan ve Bitiş kutularındaki **Düğüm komutu**, düğümün etkin olduğu
her LLM çağrısında ilk `system` mesajı olarak gönderilir. Kullanıcının gerçek
mesajı ayrı bir `user` mesajıdır; düğüme girişte veya geçişte komutla değiştirilmez.
Eski `message` alanı da sistem talimatı olarak yorumlanır. Komut boşsa sistem
mesajı eklenmez. Kullanıcı henüz konuşmamışsa açılış isteğinin `user` içeriği
boş kalır; yapay bir kullanıcı komutu üretilmez.

Geçmişte gerçek kullanıcı mesajları ve asistan yanıtları tutulur. Düğüm
geçişinde sistem mesajı yeni düğümün komutuyla değiştirilir; önceki düğümlerin
komutları geçmişe eklenmez. Ortak workflow talimatı, dil eki, yönlendirme
talimatı ve araç/çıkış şeması eklenmez. Araç sonuçları kullanıcı mesajına veri
olarak eklenir. Bağlam daraldığında önce eski konuşma turları azaltılır;
etkin sistem talimatı ve kullanıcının güncel mesajı kesilmez.

Kullanıcının son cümlesi, seçili embedding modeliyle etkin ajanın çıkış ve bağlı
kaynak ifadeleriyle karşılaştırılır. En yüksek kosinüs benzerliği
`settings.semantic_threshold` değerine eşit veya yüksekse ilgili işlem seçilir.
**Modeller → Anlamsal eşleşme eşiği** varsayılanı `0.70`'tir; bu puan bir olasılık
veya doğruluk yüzdesi değildir. Eşit en yüksek puanlarda işlem seçilmez.
Eşik altında ajan normal sohbete devam eder. Embedding hatasında LLM ile
araç seçimine geri dönülmez; hata görünür olarak bildirilir.

Başlangıç ve ajan düğümlerinde **her girişte en az bir başarılı, boş olmayan LLM
yanıtı** gerekir. İlk kullanıcı mesajı koşula uysa bile bu yanıt alınmadan
başka düğüme geçilmez. Sonraki kullanıcı mesajları geçiş koşullarıyla karşılaştırılır.
Hatalı veya boş LLM yanıtı geçişi açmaz. Aynı düğüme geri dönüldüğünde bu kural
yeniden uygulanır; önceki ziyaretin yanıtı sayılmaz.

Bir kullanıcı turunda en fazla bir anlamsal işlem seçilir. Geçiş seçilirse
hedef düğüm etkinleşir; hedefin komutu `system`, geçişi tetikleyen gerçek
kullanıcı cümlesi `user` olarak birlikte gönderilir. Aynı cümle birden fazla ajan atlatmaz. Geçişler araç çağrısı değildir;
`semantic_match` ve `transition` olaylarıyla izlenir. Araç eşleşmesinde parametreler
kaynak düğümünün **Parametreler (JSON)** alanından gelir; `{"$ref":"user"}` gibi
bağlar desteklenir. LLM araç adı veya parametre üretmez. Bilgi koleksiyonu
eşleşmesinde yalnız eşleşen koleksiyonda kullanıcının cümlesi aranır. Araç ve
belge sonuçları başka anlamsal işlemleri tetiklemez.

Sabit Araç Adımı ve `true/false` koşulları akışa girildiğinde tanımlı işlevlerini
çalıştırır; bu işlem düğümleri konuşma düğümü değildir. Başlangıç düğümü talimatı
boş olsa bile LLM yanıtı üretir. Başlangıç çıkışında koşul yazılmışsa bu koşul
atlanmaz; ilk yanıttan sonraki kullanıcı mesajlarında embedding eşleşmesi beklenir.
Başlangıç çıkışı boşsa ilk LLM yanıtı tamamlandıktan sonra koşulsuz ilerler.
Bitiş düğümü son LLM yanıtını üretir, ardından oturum biter.

**Modeller** panelinden STT, LLM, TTS ve embedding modelini seçin. Türkçe başlangıç
seçimleri Vosk `trRecognizeModel`, `ufakzeka-1-q8_0`, `fettah` ve
`embedding:mxbaiV1` şeklindedir. **Kaydet** taslağı saklar; **Etkinleştir** doğrulanan
sürümü yayınlar ve Local AI ayarlarına uygular. YZ modunda yerel ajan bu sürümü açar.
Sonradan taslak düzenlemek, yayınlanan sürümü değiştirmez. Webde `/#voice`
sayfasında Local AI için yalnız workflow seçilir; **Seçili workflow’u bağla ve
uygula** kayıtlı taslağı doğrulayıp yayınlar. Dil ve modeller workflow editöründe; giriş komutu ilgili düğümde düzenlenir. Eski düz sohbet yolu diğer istemcilerle geriye
uyumluluk için korunur; webde workflow seçmeden Local AI başlatılmaz.

Node silindiğinde ona bağlı çizgiler de kaldırılır. Eski taslaklarda silinmiş
node’a giden bağlantılar varsa editör bunları açılışta uyarıyla temizler;
eksik node’u ekleyip bağlantıları tamamlayın ve taslağı kaydedin.

## Belge yükleme

**Dosyalar** panelinde koleksiyon oluşturun; oluşturma sırasında workflow'un
embedding modeli kullanılır. Koleksiyon modelini sonradan değiştirmek yeniden
indekslemeyi başlatır. PDF/CSV/JSON yükleyin; mobilde sistem dosya seçicisi açılır.
Dosya baytları native HTTP yüklemesiyle aktarılır; WebView köprüsünden geçirilmez.

Metin PDF desteklenir; şifreli veya taranmış PDF için açıklamalı hata gösterilir.
CSV ve JSON UTF-8 olmalıdır; BOM kabul edilir. CSV ayırıcısı otomatik algılanabilir
veya panelden seçilebilir. Önizlemede ilk 20 kayıt ve her kaydın ilk 4000 karakteri
gösterilir. Tam içerik indekslenir; kota aşımında sessiz kesme yapılmaz.

Sınırlar: dosya başına 20 MiB, koleksiyon başına 100 dosya ve 20.000 parça.
İndeksleme durumları `queued`, `indexing`, `ready`, `failed` olarak gösterilir.
Sesli görüşme sırasında yeni indeks parçaları bekler. Yeni sürüm tamamlanmadan
mevcut indeks değiştirilmez. Silinen dosyalar anında yeni aramalardan çıkarılır;
kaynak baytları ve eski indeksler kurtarma için diskte tutulur.

Koleksiyonu ajana bağlayıp tetikleme ifadelerini yazdığınızda eşleşen kullanıcı
cümlesi `search_documents` işlemini başlatır. Sabit arama için
Araç Adımı'nda `search_documents` seçin, izinli koleksiyonu işaretleyin ve parametre
olarak şunu kullanın:

```json
{"query":{"$ref":"user"},"top_k":4}
```

Başarı çıkışını Ajan node'una bağlayın. Ajan talimatı örneği:
“Kullanıcının sorusunu araç sonucundaki belgeye göre Türkçe yanıtla. Bilgi yoksa
bunu belirt.” Hata çıkışına açıklayıcı mesajı olan Bitiş node'u ekleyin.
CSV/JSON araması anlamsal aramadır; kesin toplama veya SQL sorgusu değildir.

## Test ve model sınırlamaları

Workflow ses hattı, yanıtın tamamını beklemek yerine LLM cümlelerini sınırlı
kuyruk üzerinden Piper'a aktarır. Yönlendirme embedding ile yapılır; LLM yalnız seslendirilecek yanıtı üretir. Ölçümler ve sınırlar:
[Workflow gecikme raporu](benchmarks/workflow-stream-2026-09-18.md).

**Test** paneli dört sekmeye ayrılır:

- **Canlı:** Sesli görüşmeyi başlat / sonlandır düğmeleri ve sürekli görünür konuşma alanı.
- **Metin:** Mikrofon ve hoparlör olmadan mesaj gönderme; aynı oturumda düğüm ve konuşma geçmişi korunur.
- **Kayıtlar:** Tamamlanan yerel görüşmeler tarih, workflow adı ve süre sütunlarıyla
  listelenir. **Sonuçları görüntüle** bağlantısı `/workflows?recording=<id>`
  adresinde tam sayfa görüşme sonucunu açar. Geri bağlantısı Kayıtlar sekmesine döner;
  editördeki kaydedilmemiş taslak korunur.
- **Görüşme sonucu:** Kullanıcı ve Kufi seslerini iki ayrı dalga formunda gösterir.
  Dalga üzerinde tıklama/sürükleme, klavyede ok tuşlarıyla beş saniye sarma ve
  Home/End ile başa/sona gitme desteklenir. Oynatma hızı ayarlanabilir; geçmişteki
  zaman düğmeleri ilgili ses konumuna gider. Konuşmalar, düğüm geçişleri ve
  argüman/sonuç/hata detaylarıyla araç çağrıları aynı zaman çizelgesinde görünür.
  Eski WAV kayıtları dinlenebilir; önceden saklanmamış geçmiş geri üretilemez.

- **Olaylar:** Son seçilen canlı/metin oturumunun teknik olayları ve kaynakları.

Yeni kayıtların JSON metadata'sı `schema_version: 2`, görüşme anındaki workflow
adı/düğüm adları ve sesle aynı monotonik saate göre `offset_ms`/`sequence` taşıyan
olayları içerir. Geçici transkriptler ayrı mesaj olarak arşivlenmez. Normal bitişte
ve SIGTERM ile durdurmada kayıt sonlandırılır; metadata atomik yayımlanır.
`GET /api/recordings` hafif özet listesi, `GET /api/recordings/{id}/details`
geçmişi döndürür. `GET /api/recordings/{id}/waveform` kanal başına en fazla 1200
min/max çifti üretir ve `.peaks` dosyasında önbellekler; eski kayıtlar da desteklenir.
`GET /api/recordings/{id}` WAV ve HTTP Range erişimini korur. Tüm kayıt uçları
mevcut kumanda sahipliği ve workflow token yetkilendirmesini kullanır.

**Sesli görüşmeyi başlat** taslağı kaydedip etkinleştirir, ses düğümünün ayar
onayını bekler ve robotu YZ moduna geçirir. Robotun mikrofonuna konuşun;
tarayıcı mikrofonu kullanılmaz. **Sesli görüşmeyi sonlandır**, henüz ayar onayı
bekleyen başlangıcı da iptal eder. Ses oturumu kapanıp kumanda moduna dönüldüğünde
başlat düğmesi yeniden açılır. Panel dışında başlatılmış yerel ses oturumu da
bu düğmeden sonlandırılabilir. Başlangıç hatası panelde görünür ve yeniden
denemeye izin verilir. Gerçek hareket araçları içeren sesli testlerde
**Gerçek robot hareketleri** onayı gerekir.

Canlı konuşma alanı panelin kalan yüksekliğini kullanır. **Genişlet** masaüstünde
paneli büyütür. Kullanıcı ve Kufi mesajları, etkin düğüm, geçişler, anlamsal
puanlar ve araç sonuçları aynı akışta görünür. Kaynak dosya adı, konumu ve
alıntısı ilgili yanıtın altında açılabilir. Yukarı kaydırınca otomatik takip
durur. Vosk ara tanıma sonuçları aynı mesajda güncellenir; kesin sonuç onu
değiştirir. Canlı ve metin konuşmaları birbirine karışmaz.

Metin sekmesinde mesaj yazıp **Metin testi gönder** veya **Ctrl/⌘ + Enter**
kullanın. Yanıt hazırlanırken tekrar gönderme kapatılır; **Metin testini durdur**
ile işlem iptal edilebilir. Sonraki mesaj aynı işçi ve etkin düğümle devam eder.
**Yeni metin oturumu** işçiyi kapatıp konuşmayı temizler; sonraki mesaj başlangıç
düğümünden ilerler. Akış değiştirilmişse sonraki gönderim yeni oturum açar.
Ses oturumu açıkken veya başlatılırken metin testi çalıştırılmaz; önce Canlı
sekmesinden sonlandırın. Kayıtları açmak konuşma alanını veya oturumu sıfırlamaz.

Test paneli gerçek yerel LLM ve belge indeksini kullanır; hareket araçları
**Metin testi gönder** yolunda varsayılan olarak taklit edilir. “Gerçek robot hareketleri” açıkça seçilirse
kumanda modunda mevcut sınırlarla uygulanır. Testler 120 saniye ile sınırlıdır;
yanıttan sonra aynı akışta sonraki metin turu gönderilebilir. Akış değiştiğinde sonraki metin gönderimi yeni test oturumu açar. Sesli görüşme açıkken ikinci LLM testi başlatılmaz.

Araç ve geçiş seçimi artık sohbet modelinden bağımsızdır. Başarı, seçili embedding
modelinin Türkçe anlamsal kalitesine, tetikleme örneklerine ve eşiğe bağlıdır.
Test günlüğü eşleşme puanını, eşiği ve seçilen işlemi gösterir.

Belge yanıtında **Otomatik (modelle yanıtla)** ve **Modelle yanıtla** LLM'i kullanır;
modele göre gizli bir alıntı davranışı uygulanmaz. Açıkça **Doğrudan alıntı**
seçildiğinde en yüksek puanlı kaynak parçası ek bir sabit cümle olmadan okunur.
Ancak düğüme ilk girişte zorunlu LLM yanıtı atlanmaz; alıntı modu sonraki
cevaplarda uygulanır. JSON kaynak yolu seslendirilmez. Sonuç yoksa yanıtı düğüm komutu belirler.

Önceki sürümdeki küçük Türkçe JSON örneğinde `mxbaiV1` üç sorunun üçünde doğru kaydı ilk sırada
buldu. Son kontrolde indeksleme 1,86 s, aramalar 0,99–1,14 s, sabit arama ve
alıntılı yanıt 0,96 s ölçüldü. Embedding ve LLM içeren test sürecinin en yüksek
RSS belleği yaklaşık 935 MiB idi. Bu küçük örnek kapsamlı Türkçe doğruluk ölçümü
veya canlı mikrofon uçtan uca gecikmesi değildir.

Önceki sürümün otomatik doğrulamasında 82 test geçti: workflow/indeksleme/yetkilendirme ve mevcut
Local AI/kumanda regresyonları ile Chromium'da masaüstü ve mobil genişlikte
canvas kontrolleri. Web ve mobil TypeScript kontrolleri ve ROS paket derlemesi
başarılıdır. Fiziksel telefonda native dosya seçimi, gerçek robot hareketleri,
sesli uçtan uca kabul akışı ve indeksleme sırasındaki ses gecikmesi henüz
doğrulanmadı; APK'nın yeniden derlenmesi ve cihaz kabul testi gerekir.

Tekrarlanabilir, ses ve hareket üretmeyen kontrol:

```bash
PYTHONPATH=src/kufibot_interaction .venv/bin/python tools/local_voice/benchmark_workflow.py
```

## Derleme ve depolama

Canvas kaynakları `workflow-ui` içindedir. Bağımlılık sürümleri lock dosyasında
sabitlenmiştir. Derlenmiş JS/CSS ROS paketine dahil edilir; robotta Node.js
çalıştırılması ve kullanım sırasında CDN bağlantısı gerekmez.

```bash
cd workflow-ui
npm ci
npm run typecheck
npm run build
```

Python ortamında `requirements-common.txt` kurulmalıdır (`pypdf` eklendi).
ROS için `kufibot_interaction` ve `kufibot_remote` paketlerini yeniden derleyin.
Mobilde `expo-document-picker` eklendiğinden Android uygulaması/dev client yeniden
derlenmelidir; yalnız JavaScript yenilemesi mevcut APK'ya native modülü eklemez.
Expo 55 için Node.js 20.19.4 veya üzeri kullanın.

Workflow'lar `~/.config/kufibot/workflows/`, yayınlar bunun `published/` dizini,
belgeler ve indeksler `~/.local/share/kufibot/knowledge/` altında tutulur.
`KUFIBOT_WORKFLOW_ROOT` ve `KUFIBOT_KNOWLEDGE_ROOT` test/kurulum köklerini değiştirebilir;
remote ve voice süreçlerine aynı değerler verilmelidir.

Yükleme yetkisi mevcut kontrol bağlantısına bağlı, kısa ömürlü bir Bearer token'dır;
sahiplik kaybında kullanılamaz. Token URL'ye veya kalıcı depolamaya yazılmaz.
Kontrol sahibi değişince yerel workflow durdurulur. Node geçişleri 16, tur başına
araç çağrıları dört, her araç çağrısı 10 saniye ile sınırlıdır.


### Tüm yerel sesli görüşmelerin arşivi

Üst sol menüde **Kayıtlar** (`/#recordings`) tüm tamamlanmış yerel sesli
görüşmeleri listeler: test panelinden başlayan oturumlar, normal ses ajanı
kullanımı ve workflow bağlı olmayan yerel oturumlar aynı arşivdedir.
Arşiv doğrudan `/recordings` adresinden de açılabilir. Liste 100 kayıtla
sınırlanmaz; tarih sırasına göre tüm kayıtları içerir. Kayıt seçimi mevcut
sonuç ekranını açar. Menü içindeki arşiv ana sayfanın bağlantısını ve yetkisini
kullanır; ikinci bir kumanda bağlantısı açmaz. Menü/arşiv gezinmesi devam eden
sesli oturumu durdurmaz; arşivden ayrılınca kayıt oynatıcısı kapanır.


### Workflow → Ayarlar: oturum, VAD ve AEC

Workflow editöründeki **Ayarlar** bölümünde toplam görüşme sınırı (saniye;
0 sınırsız), tek kullanıcı konuşmasının sınırı (1–120 saniye), VAD eşiği,
konuşma sonu sessizliği, asgari konuşma süresi ve ön tampon düzenlenir.
Boş bırakılan alanlar robotun `local_*` varsayılanlarını kullanır. **Robot
varsayılanlarına dön** workflow'a ait ses ayarı geçersiz kılmalarını temizler.
**Kaydet** taslağa yazar; **Etkinleştir** yayınlar ve bağlı aktif ses oturumunu
mevcut etkinleştirme davranışıyla yeni revizyona geçirir. Testten bağımsız
normal yerel ses oturumları da aynı yayınlanmış ayarları kullanır.

Bu alanlar `settings.voice` altında saklanır; sağlayıcı/model ayarlarının
şeması değişmez. Süre sayacı modeller hazır olup kayıt başladığında çalışır;
sessizlik ve model yanıtını bekleme süreleri de sayılır. Worker süre sınırında
kaydı kapatıp normal bitiş bildirir. Worker yerel bir model çağrısında takılırsa
üst süreç iki saniyelik kapanış payından sonra mevcut durdurma yolunu kullanır.

AEC seçenekleri `system` (robotun ses yönlendirmesi), `enabled` (kurulu AEC
mikrofon/hoparlör çifti) ve `disabled` (doğrudan fiziksel aygıtlar) şeklindedir.
AEC seçiliyken gerekli sanal/fiziksel aygıtlar yoksa oturum hata ile başlatılmaz.
Yerel worker, Pulse aygıtlarında `parec`/`pacat`, ALSA aygıtlarında
`arecord`/`aplay` kullanır. PipeWire filtreleri ve Bluetooth referans gecikmesi
ortak ses yapılandırmasında (`tools/setup_voice_aec.py`) kalır; workflow başına
ses hizmetleri yeniden başlatılmaz. Yerel ajan hâlâ sırayla dinler ve konuşur;
AEC seçimi araya girerek konuşma özelliği eklemez.

Kayıt arşivi `/recordings` ve `/recordings/` adreslerinde kendi HTML/JavaScript
başlangıç dosyasından açılır; workflow canvas'ını başlatmaz. Sayfa yanıtları
önbelleğe alınmaz; JavaScript/CSS adresleri içerik sürümü taşır. Frontend
paketlerini birlikte üretmek için `workflow-ui` içinde `npm run build` kullanılır.
