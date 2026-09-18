# Cümle akışıyla erken seslendirme — 12 Eylül 2026

Tamamlanan LLM cümleleri artık yanıtın geri kalanı üretilirken Piper'a aktarılıyor.
Tek bir ALSA oynatma süreci, iki cümlelik sınırlı kuyruk ve ayrı bir Piper thread'i
kullanılıyor. LLM üretimi 3, prompt işleme 4, Piper çıkarımı 1 CPU thread'i kullanır.

Pi 5 üzerinde gerçek Dolphin GGUF ve Piper ile her dilde iki eşleştirilmiş tekrar
çalıştırıldı. Her iki yolda aynı prompt, aynı üç cümlelik yanıt, temperature 0,
seed 17 ve 96 token sınırı kullanıldı. Tekrarların sırası ters çevrildi; LLM
bağlamı her çalıştırmada temizlendi. Piper her iki yolda da 1 thread kullandı.

| Dil | Tam yanıtı bekleyince ilk PCM | Cümle akışıyla ilk PCM | Azalma |
|---|---:|---:|---:|
| İngilizce | 6.434 s | 3.950 s | %38.6 |
| Türkçe | 13.533 s | 8.478 s | %37.4 |

Süreler LLM çağrısı başlangıcından ALSA'ya ilk ses verisinin yazılmasına kadar
ölçülen medyanlardır. Dört akış çalıştırmasının tamamında ses, `llm_end` olayından
önce başladı. Çıktı metinlerinin aynı olduğu otomatik olarak doğrulandı.

CPU paylaşımı nedeniyle LLM'nin tamamlanma süresi İngilizcede 6.010 → 6.672 s,
Türkçede 12.323 → 14.177 s oldu. Buna rağmen tüm sentezin bitmesi İngilizcede
7.607 → 7.326 s, Türkçede 16.161 → 15.627 s sürdü. Temel kazanç, kullanıcının
ilk yanıtı daha erken duymaya başlamasıdır.

Ölçüm ALSA `null` aygıtıyla yapıldı; fiziksel hoparlör tampon gecikmesini,
STT süresini ve arka plandaki gerçek ROS/kamera yükünü içermez. İki tekrar genel
performans garantisi oluşturmaz. Bir cümleden oluşan veya noktalamasız yanıtlar
aynı oranda hızlanmayabilir.

[Ham sonuçlar ve üretilen metinler](local-voice-sentence-stream-2026-09-12.json)
ile `tools/local_voice/benchmark_sentence_stream.py` karşılaştırmayı yeniden
çalıştırmak için kullanılabilir.

Regresyon testleri ilk cümlenin LLM bitmeden tüketilmesini, cümle sırasını,
kısaltma/ondalık/alıntı sınırlarını, noktalamasız son parçayı, boş yanıtı,
kuyruk sınırını, LLM/Piper hatasında iptali ve tek oynatma sürecini kapsar.
