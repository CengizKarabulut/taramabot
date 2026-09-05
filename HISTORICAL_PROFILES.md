# Hisse Bazli Tarihsel Profiller

Bu katman, canli A-I taramasinda bulunan bir hissenin ayni kod ve zaman dilimindeki gecmis davranisini ozetler. Amaci yeni bir AL/SAT sistemi uretmek degil; canli tarama sonucuna tarihsel baglam eklemektir.

## Telegram akisi

Taramada yeni hisse varsa normal tarama sonucu korunur. Tarihsel profil mesaji ayri gonderilir ve kullaniciya yalniz dis kodlar gosterilir: `A, B, C, D, E, F, G, H, I`. Ic strateji adlari Telegram mesajinda kullanilmaz.

Bir kart su bilgileri verir:

- Hisse, A-I kodu ve zaman dilimi.
- Tarihsel kalite puani (0-100) ve kalite etiketi.
- Olgunlasmis bagimsiz sinyal olayi sayisi.
- Son uc yildaki sinyal olayi sayisi.
- Zaman dilimine uygun ana ileri ufukta maliyet sonrasi pozitif olay orani.
- Medyan net ileri getiri.
- Ortalama MFE ve MAE.
- Son 10 olgun olaydan kacinin maliyet sonrasi pozitif oldugu.
- Yeterli orneklem varsa hissenin A-I x zaman dilimi kombinasyonlari icindeki sirasi.
- Hissenin yeterli ornekleme sahip en guclu tarihsel kod/zaman dilimi kombinasyonu.

## Olcum kurali

- Sinyal, uretim tarayicisinin A-I kosullariyla ayni parity motorundan uretilir.
- Ardisik mumlarda ayni kosul devam ediyorsa her mum ayri olay sayilmaz; yeni baslayan sinyal episode'u kullanilir.
- Giris referansi sinyal mumundan sonraki mumun acilisidir. Bu, gelecegi gorerek giris yapilmasini engeller.
- `1, 2, 4, 8, 16, 32` bar ileri kapanis getirisi, MFE ve MAE hesaplanir.
- Basit maliyet kontrolu icin tur basi toplam `%0,20` surtunme ileri getiriden dusulur.
- Henuz ileri ufku tamamlanmamis son sinyal performans hesabina girmez; dolayisiyla canli sinyal kendi tarihsel puanini gelecekteki veriyle etkileyemez.

## Ana ileri ufuk

| Zaman dilimi | Ana ufuk |
| --- | ---: |
| 15m | 16 bar |
| 30m | 8 bar |
| 45m | 8 bar |
| 1H | 8 bar |
| 2H | 4 bar |
| 4H | 4 bar |
| 1D | 8 bar |
| 1W | 4 bar |
| 1M | 2 bar |

## Veri kaynagi

- `15m`: kalici derin 15m arsivi.
- `30m, 45m, 1H, 2H, 4H`: derin 15m arsivinden canli uretimde kullanilan ayni BIST resampler'i ile turetilir.
- `1D`: kalici derin gunluk arsiv.
- `1W`: derin 1D arsivinden takvimsel olarak turetilir.
- `1M`: uzun hareketli ortalamalar icin gereken derinligi korumak amaciyla kalici derin 1M arsivi.

Bu ayrim, canli snapshot ile tarihsel profil arasinda zaman dilimi/bucket farki olusmasini azaltir.

## Kalite ve guven

Puan sadece kazanma oranina bakmaz. Maliyet sonrasi pozitif oran, son donem tutarliligi, kazanc/kayip dengesi, MFE/MAE dengesi ve ortalama net edge birlikte kullanilir. Dusuk orneklemde puan otomatik olarak notr bolgeye yaklastirilir.

Orneklem guveni:

- `<5`: Yetersiz
- `5-9`: Dusuk
- `10-19`: Orta
- `20-39`: Iyi
- `40+`: Yuksek

Hissenin genel `en guclu tarihsel profil` siralamasina girmek icin en az 8 olgun olay gerekir.

## 15 dakika ozel kurali

15m, yapilan OOS ve maliyet testlerinden sonra dogrulanmis bir islem sistemi olarak terfi etmemistir. Bu nedenle 15m kartlari `ERKEN UYARI` olarak etiketlenir. 15m puani ana `en guclu tarihsel profil` siralamasina katilmaz; ayri erken-uyari siralamasinda tutulur.

## Otomasyon

`Historical Symbol Profiles` workflow'u derin arsiv guncellendikten sonra profil artifact'ini yeniden uretir. Canli taramada hesaplama bastan yapilmaz; hazir `historical-symbol-profiles-latest` artifact'i kullanilir. Bu sayede 15 dakikalik tarama dongusu agir tarihsel hesaplamadan ayrilir.

Tarihsel profil betikleri:

- `symbol_historical_profiles.py`
- `monthly_symbol_profiles.py`
- `merge_symbol_profiles.py`
- `historical_profile_sender.py`

Bu metrikler tarihsel betimlemedir; gelecek performansi garanti etmez.
