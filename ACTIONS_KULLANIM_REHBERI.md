# GitHub Actions Kullanım Rehberi

Bu repo üretim kullanımı için sadeleştirilmiştir. Actions menüsünde yalnız canlı sistem, veri arşivi, tarihsel profil, manuel test/kurtarma ve sağlık kontrolü için gereken workflow'lar bırakılmıştır.

> Normal günlük kullanımda çoğu workflow'u elle çalıştırmak gerekmez. Otomatik akış kendi kendine çalışır.

## 1. StockMarketLab 15 Dakika Snapshot Taramasi

**Dosya:** `.github/workflows/cached_continuous_scan.yml`

Ana üretim workflow'udur.

- Hafta içi canlı BIST/XUTUM taramasını çalıştırır.
- Snapshot verisini artımlı günceller.
- 15m, 30m, 45m, 1H, 2H, 4H, 1D, 1W ve 1M zaman dilimlerini tarar.
- A-I + KARAR ailesini hesaplar.
- Telegram tarama görsellerini ve çoklu sinyal özetini gönderir.
- State ve güncel market-data artifact'ını saklar.

### Manuel çalıştırma seçenekleri

Actions > **StockMarketLab 15 Dakika Snapshot Taramasi** > **Run workflow**

- `intraday_once`: Veriyi artımlı yeniler ve bir kez tarar. En güvenli manuel canlı test seçeneğidir.
- `intraday_chain`: Gün içi 15 dakikalık zinciri başlatır/devam ettirir. Normalde otomatik akış zaten bunu yapar.
- `scan_only`: Yeni veri çekmeden mevcut son snapshot üzerinde tarama yapar.
- `full_refresh`: İlk kurulum/kurtarma amaçlı tam snapshot yenilemesidir. Günlük kullanım için değildir.
- `send_telegram=true`: Manuel tarama sonucunu Telegram'a gönderir.

**Ne zaman elle kullanılır?** Canlı akışın o an çalışıp çalışmadığını görmek veya tek seferlik güncel tarama göndermek istediğinde `intraday_once + send_telegram=true` kullan.

---

## 2. Long History Incremental Archive

**Dosya:** `.github/workflows/historical-incremental-archive.yml`

Kalıcı uzun geçmiş veri arşividir.

- Hafta içi Türkiye saatiyle yaklaşık **20:15** otomatik çalışır.
- Eski geçmişi silmez; yalnız yeni/yakın mumları ekler/günceller.
- Dokuz timeframe için ayrı uzun-geçmiş artifact'ları tutar.
- Tarihsel başarı profillerinin veri kaynağıdır.

**Ne zaman elle kullanılır?** Akşam otomatik koşu kaçırıldıysa, uzun geçmiş artifact'ı bozulduysa veya özellikle geçmiş veriyi hemen güncellemek istiyorsan.

Normalde **elle çalıştırma**.

---

## 3. Historical Symbol Profiles

**Dosya:** `.github/workflows/historical-symbol-profiles.yml`

Hisse bazlı tarihsel başarı motorudur.

- Long History Incremental Archive başarıyla tamamlanınca otomatik tetiklenir.
- Hisse × A-I tarama × timeframe geçmiş performans profillerini yeniden hesaplar.
- Olay sayısı, net pozitif oranı, medyan net getiri, MFE, MAE, son dönem tutarlılığı ve kalite puanlarını üretir.
- Her hissenin en güçlü tarihsel profilini belirler.
- `historical-symbol-profiles-latest` artifact'ını yayımlar.

**Ne zaman elle kullanılır?** Profil puanlama/yorum kodunda değişiklik yaptıysan veya son profil artifact'ını yeniden üretmek gerekiyorsa.

Normalde **elle çalıştırma**; uzun geçmişten sonra otomatik gelir.

---

## 4. Historical Profile Follow-up

**Dosya:** `.github/workflows/historical-profile-followup.yml`

Tarama sonrası tarihsel profil mesajlarını Telegram'a gönderen otomatik takip workflow'udur.

