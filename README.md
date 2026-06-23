# Taramabot


## Guncel Sinyal Kodlari

- `M-1`: MACD Pozitif Kesisim. MACD sinyal cizgisini yukari keser ve MACD pozitif bolgede kalir.
- `S-M-1`: SMI/MACD Momentum. SMI yukari kesisim ve MACD histogram pozitif momentum kosullarini arar.
- `S-M-V-1`: SMI/MACD Guclu Onay. S-M-1 kosullarina MA200 ustu fiyat ve guclu hacim onayi ekler.
- `E-V-1`: EMA Trend + Hacim. Kisa EMA yapisi uzun EMA yapisinin ustundedir ve hacim trendi destekler.
- `R-M-V-1`: RSI + MACD + Hacim. RSI guclenirken MACD yukari kesisim ve hacim artisi birlikte olusur.
- `A-M-V-1`: SMA + MACD + Hacim. SMA 5/8/21 dizilimi, MACD pozitifligi, RSI araligi ve hacim onayini birlestirir.
- `S-M-V-2`: SMI/MACD Full. SMI/MACD al sinyaline MA200 ustu fiyat ve hacim filtresi ekler.
- `S-M-2`: SMI/MACD Erken. SMI ve MACD momentum kesisimlerini temel alan erken sinyaldir.
- `R-V-1`: RSI Momentum. RSI guc bolgesine gecisi ve yukselis momentumunu izler.

Taramabot, TradingView verilerini kullanarak BIST hisseleri, emtialar ve kripto paralar için teknik analiz sinyalleri üreten ve bu sinyalleri Telegram üzerinden anlık olarak paylaşan bir Python botudur. Bot, SMI/MACD ve RSI tabanlı iki farklı strateji ile alım sinyalleri taraması yapar ve tespit edilen sinyallerin grafiklerini otomatik olarak oluşturup gönderir.

## Özellikler

*   **Çift Strateji Taraması:** Stochastic Momentum Index (SMI) / Moving Average Convergence Divergence (MACD) ve Relative Strength Index (RSI) indikatörlerine dayalı iki farklı alım sinyali stratejisi.
*   **Asenkron Çalışma:** `asyncio` ve `async_playwright` kullanarak sembolleri paralel ve hızlı bir şekilde tarar.
*   **Otomatik Grafik Oluşturma:** `mplfinance` kütüphanesi ile sinyal üreten varlıkların teknik analiz grafiklerini otomatik olarak oluşturur.
*   **Telegram Entegrasyonu:** Tespit edilen sinyalleri ve ilgili grafikleri HTML formatında, anlaşılır ve şık mesajlarla Telegram kanalına gönderir.
*   **Zamanlanmış Görevler:** Belirli saat aralıklarında (örneğin, Türkiye saati ile 10:30-18:30 arası her saat başı) otomatik olarak çalışacak şekilde yapılandırılabilir.
*   **Modüler Yapı:** Kolay geliştirme ve bakım için kod, `config`, `indicators`, `scanner`, `screenshot`, `telegram_sender`, `chart_generator` ve `scheduler` gibi modüllere ayrılmıştır.
*   **Durum Yönetimi:** `state.json` dosyası ile daha önce gönderilen sinyalleri takip ederek tekrar eden bildirimleri engeller.

## Kurulum

Botu kurmak ve çalıştırmak için aşağıdaki adımları izleyin.

### 1. Depoyu Klonlayın

```bash
git clone https://github.com/CengizKarabulut/taramabot.git
cd taramabot
```

### 2. Sanal Ortam Oluşturun ve Aktive Edin

Python bağımlılıklarını izole etmek için bir sanal ortam kullanmanız önerilir.

```bash
python3 -m venv venv
source venv/bin/activate  # Linux/macOS
# venv\Scripts\activate    # Windows
```

### 3. Bağımlılıkları Yükleyin

```bash
pip install -r requirements.txt
```

### 4. Playwright Tarayıcılarını Kurun

