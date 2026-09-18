# Pi 5 yerel ses ölçümü — 12 Eylül 2026

Önerilen akış **Silero VAD + Vosk → llama.cpp → Piper**. Hailo-8L Whisper tiny/base
backend'leri kart üzerinde çalıştırıldı ve isteğe bağlı STT seçenekleri olarak
kuruldu. Bu ölçüm Türkçe için Hailo'ya geçişi desteklemiyor.

Donanım: Raspberry Pi 5 / 8 GB, Hailo-8L / HailoRT 4.20. Yazılım:
llama-cpp-python 0.3.35, ONNX Runtime 1.29.0, Piper 1.8.0, tokenizers 0.21.4.
[Ham sonuçlar, metinler, süreler ve dosya özetleri](local-voice-pi5-2026-09-12.json)
aynı klasördedir.

## STT ve konuşma sonu

Her dilde 6 Piper üretimi sentetik kayıt: kısa komut, temiz konuşma, gürültü,
cümle içi duraksama, uzun konuşma ve sessizlik. Türkçe 53, İngilizce 65 referans
kelime vardır. Bütün backend'ler **aynı WAV dosyalarıyla** çalıştırıldı.

| Akış | Türkçe kelime hatası | İngilizce kelime hatası | TR / EN işlem süresi ÷ ses süresi |
|---|---:|---:|---:|
| Eski Vosk | 2/53 | 1/65 | 0.068 / 0.092 |
| VAD + Vosk | 2/53 | 1/65 | 0.073 / 0.098 |
| VAD + Hailo tiny | 35/53 | 1/65 | 0.384 / 0.224 |
| VAD + Hailo base | 32/53 | 7/65 | 0.322 / 0.261 |

VAD, karşılaştırılabilir 9 kayıt için konuşma bitişini medyanda **333 ms daha
erken** kesinleştirdi. Bu örneklerde Vosk doğruluğu aynı kaldı. VAD küçük bir CPU
maliyeti ekliyor; yararı endpoint kontrolü ve sessizliğin STT/LLM'ye ulaşmamasıdır.
Bütün sessizlik örnekleri metinsiz tamamlandı.

Hailo tiny İngilizcede aynı kelime hata sayısına yaklaşık %44 daha az host CPU
süresiyle ulaştı; ancak toplam STT işlem süresi daha uzundu. Türkçe tiny/base
çıktılarında atlama ve tekrarlar görüldü. Kısa pencere, örtüşme birleştirme ve
son sessizliği kırpma uygulanmasına rağmen doğruluk yeterli değil. Model seçimi
otomatik değiştirilmedi.

## LLM thread karşılaştırması

Dolphin GGUF, 2048 context, 48 token yanıt sınırı, temperature 0, sabit seed.
İki dilde kısa hikâye isteği; her hücre tüm tekrarların medyanıdır.

| Yanıt / prompt thread | Ölçüm sayısı | İlk token | Toplam üretim | CPU süresi |
|---|---:|---:|---:|---:|
| 2 / 2 | 4 | 1.960 s | 10.264 s | 20.438 s |
| 3 / 3 | 4 | 1.351 s | 9.461 s | 28.199 s |
| 4 / 4 | 4 | 1.127 s | 9.475 s | 37.209 s |
| **3 / 4** | 6 | **1.127 s** | **9.149 s** | **28.413 s** |

Bu Pi için varsayılan **3 yanıt / 4 prompt thread** seçildi: 4/4'e göre yaklaşık
%3 daha kısa toplam süre ve %24 daha az CPU süresi. Seçili GGUF değiştirilmedi;
başka modellerde ROS parametreleriyle yeniden ölçülebilir. Küçük örneklem ve
sıcaklık değişimleri nedeniyle bu oranlar genel performans garantisi değildir.

## Bütün worker akışı

Kaydedilmiş ses mikrofon hızında gönderildi; gerçek Vosk, LLM ve Piper çalıştı,
ses ALSA `null` aygıtına yazıldı. Her dilde aynı temiz kayıt üç konuşma turunda
kullanıldı. İstek tek kısa cümlelik yanıttır; yanıt sınırı 48 tokendir. Üretim
varsayılanı olan 160 token sınırıyla veya daha uzun sorularla aynı süre beklenmemelidir.

| Dil | Konuşma sonu → ilk PCM p50 | p95 | Piper başlangıcı → ilk PCM p50 |
|---|---:|---:|---:|
| Türkçe | 2.465 s | 3.336 s | 0.143 s |
| İngilizce | 3.256 s | 3.598 s | 0.284 s |

Model yükleme ayrı ölçüldü: son tekrar çalıştırmasında TR 9.69 s, EN 16.87 s.
Önceki soğuk çalıştırmada toplam yükleme 30.90 s'ye ulaştı; disk/page-cache
etkisi önemlidir. Model yükleme her konuşmada tekrarlanmıyor. Ölçülen konuşma
oturumlarında worker swap kullanımı sıfırdı.

Native yükleme sırasında thread heartbeat'inin durabildiği görüldü ve bağımsız
hafif süreçle değiştirildi. Son çalıştırmada worker aşama heartbeat'leri arasındaki
en büyük boşluk EN 0.752 s, TR 0.753 s altında kaldı. Yardımcı süreç, worker
öldüğünde Linux parent-death sinyaliyle kapanır. Python başlangıcı için ana
ROS düğümü ayrı 30 s üst sınır ve yükleme heartbeat'i uygular.

## Doğrulama sınırları ve yeniden çalıştırma

Bu kayıtlar gerçek kullanıcı sesi değildir; özellikle kısa Türkçe “Dur” kaydı
Vosk'ta da yanlış tanındı. Mikrofon, oda gürültüsü, gerçek hoparlör tamponu veya
eşzamanlı ROS/kamera yükü bu zaman ölçümüne dahil değildir. Dolayısıyla eski ve
yeni tam robot akışı için doğrulanmış uçtan uca hızlanma yüzdesi verilmez.

Kullanılan kayıtlar robotta
`/usr/local/ai.models/benchmarks/local-voice-2026-09-12/manifest.json` altında
saklandı. WAV özetleri ham sonuç dosyasındadır. Başka cihazda sentetik test
üretmek ve gerçek konuşma manifestiyle ölçüm yapmak için
[yerel ses ölçüm araçları](../local-ai.md#tekrarlanabilir-ölçüm) kullanılabilir.

Kaynak önceliği, VAD sınırları, yakalama kapanışı, parça oynatma, worker hatası,
GIL tutan çağrılar sırasında heartbeat, worker ölünce yardımcı sürecin kapanması,
gerçek DDS lease süre aşımı ve eski görsel veriye karşı navigasyon kontrolleri
regresyon testleriyle doğrulandı. Mevcut mimik testi robotun kişiselleştirilmiş
poz dosyalarından etkileniyordu; test açıkça paketlenmiş fixture'ı kullanacak
şekilde sabitlendi, robotun poz verileri değiştirilmedi.
