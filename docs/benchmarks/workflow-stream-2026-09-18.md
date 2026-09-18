# Workflow ses hattı — 18 Eylül 2026

Workflow yanıt yolu, düz Local AI'deki cümle bazlı üretim/seslendirmeyi
kullanmıyordu: tüm yanıt tamamlanıyor, sonra Piper'a tek parça gönderiliyordu.
Araç/çıkış olan node'larda kontrol JSON'u ayrıca kullanılmayan bir `text` alanı
ürettiriyordu. Olay okuyucusu her workflow olayında model kataloğu ve workflow
dosyalarını tarayıp tam telemetriyi yayınlıyordu.

Değişiklikler:

- Yalnız izinli kontrol kararı üretilir; reply kararında yanıt metni üretilmez.
- Yanıt cümleleri, LLM devam ederken mevcut Piper worker'ına aktarılır. İki
  cümlelik sınırlı kuyruk ve tek ALSA oynatma süreci korunur; aynı yanıt ikinci
  kez seslendirilmez. Kontrol JSON'u ve geçişler bu kanala girmez.
- Workflow yanıtı mevcut `local_llm_max_tokens` sınırına uyar (kurulumda 160);
  eski sabit 256 tokenlık yanıt sınırı kaldırılmıştır.
- Belge alıntıları da cümlelere bölünerek seslendirilir.
- Katalog 5 saniyelik önbelleğe alınır; olay kuyruğu 200 ms yayın zamanlayıcısıyla
  aktarılır. LLM/TTS JSONL okuyucusu disk taraması yapmaz.
- Assistant metni 250 ms aralıklarla chat paneline akar. Modeller oturum boyunca
  yüklü kalır; Vosk/VAD ve mevcut donanım thread ayarları değiştirilmedi.

## Ölçüm

Pi üzerindeki gerçek ufakzeka GGUF ve fettah Piper; ALSA `null`, seed 17,
temperature 0, 160 token. Her koşulda aynı 356 karakterlik yanıt. Sıra ikinci
tekrarda ters çevrildi. Bu karşılaştırma **yanıt üretimi → ilk PCM** içindir;
rota kararı, mikrofon/STT, fiziksel hoparlör ve eşzamanlı ROS yükü dahil değildir.

| Yol | İlk PCM, tekrar 1 | İlk PCM, tekrar 2 | Ortalama |
|---|---:|---:|---:|
| Tam yanıtı bekleyen | 2.698 s | 2.686 s | 2.692 s |
| Cümle bazlı akış | 2.252 s | 2.321 s | 2.287 s |

Bu örnekte ilk PCM yaklaşık **%15 erken** başladı. Akışlı iki denemede de ilk
ses LLM tamamlanmadan gönderildi. Toplam işlem ortalaması 7.545 → 7.288 s.
İki tekrar genel gecikme garantisi değildir; tek cümlelik yanıtlar aynı kazancı
sağlamayabilir. Test hoparlörden ses çıkarmaz.

```bash
PYTHONPATH=src/kufibot_interaction .venv/bin/python tools/local_voice/benchmark_workflow_stream.py
```

Türkçe sentetik STT korpusunda Vosk yeniden kontrol edildi: konuşmalı örneklerde
RTF 0.08–0.22; temiz, duraklamalı ve uzun örneklerde sözcük hatası yoktu. Gürültülü
örnekte bir sözcük atlandı; tek kelimelik “Dur” örneği “gör” olarak tanındı.
Bu nedenle VAD sonlandırma süresi körlemesine düşürülmedi ve sesli durdurma
komutu güvenlik garantisi değildir. Mevcut fiziksel/kumanda durdurması korunur.

Canlı ölçümde `workflow_start`, `workflow_route_start/end`, `llm_first_token`,
`tts_start`, `playback_start` ve `workflow_end` olayları kaydedilir.
`tools/local_voice/summarize_metrics.py` rota çağrısı, workflow ilk sesi ve toplam
tur için p50/p95 raporlar. Mikrofon/hoparlörlü uçtan uca ölçüm henüz yapılmadı.