`playwright` kütüphanesi, TradingView ekran görüntülerini almak için tarayıcı motorlarına ihtiyaç duyar.

```bash
playwright install --with-deps
```

### 5. Ortam Değişkenlerini Ayarlayın

Proje kök dizininde `.env` adında bir dosya oluşturun ve aşağıdaki değişkenleri kendi bilgilerinizle doldurun. `.env.example` dosyasını referans alabilirsiniz.

```ini
# Telegram Bot Token (BotFather'dan alınır)
TG_BOT_TOKEN="YOUR_TELEGRAM_BOT_TOKEN"

# Telegram Sohbet ID (Botunuzla konuşarak öğrenebilirsiniz)
TG_CHAT_ID="YOUR_TELEGRAM_CHAT_ID"

# Telegram konu başlığı ID (forum/topic grupları için opsiyonel)
TG_THREAD_ID="YOUR_TOPIC_THREAD_ID"

# TradingView Kullanıcı Adı (Opsiyonel, giriş yapmak için)
TV_USERNAME="YOUR_TRADINGVIEW_USERNAME"

# TradingView Şifre (Opsiyonel, giriş yapmak için)
TV_PASSWORD="YOUR_TRADINGVIEW_PASSWORD"

# TradingView Kayıtlı Grafik ID (Opsiyonel, belirli bir şablonu kullanmak için)
TV_CHART_ID="YOUR_TRADINGVIEW_CHART_ID"

# Log seviyesi (DEBUG, INFO, WARNING, ERROR, CRITICAL)
LOG_LEVEL="INFO"
```

*   **`TG_BOT_TOKEN`**: Telegram BotFather üzerinden alacağınız bot token'ınız.
*   **`TG_CHAT_ID`**: Botunuzun mesaj göndereceği sohbetin (kanal, grup veya kişisel sohbet) ID'si. Botunuza `/start` yazıp, ardından `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates` adresini ziyaret ederek `chat` objesi içindeki `id` değerini bulabilirsiniz.
*   **`TG_THREAD_ID`**: Telegram forum gruplarında mesajın gideceği konu başlığının ID'si. Konu linkini `https://t.me/c/.../<topic_id>` veya `https://web.telegram.org/a/#-100..._<topic_id>` biçiminde kopyalayıp bu değere verebilir veya direkt sayısal topic ID kullanabilirsiniz. Boş bırakılırsa mesajlar grubun genel alanına gider.
*   **`TV_USERNAME`** ve **`TV_PASSWORD`**: TradingView hesabınızın kullanıcı adı ve şifresi. Bu bilgiler, botun TradingView'e giriş yaparak daha stabil ekran görüntüleri almasını sağlar. İsteğe bağlıdır, boş bırakılırsa anonim olarak denenir ancak bazı özellikler kısıtlanabilir.
*   **`TV_CHART_ID`**: TradingView'de kaydettiğiniz bir grafik şablonunun ID'si. Bu ID'yi kullanarak bot, ekran görüntüsü alırken sizin özel grafik ayarlarınızı kullanabilir. Grafik URL'sinde `tradingview.com/chart/YOUR_CHART_ID/` şeklinde bulunur. İsteğe bağlıdır.
*   **`LOG_LEVEL`**: Botun loglama seviyesini belirler. Geliştirme aşamasında `DEBUG` veya `INFO`, üretimde `WARNING` veya `ERROR` kullanılması önerilir.

### 6. Çalıştırma

Botu iki farklı şekilde çalıştırabilirsiniz:

#### a) Tek Seferlik Tarama (Manuel)

Belirli bir pazar ve zaman dilimi için tek seferlik tarama yapmak isterseniz:

```bash
python main.py scan <ZAMAN_DILIMI> <PAZAR_TIPI>
```

Örnekler:

*   BIST hisseleri için günlük tarama:
    ```bash
    python main.py scan 1D bist
    ```
*   Emtialar için 4 saatlik tarama:
    ```bash
    python main.py scan 4H emtia
    ```
