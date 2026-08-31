# Karar Paneli günlük taraması

Bu entegrasyon mevcut Taramabot akışından bağımsızdır.

- Mevcut main.py, telegram_sender.py ve .github/workflows/auto_scan.yml dosyalarını değiştirmez.
- decision_panel_scan.py, BorsaPy ile XUTUM evrenini günlük periyotta tarar.
- Varsayılan minimum puan 70'tir; manuel çalıştırmada 75 seçilebilir.
- Sonuç, depoda zaten tanımlı TG_BOT_TOKEN secret'ı ile gönderilir.
- Hedef grup TG_CHAT_ID, hedef konu TG_THREAD_ID repository variable değerlerinden alınır.
- Tam CSV ve JSON çıktıları GitHub Actions artifact alanında 14 gün saklanır.

## Manuel test

1. GitHub deposunda **Actions** sekmesini açın.
2. **Karar Paneli Günlük BIST Tüm** iş akışını seçin.
3. **Run workflow** düğmesine basın.
4. İlk denemede Minimum toplam puan = 70, grafik sayısı = 0 bırakılabilir.

İş akışı ayrıca hafta içi Türkiye saatiyle 18:20'de otomatik çalışır.
