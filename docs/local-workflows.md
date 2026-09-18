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
Ajan çıkışlarına açıklama verin; model sadece mevcut çıkışları seçebilir.

Başlangıç, Ajan ve Bitiş kutularındaki **Sistem mesajı** okunacak metin değil,
robotun o node'da nasıl davranacağını belirleyen talimattır. Eski `message`
alanı da artık sistem talimatı olarak yorumlanır. Örneğin “Kullanıcıyı selamla
ve adını sor” yazıldığında yalnız modelin ürettiği selamlama/soru seslendirilir.
Talimatı boş Başlangıç doğrudan sonraki node'a geçer; talimatı olan Başlangıç
bir konuşma node'u gibi yanıt verir veya izinli geçişi seçer.

Yanıt vermek mevcut node'da sonraki kullanıcı turunu bekletir. Model bağlantı
açıklamasına göre `transition_node` iç işlemini seçtiğinde hedef node etkin
olur; eski node'un talimatı sistem mesajından çıkar, hedef node'un talimatı
kullanılır. Ortak workflow sistem mesajı korunur. Geçiş kararları testte araç
olayı olarak görünür, TTS'ye gönderilmez. Bitiş talimatı varsa son yanıt modelle
üretilir, ardından oturum biter. Geçiş doğruluğu seçilen modelin yeteneğine
bağlıdır; ufakzeka sınırlamaları aşağıda açıklanmıştır.

**Modeller** panelinden STT, LLM, TTS ve embedding modelini seçin. Türkçe başlangıç
seçimleri Vosk `trRecognizeModel`, `ufakzeka-1-q8_0`, `fettah` ve
`embedding:mxbaiV1` şeklindedir. **Kaydet** taslağı saklar; **Etkinleştir** doğrulanan
sürümü yayınlar ve Local AI ayarlarına uygular. YZ modunda yerel ajan bu sürümü açar.
Sonradan taslak düzenlemek, yayınlanan sürümü değiştirmez. Webde `/#voice`
sayfasında Local AI için yalnız workflow seçilir; **Seçili workflow’u bağla ve
uygula** kayıtlı taslağı doğrulayıp yayınlar. Dil, modeller ve sistem mesajı
workflow editöründe düzenlenir. Eski düz sohbet yolu diğer istemcilerle geriye
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

Koleksiyonu ajana bağladığınızda `search_documents` aracı açılır. Sabit arama için
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
kuyruk üzerinden Piper'a aktarır. Yönlendirme yalnız kontrol kararı üretir;
kontrol JSON'u seslendirilmez. Ölçümler ve sınırlar:
[Workflow gecikme raporu](benchmarks/workflow-stream-2026-09-18.md).

**▷ Test → Test et · Sesli görüşmeyi başlat** taslağı kaydedip etkinleştirir,
ses düğümünün ayar onayını bekler ve robotu otomatik YZ moduna geçirir. Robotun
mikrofonuna konuşun; tarayıcı mikrofonu kullanılmaz. Yerel workflow açıldığında
robot ilk node talimatıyla konuşmayı başlatır. Bu
otomatik açılış kullanıcı konuşması olarak kaydedilmez. Panelde **Sen** ve
**Kufi** mesajları, etkin node ve geçiş olayları görünür. Etkin node canvas'ta
vurgulanır. Son 64 olay taşınır; hızlı geçişler telemetri aralarında kaybolmaz.
**Canlı konuşma** bölümü doğrudan `voice_session/transcript` kanalını web
telemetrisine taşıyan ayrı bir bağlantı kullanır; workflow olaylarına bağlı
değildir. Vosk ara tanıma sonuçları yaklaşık 250 ms aralıklarla aynı **Sen**
balonunda “Dinleniyor…” olarak güncellenir, kesin sonuç onu değiştirir. Batch
STT motorlarında metin cümle çözümlendikten sonra görünür. Workflow robot
yanıtı seslendirilmeden önce **Kufi** balonuna aktarılır. Panel son mesajı takip
eder; geçmişi okumak için yukarı kaydırıldığında otomatik kaydırma durur.
Araç çağrısı, araç sonucu/hatası ve node geçişi de aynı konuşma akışında robot
zaman damgasıyla sıralanır. **Parametreler ve sonuç** ayrıntısından gönderilen
argümanlar ve dönen veri açılır. Geçişlerde node kimliği yerine kutunun adı
gösterilir; koşul ve sabit araç çıkışları da izlenir. Ham olaylar ve belge
kaynakları ayrıca açılabilir **Teknik olay günlüğü ve kaynaklar** bölümündedir.
**Testi durdur**, editörü kapatma veya kontrol bağlantısının kapanması sesli
testi durdurup kumanda moduna döndürür. Hareket aracı içeren sesli testlerde
**Gerçek robot hareketleri** onayı gerekir; simülasyon için metin testini kullanın.

Test paneli gerçek yerel LLM ve belge indeksini kullanır; hareket araçları
**Metin testi gönder** yolunda varsayılan olarak taklit edilir. “Gerçek robot hareketleri” açıkça seçilirse
kumanda modunda mevcut sınırlarla uygulanır. Testler 120 saniye ile sınırlıdır;
yanıttan sonra aynı akışta sonraki metin turu gönderilebilir. Akış değiştiğinde
önce testi durdurun. Sesli görüşme açıkken ikinci LLM testi başlatılmaz.

Ufakzeka-1 küçük bir sohbet modelidir. Bu cihazdaki iki araç seçimi denemesinde
doğru araç çağrısı yapmadı (0/2). Yapısal JSON doğrulaması anlamsal doğruluğu
garanti etmez. Bu modelle dosya/sensör okumasını sabit araç adımlarına bağlayın;
otomatik araç seçimi için seçtiğiniz modeli ayrıca test edin.

Ayrıca ufakzeka, doğru belge verilmesine rağmen kaynakla çelişen temizlik önerisi
üretti. Bu nedenle bu modelde **Belge yanıtı → Otomatik** seçimi en yüksek puanlı
kaynak parçasını doğrudan alıntılar. Kaynak konumu ayrıca gösterilir; JSON alan
yolu sesli yanıtta okunmaz. Ajan node'undan bütün modeller için doğrudan alıntı
veya modelle yanıt üretme seçilebilir. Belge sonuçlarından sonra aynı turda
modelin yeni araç veya geçiş seçmesi engellenir; belge içindeki talimatlar
robot eylemi başlatamaz.

Küçük Türkçe JSON örneğinde `mxbaiV1` üç sorunun üçünde doğru kaydı ilk sırada
buldu. Son kontrolde indeksleme 1,86 s, aramalar 0,99–1,14 s, sabit arama ve
alıntılı yanıt 0,96 s ölçüldü. Embedding ve LLM içeren test sürecinin en yüksek
RSS belleği yaklaşık 935 MiB idi. Bu küçük örnek kapsamlı Türkçe doğruluk ölçümü
veya canlı mikrofon uçtan uca gecikmesi değildir.

Otomatik doğrulamada 82 test geçti: workflow/indeksleme/yetkilendirme ve mevcut
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