*   Kripto paralar için haftalık tarama:
    ```bash
    python main.py scan 1W kripto
    ```

Desteklenen Zaman Dilimleri: `4H`, `1D`, `1W`
Desteklenen Pazar Tipleri: `bist`, `emtia`, `kripto`

#### b) Zamanlanmış Çalışma (Otomatik)

Botun belirli saatlerde otomatik olarak çalışmasını sağlamak için `main.py` dosyasını argümansız çalıştırın. Bot, `config.py` içinde tanımlanan `SCAN_START_HOUR`, `SCAN_END_HOUR` ve `SCAN_INTERVAL_MINUTES` değerlerine göre çalışacaktır.

```bash
python main.py
```

Bu mod, botu bir sunucuda (örneğin, bir VPS veya bulut ortamında) sürekli çalıştırmak için idealdir. Bot, Türkiye saati ile 10:30'da başlayıp 18:30'da bitecek şekilde her saat başı tarama yapacaktır.

### GitHub Actions ile Otomatik Çalıştırma

Botu GitHub Actions kullanarak otomatik olarak çalıştırmak için `.github/workflows/main.yml` dosyasını kullanabilirsiniz. Bu dosya, botun belirli zamanlarda (Türkiye saati ile 10:30-18:30 arası her saat başı) farklı pazar ve zaman dilimleri için tarama yapmasını sağlar.

**Yapılandırma:**

1.  GitHub deponuzda `Settings -> Secrets and variables -> Actions` bölümüne gidin.
2.  Aşağıdaki repository secret'ları ekleyin:
    *   `TG_BOT_TOKEN`
    *   `TG_CHAT_ID`
    *   `TG_THREAD_ID` (Opsiyonel, Telegram konu başlığı için)
    *   `TV_USERNAME` (Opsiyonel)
    *   `TV_PASSWORD` (Opsiyonel)
    *   `TV_CHART_ID` (Opsiyonel)
3.  Workflow'u manuel olarak tetikleyebilir veya zamanlanmış çalıştırmayı bekleyebilirsiniz.

## Modül Açıklamaları

*   **`config.py`**: Botun tüm yapılandırma ayarlarını (API anahtarları, tarama periyotları, indikatör ayarları vb.) içerir.
*   **`indicators.py`**: SMI/MACD ve RSI gibi teknik indikatörlerin hesaplama mantığını ve sinyal kontrol fonksiyonlarını barındırır.
*   **`scanner.py`**: `tvDatafeed` ve `borsapy` kütüphanelerini kullanarak piyasa verilerini çeker, indikatörleri uygular ve alım sinyallerini tespit eder. Asenkron tarama yeteneğine sahiptir.
*   **`screenshot.py`**: `Playwright` kullanarak TradingView grafiklerinin ekran görüntülerini alır. Hata yönetimi ve tekrar deneme mekanizmaları içerir.
*   **`telegram_sender.py`**: Telegram API ile etkileşime girerek metin mesajları ve resimli grafikler gönderir. Mesajları HTML formatında biçimlendirir.
*   **`chart_generator.py`**: `mplfinance` kütüphanesi ile teknik analiz grafiklerini (mum grafikleri, indikatörler) oluşturur ve kaydeder.
*   **`scheduler.py`**: Botun belirli saat aralıklarında otomatik olarak çalışmasını sağlayan zamanlama mantığını içerir.
*   **`main.py`**: Botun ana giriş noktasıdır. Komut satırı argümanlarını işler veya zamanlanmış tarama döngüsünü başlatır.

## Katkıda Bulunma

Projeye katkıda bulunmak isterseniz, lütfen bir `pull request` açmadan önce `issues` bölümünden tartışma başlatın veya mevcut bir `issue` üzerine çalışın.

## Lisans

Bu proje MIT Lisansı altında lisanslanmıştır. Daha fazla bilgi için `LICENSE` dosyasına bakınız.
