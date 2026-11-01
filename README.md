# ⚡ FastCon

**FastCon**, İnternet Servis Sağlayıcılarının (İSS) uyguladığı **DPI (Derin Paket İncelemesi / SNI Filtreleme)** ve **DNS Zehirlemesi (DNS Hijacking/Poisoning)** engellerini; uzak bir VPN sunucusuna bağlanmadan, oyun ve ses trafiğine ek gecikme eklemeden aşmak için geliştirilmiş şeffaf ve açık kaynaklı bir Windows masaüstü uygulamasıdır.

---

## 🎯 Neden FastCon?

| Yöntem | Ping / Gecikme | Masaüstü Uygulama Desteği (Discord & Roblox) | Şeffaflık & Güvenlik |
| :--- | :---: | :---: | :--- |
| **Klasik VPN / Tünel** | ❌ Yüksek Ping & Hız Kaybı | ✅ Var | Tüm trafiği uzak sunucuya yönlendirir |
| **Standart DNS Değişikliği** | ✅ Doğrudan İSS Pingi | ❌ DPI Engellerinde Çalışmaz | İSS tarafından yönlendirilir veya sıfırlanır |
| **Hazır Kapalı Derlemeler (.exe)** | ✅ Doğrudan İSS Pingi | ✅ Var | ⚠️ Kaynak kodu denetlenemeyen ikili dosyalar |
| **⚡ FastCon** | ✅ **Doğrudan İSS Pingi** | ✅ **Tam Destek (Web, Ses & Oyun)** | ✅ **%100 Açık Kaynaklı Python Kodu & GPLv3** |

> **Gecikme Notu:** FastCon, oyun sunucusu ve sesli sohbet (UDP) paketlerini herhangi bir tünel veya vekil sunucuya yönlendirmez; bu paketler doğrudan internet çıkışınız üzerinden akar. Yalnızca bağlantıyı başlatan ilk TCP el sıkışması (`TLS ClientHello` ve `HTTP Host`) yerel olarak bölünür (<1 ms işlem süresi).

---

## ✨ Temel Özellikler

- **🚀 Doğrudan İSS Pingi (Surgical Handshake Manipulation):** UDP ses (Discord WebRTC) ve oyun sunucusu paketlerine dokunulmaz. Sadece engelleme uygulanan ilk TLS/HTTP el sıkışma paketleri filtrelenir.
- **🛡️ Çok Katmanlı DPI Aşma Motoru:** Giden TLS el sıkışma paketlerini RFC 5246 TLS Record katmanında ve TCP segment düzeyinde SNI uzantısı sınırından böler (`Standart Mod`, `Derin Paket Aşma`, `Hafif Mod`).
- **🔒 Yerel DNS-over-HTTPS (DoH) Çözümleyici:** Ağ bağdaştırıcısı ayarlarını bozmadan, DNS sorgularını şifreli HTTPS (*Cloudflare*, *Google*, *Quad9*) üzerinden çözer ve yerel TTL önbelleğine alır.
- **🖥️ Modern Monokrom Arayüz & İki Sekmeli Tasarım:**
  - **Ana Ekran:** Canlı animasyonlu **BAŞLAT / DURDUR** güç butonu, Discord ve Roblox için gerçek zamanlı 1-RTT ping ve erişim testi (**Şimdi Test Et**), **Bağlantı Profili** seçimi.
  - **Ayarlar Sekmesi:** Kapsam seçimi (*Sadece Seçili Uygulamalar* / *Tüm Siteler*), hedef alan adı listesi düzenleyici, şifreli DoH sağlayıcı seçimi, tek tıkla DNS önbelleği temizleme, sistem tepsisi (System Tray) ve Windows başlangıçta otomatik çalışma tercihleri.
  - **Tanılama & Destek:** Sorun anında tek tıkla panoya kopyalanabilir **Tanı Raporu** ve dönen log dosyası (`fastcon.log`).
- **🛡️ Çift Örnek Kilidi (Single-Instance Mutex):** Uygulamanın yanlışlıkla iki kez açılarak ağ sürücüsünü çakıştırmasını engeller; ikinci açılışta mevcut pencereyi otomatik öne getirir.

---

## 🏗️ Mimari ve Proje Yapısı

```text
FastCon/
├── assets/
│   └── icon.ico                # Uygulama ve masaüstü kısayol simgesi
├── src/
│   ├── main.py                 # Uygulama giriş noktası, UAC yükseltme ve tek örnek kilidi
│   ├── app_logging.py          # Dönen UTF-8 loglayıcı ve oran sınırlayıcı
│   ├── single_instance.py      # Windows Named Mutex tek örnek denetleyicisi
│   ├── config.py               # Bağlantı profilleri, DoH sağlayıcıları ve ayarlar
│   ├── autostart.py            # Windows başlangıç yöneticisi (schtasks)
│   ├── engine/
│   │   ├── packet_parser.py    # TLS SNI / HTTP Host ayrıştırıcı ve RFC 5246 parçalayıcı
│   │   ├── tls_proxy.py        # Yerel TLS Record bölme ve BTK sinkhole iyileştirme motoru
│   │   ├── doh_resolver.py     # Yerel önbellekli DNS-over-HTTPS (DoH) çözümleyici
│   │   └── dpi_service.py      # WinDivert tabanlı arka plan paket yakalama servisi
│   ├── diagnostics/
│   │   ├── health_check.py     # Discord (Web+Ses) ve Roblox (Web+Oyun) canlı 1-RTT testi
│   │   └── report.py           # Destek için sistem tanı raporu oluşturucu
│   └── ui/
│       ├── app_window.py       # Monokrom CustomTkinter arayüzü ve animasyonlu kontroller
│       └── tray_icon.py        # Sistem tepsisi (System Tray) yöneticisi
├── tests/                      # 41 adet Pytest birim ve entegrasyon testi
├── setup.bat                   # Tek tıkla otomatik sanal ortam ve masaüstü kısayolu kurucu
├── FastCon.bat                 # Tek tıkla arka planda konsolsuz başlatıcı
├── requirements.txt            # Çalışma zamanı bağımlılıkları
└── requirements-dev.txt        # Test ve geliştirme bağımlılıkları
```

