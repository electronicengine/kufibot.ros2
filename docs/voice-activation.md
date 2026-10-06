# Uyanma ile sesli ajan

Robot `remote` (uzaktan kumanda) modunda açılır. Ayrı bir bekleme kontrol modu yoktur;
üç mod `remote`, `ai` ve `tools` olarak kalır. `activation` ayarları globaldir; workflow ayarları
bu alanı değiştirmez. Eski `setAiSettings` istemcileri alanı göndermediğinde
robot kayıtlı uyanma ayarını korur. Aktif görüşme boyunca ayarların o oturumun
başlangıcındaki sürümü kullanılır; kaydedilen sürüm sonraki döngüye uygulanır.

`remote` → yerel STT → AI kontrolü onayı → karşılama sesi/mimiği →
sağlayıcı oturumu → normal bitişte kapanış sesi/mimiği → `remote`.
Araç modunda ve workflow testlerinde otomatik tetikleme askıya alınır.

## Kontrol sözleşmesi

- `mode: ai` ve `/voice_session/start` kelime beklemeden karşılama akışını başlatır.
- `stopVoice` ve `/voice_session/stop` normal kapanışı çalıştırır.
- `mode: remote` devam eden karşılama/oturumu iptal eder; kumandada dinleme sürer.
- `voice_session/control_request` otomatik geçişin kimliğini, beklenen modu ve
  kontrol epoch değerini taşır. Merkezi kontrol önce hareketleri sıfırlar,
  arbiter onayını gördükten sonra `control_ack` yayınlar. Yeni bir manuel mod
  seçimi eski isteği geçersiz kılar. `control_state` epoch ve test askısını taşır.
- `VoiceState` alanları değişmez. Sesli ajan alt durumları `preparing`, `wake_listening`,
  `greeting`, `farewell`, `disabled`, `suspended`; sağlayıcı durumları korunur.
  `aiConfig.activation_status` son hata ve bitiş nedenini taşır.
  `last_heard` alanı son tamamlanan STT metnini, `matched` sonucunu ve
  `timestamp_ms` zamanını taşır. Eşleşmeyen konuşmalar da arayüzde görünür;
  oturum transcript geçmişine veya diske eklenmez. Son metin yeni konuşmaya
  kadar kalır, ses düğümü yeniden başlatıldığında temizlenir.

## Kaynak sahipliği

STT worker'ı LLM/embedding yüklemez. Mikrofon süreci tamamen kapandıktan sonra
karşılama başlar. Sabit ses bitiminde yapılandırılmış yankı kuyruğu beklenir;
sonra sağlayıcı mikrofonu açılır. Tersi geçişte sağlayıcı ve tüm ses süreçleri
kapatılmadan yeni dinleyici açılmaz. Her worker ayrı süreç grubundadır;
iptalde kayıt/oynatıcı alt süreçleri de sonlandırılır.

Metin, dil, model dosyası ve model metadata sürümüne göre WAV önbelleği tutulur.
Statik mimikler kitaplıktan kimlikle seçilir; anlamsal embedding kullanılmaz.
Vosk sözlüğünde bulunmayan ifadeler otomatik seçimde kurulu Hailo Whisper'a
aktarılır. Açık Vosk seçiminde kullanıcıya başka ifade/model seçmesi bildirilir.

## Doğrulama ve donanım sınırları

`test_voice_activation.py` eşleşme, iptal, tek oturum, bitiş sırası, stale olaylar,
kontrol devri ve test askısını kapsar. Ayar, web ve ROS regresyon testleri de
mevcut test paketlerindedir. Mobil ve workflow arayüzleri TypeScript ile kontrol edilir.

[Pi ölçümü](benchmarks/voice-activation-pi-2026-09-22.json) gerçek yerel modellerle
üretilmiş “Merhaba Kufi” sesini kullanır; hoparlör çalınmamıştır. Türkçe Vosk
sözlüğünde `kufi` yoktur; otomatik Whisper yolu ifadeyi eşleştirmiştir. Ölçüm,
ortam gürültüsü altında insan sesi veya uçtan uca sağlayıcı gecikmesi testi değildir.
Gerçek mikrofonla 5 saniyelik bekleme örneğinde worker yaklaşık tek çekirdeğin
%2,58'ini ve 239 MiB RAM kullandı; ses veya transcript saklanmadı. Bu kısa örnek,
uzun süreli yanlış tetikleme oranını ölçmez. Canlı Verasist için bu geliştirme
oturumunda kimlik bilgileri mevcut değildir.

İlgisiz `test_websocket_tools_with_ros` navigasyon testi, servo başlangıcını
bekledikten sonra `goto` aşamasında `range_reference_changed` ile başarısızdır.
Aynı sonuç değişiklik öncesi Control, RemoteController ve ServoArbiter kodlarıyla
ve eski `remote` açılış varsayılanıyla da tekrarlandı. Sesli ajan ROS testleri
sağlayıcı/ses adaptörlerini taklit eder; gerçek kontrol/arbiter mesajlarını kullanır.

Robot kabul denemesi: iki sağlayıcıyla gerçek insan sesi kullanarak açılış,
kumandada tetikleme, karşılama sırasında manuel iptal, süre aşımı, normal kapanış,
yankıyla yanlış tetiklenmeme ve mikrofon çıkarma/takmayı doğrulayın. Aynı anda
iki kayıt süreci bulunmadığını ve hareketin AI onayından önce durduğunu gözleyin.