- Ana canlı tarama veya Manual Telegram Snapshot Scan başarıyla bitince otomatik tetiklenir.
- O turdaki gerçek tarama sonuçlarını okur.
- Yalnız taramada çıkan hisseler için tarihsel profil + analist değerlendirmesi gönderir.

**Kullanıcı tarafından çalıştırılmaz.** Bu workflow'u gördüğünde müdahale etme; ana taramanın otomatik devamıdır.

---

## 5. Manual Telegram Snapshot Scan

**Dosya:** `.github/workflows/manual-telegram-snapshot-scan.yml`

Son kaydedilmiş snapshot'ı kullanarak Telegram gönderimini elle test eder.

- Piyasa verisini baştan çekmez; en son `market-data-latest` artifact'ını kullanır.
- Dokuz timeframe'de A-I + KARAR taramasını çalıştırır.
- Telegram görsellerini/özetlerini gönderir.
- Ardından Historical Profile Follow-up otomatik tetiklenir.

**Ne zaman kullanılır?** Telegram görünümünü, yeni mesaj formatını veya tarama gönderimini test etmek istediğinde.

> Güncel veri de istiyorsan bunun yerine ana workflow'da `intraday_once + send_telegram=true` kullan.

---

## 6. Rebuild Clean Live Snapshot

**Dosya:** `.github/workflows/rebuild-live-snapshot.yml`

Kurtarma/bakım aracıdır.

- Dokuz kalıcı uzun-geçmiş arşivinden temiz bir `market-data-latest` snapshot'ı yeniden kurar.
- Timeframe hizalamasını doğrular.
- Tüm taramaları Telegram/state kullanmadan smoke-test eder.

**Ne zaman kullanılır?** Snapshot'ta sembol fazlalığı, eksik veri, timeframe hizalama problemi veya veri kirlenmesi şüphesi varsa.

Normal günlük kullanımda **çalıştırma**.

---

## 7. Verify Incremental Live Refresh

**Dosya:** `.github/workflows/test-incremental-live-refresh.yml`

Sistemin uçtan uca sağlık kontrolüdür.

Kontrol ettiği başlıca noktalar:

- gerçek artımlı veri yenileme,
- timeframe bütünlüğü,
- dokuz timeframe taramasının çalışması,
- snapshot evreni ile taranan hisse sayısının eşleşmesi,
- 15m production/vector parity,
- doğrulanmış yeni snapshot'ın yayımlanması.

**Ne zaman kullanılır?** Büyük bir kod değişikliğinden sonra, “tüm BIST gerçekten taranıyor mu?”, “veri hizası bozuldu mu?” veya “sistem sağlıklı mı?” sorularına tek testle cevap almak istediğinde.

Günlük olarak çalıştırmaya gerek yoktur.

---

# En pratik kullanım özeti

| İhtiyacın | Çalıştırılacak workflow |
|---|---|
| Hiçbir sorun yok, normal gün | Hiçbir şey; sistem otomatik çalışır |
| Şimdi güncel veriyle bir kez tarama + Telegram | StockMarketLab 15 Dakika Snapshot Taramasi → `intraday_once`, Telegram açık |
| Sadece mevcut snapshot ile Telegram test et | Manual Telegram Snapshot Scan |
| Uzun geçmişi elle güncelle | Long History Incremental Archive |
| Tarihsel başarı puanlarını yeniden hesapla | Historical Symbol Profiles |
| Snapshot bozuk/kirli görünüyor | Rebuild Clean Live Snapshot |
| Sistemin tamamını doğrula | Verify Incremental Live Refresh |
| Historical Profile Follow-up | Elle çalıştırma; otomatik |

## Önemli

Backtest, eski Karar Paneli araştırmaları, 15m kalibrasyonları, timeframe araştırmaları, eski scheduler/heartbeat/watchdog ve eski rapor workflow'ları Actions listesinden kaldırılmıştır. Bunlara ait Python araştırma kodları repoda bırakılmıştır; yani geçmiş geliştirme çalışmaları kaybolmamıştır, yalnız günlük Actions ekranını kalabalıklaştıran girişler temizlenmiştir.