---

## 🛠️ Kurulum ve Çalıştırma

### Gereksinimler
- **İşletim Sistemi:** Windows 10 / Windows 11 (64-bit)
- **Python:** Python 3.11 veya üzeri ([python.org](https://www.python.org/downloads/) üzerinden kurarken *"Add python.exe to PATH"* kutucuğunu işaretleyin).
- **Yetki:** Ağ paketlerini el sıkışma katmanında inceleyebilmek için **Yönetici (Administrator)** izni gereklidir (uygulama otomatik olarak UAC onayı ister).

---

### Yöntem 1: Tek Tıkla Kolay Kurulum (Önerilen)

1. Projeyi indirin:
   - GitHub sayfasından yeşil **Code → Download ZIP** seçeneğiyle indirip klasöre çıkartın, **veya**
   - Terminalde: `git clone https://github.com/zeussback/FastCon.git`
2. Klasördeki **`setup.bat`** dosyasına çift tıklayın.  
   *(Sanal ortamı otomatik kurar, kütüphaneleri yükler ve masaüstünüze logolu **FastCon** kısayolu ekler).*
3. Kurulum bitince masaüstündeki **FastCon** simgesine çift tıklayarak kullanmaya başlayabilirsiniz!

---

### Yöntem 2: Manuel Kurulum (Geliştiriciler İçin)

```powershell
# 1. Sanal ortamı oluşturun
python -m venv .venv

# 2. Bağımlılıkları yükleyin
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

# 3. Uygulamayı başlatın
.\.venv\Scripts\python.exe -m src.main
```

---

## 🛡️ Antivirüs ve WinDivert Hakkında

FastCon, ağ paketlerini incelemek için açık kaynaklı **WinDivert (Windows Packet Divert)** sürücüsünü kullanır (GoodbyeDPI ve zapret gibi popüler araçlarla aynı standart sürücüdür).

- **Olası Güvenlik Uyarıları:** Bazı antivirüs yazılımları, paket seviyesinde filtreleme yapan sürücüleri genel olarak *"HackTool / WinDivert"* veya *"Riskware"* olarak etiketleyebilir.
- **Şeffaflık:** FastCon tamamen açık kaynaklı Python kodundan oluşur. İçerisinde kapalı derlenmiş üçüncü taraf yürütülebilir dosya (`.exe`) veya uzak sunucuya veri gönderen kod bulunmaz. İstediğiniz zaman tüm kaynak kodları satır satır inceleyebilirsiniz.
- Antivirüsünüz uyarı verirse FastCon klasörünü veya `WinDivert64.sys` sürücüsünü dışlamalara (whitelist) ekleyebilirsiniz.

---

## ❓ Sorun Giderme ve SSS

### 1. "WinDivert sürücüsü başlatılamadı" hatası alıyorum
- Uygulamanın Yönetici izniyle açıldığından emin olun.
- Windows Defender veya üçüncü taraf antivirüs programınızın `WinDivert64.sys` dosyasını engellemediğini kontrol edin.
- Başka bir DPI aşma programı (GoodbyeDPI, Zapret vb.) çalışıyorsa onu kapatın; iki program aynı anda aynı sürücü tutamacını açamaz.

### 2. Uygulama "Aktif" görünüyor ama Discord / Roblox açılmıyor
1. **Ayarlar** sekmesine gidin ve **DNS Önbelleğini Temizle** butonuna basın.
2. Ana ekrandaki **Bağlantı Profili** seçeneğini *Derin Paket Aşma (Agresif)* olarak değiştirip motoru Durdurup tekrar Başlatın.
3. Tarayıcınızı veya Discord uygulamasını tamamen kapatıp yeniden açın.

### 3. Tanı Raporu Nasıl Alınır?
- **Ayarlar** sekmesinde en alttaki **Tanılama ve Destek** kartına gelin.
- **Tanı Raporunu Kopyala** butonuna basarak sistem bilgisi, profil durumu ve son log satırlarını panoya kopyalayabilir; **Log Klasörünü Aç** butonuna basarak log dosyasına erişebilirsiniz (`%LOCALAPPDATA%\FastCon\logs\fastcon.log`).

---

## 🧪 Testler

```powershell
.\.venv\Scripts\pytest.exe -v
.\.venv\Scripts\ruff.exe check src/ tests/
```

---

## ⚖️ Sorumluluk Reddi (Disclaimer)

Bu yazılım; ağ paket analizi, DPI filtreleme mekanizmalarını araştırma ve eğitim amaçlarıyla geliştirilmiştir. Yazılımın kullanımından doğabilecek hukuki sorumluluk ve yerel mevzuatlara uyum kullanıcının kendisine aittir.

---

## 📄 Lisans

Bu proje **GNU General Public License v3.0 (GPLv3)** kapsamında lisanslanmıştır. Detaylar için [LICENSE](LICENSE) dosyasına göz atabilirsiniz.
