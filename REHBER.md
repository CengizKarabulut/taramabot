# Taramabot Bireysel Tarama ve Grup Bağlantı Rehberi

Bu rehber, `taramabot` deposundaki taramaları nasıl ayrı ayrı çalıştıracağınızı ve hepsini tek bir Telegram grubuna nasıl bağlayacağınızı açıklar.

## Yapılan Değişiklikler

1.  **Bireysel Tarayıcılar:** `individual_scanners/` klasörü altında her strateji için ayrı Python scriptleri oluşturuldu:
    *   `scan_smi_macd.py`
    *   `scan_rsi.py`
    *   `scan_new_scan.py`
    *   `scan_rsi_macd.py`
    *   `scan_ema.py`
    *   `scan_macd_cross.py`
2.  **GitHub Actions Güncellemesi:** Taramaların paralel ve bağımsız çalışabilmesi için yeni bir workflow dosyası hazırlandı.

## Nasıl Kullanılır?

### 1. Telegram Grubu Bağlantısı
Tüm taramaların aynı gruba gitmesi için GitHub deponuzun **Settings > Secrets and variables > Actions** kısmındaki `TG_CHAT_ID` değerinin hedef grubun ID'si olduğundan emin olun. Belirli bir Telegram konu başlığına göndermek için `TG_THREAD_ID` değerini de ekleyin. Bu değer konu linkindeki topic ID olabilir; örneğin `https://t.me/c/.../123` linkinde veya `https://web.telegram.org/a/#-100..._123` linkinde `123`.

### 2. GitHub Actions Workflow Dosyasını Eklemek
Güvenlik kısıtlamaları nedeniyle `.github/workflows/individual_scans.yml` dosyasını doğrudan push edemedim. Lütfen şu adımları izleyin:

1.  GitHub deponuzda `.github/workflows/` klasörüne gidin.
2.  `individual_scans.yml` adında yeni bir dosya oluşturun.
3.  Aşağıdaki içeriği kopyalayıp yapıştırın:

```yaml
name: Individual Market Scanners

on:
  schedule:
    - cron: "30 7-15 * * *"
  workflow_dispatch:
    inputs:
      strategy:
        description: 'Çalıştırılacak Strateji'
        required: true
        default: 'all'
        type: choice
        options:
          - all
          - smi_macd
          - rsi
          - new_scan
          - rsi_macd
          - ema
          - macd_cross

jobs:
  scan:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        strategy: [smi_macd, rsi, new_scan, rsi_macd, ema, macd_cross]
    
    steps:
      - name: 📥 Checkout code
        uses: actions/checkout@v4

      - name: 🐍 Setup Python 3.11
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: 'pip'

      - name: 📦 Install dependencies
        run: |
          pip install --upgrade pip
          pip install -r requirements.txt
          playwright install --with-deps chromium

      - name: 🔍 Run Strategy Scan
        if: github.event.inputs.strategy == 'all' || github.event.inputs.strategy == matrix.strategy || github.event_name == 'schedule'
        env:
          TV_USERNAME: ${{ secrets.TV_USERNAME }}
          TV_PASSWORD: ${{ secrets.TV_PASSWORD }}
          TV_CHART_ID: ${{ secrets.TV_CHART_ID }}
          TG_BOT_TOKEN: ${{ secrets.TG_BOT_TOKEN }}
          TG_CHAT_ID: ${{ secrets.TG_CHAT_ID }}
          TG_THREAD_ID: ${{ vars.TG_THREAD_ID || secrets.TG_THREAD_ID }}
        run: |
          echo "🚀 ${{ matrix.strategy }} TARAMASI BAŞLIYOR..."
          python individual_scanners/scan_${{ matrix.strategy }}.py bist

      - name: 📊 Upload state
        uses: actions/upload-artifact@v4
        with:
          name: state-${{ matrix.strategy }}-${{ github.run_number }}
          path: state.json
          retention-days: 1
```

### 3. Manuel Çalıştırma
GitHub Actions sekmesinden "Individual Market Scanners" workflow'unu seçip "Run workflow" diyerek istediğiniz stratejiyi manuel olarak başlatabilirsiniz.

## Önemli Notlar
*   Her tarama kendi `state.json` dosyasını kullanır, böylece sinyaller birbirine karışmaz.
*   Tüm sonuçlar `TG_CHAT_ID` ile belirttiğiniz gruba gönderilir. `TG_THREAD_ID` doluysa sonuçlar ilgili Telegram konu başlığına gider.
