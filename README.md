# ⚡ FastCon

**FastCon**, Türkiye'deki İnternet Servis Sağlayıcılarının (özellikle **Turkcell Superonline**, Türk Telekom, Kablonet vb.) uyguladığı **DPI (Derin Paket İncelemesi / SNI Filtreleme)** ve **DNS Zehirlemesi (DNS Hijacking/Poisoning)** engellerini; uzak bir VPN sunucusuna bağlanmadan, **0 ms ek gecikme (ping)** ve **%0 hız kaybı** ile aşmak için geliştirilmiş açık kaynaklı bir Windows masaüstü uygulamasıdır.

---

## 🎯 Neden FastCon?

| Yöntem | Ping / Hız Etkisi | Masaüstü Uygulama Desteği (Discord Ses & Roblox Player) | Kalıcılık & Güvenlik |
| :--- | :---: | :---: | :--- |
| **Klasik VPN / Tünel** | ❌ Yüksek Ping & Hız Düşüşü | ✅ Var | Tüm trafiği uzak sunucuya yönlendirir |
| **Standart DNS Değişikliği** | ✅ Etkilemez | ❌ Superonline DPI'da Çalışmaz | Windows/İSS tarafından sıkça sıfırlanır veya yakalanır |
| **Hazır İkili (Binary) Araçlar** | ✅ Etkilemez | ✅ Var | ⚠️ Kapalı derleme / Antivirüs (HackTool) uyarıları |
| **⚡ FastCon** | ✅ **0 ms Ek Ping** | ✅ **Tam Destek (UDP Ses & Oyun)** | ✅ **%100 Şeffaf Python Kaynak Kodu & Modern GUI** |

---

## ✨ Temel Özellikler

- **🚀 Sıfır Gecikme (Zero-Latency Handshake Manipulation):** Oyun içi UDP paketleri (Roblox oyun trafiği, Discord WebRTC sesli sohbet ve yayın paketleri) hiçbir vekil sunucuya veya tünele sokulmaz. Yalnızca bağlantıyı başlatan ilk `TLS ClientHello` (SNI) ve `HTTP Host` paketleri işlenir.
- **🛡️ Superonline Özel DPI Aşma Motoru:** Giden TLS el sıkışma paketlerini SNI uzantısı sınırından böler (TCP/TLS Fragmentation), ters sıralı segment gönderimi (Reverse Fragmentation) ve düşük TTL desenkronizasyonu uygular.
- **🔒 Yerel DNS-over-HTTPS (DoH) Çözümleyici:** Windows ağ bağdaştırıcısı ayarlarını kalıcı olarak bozmadan, giden DNS sorgularını şifreli HTTPS (Cloudflare `1.1.1.1` / Google `8.8.8.8`) üzerinden çözer ve önbelleğe (TTL cache) alır.
- **🖥️ Modern Masaüstü Arayüzü & Sistem Tepsisi (System Tray):**
  - Tek tuşla **Başlat / Durdur** kontrolü.
  - **Kapsam Seçimi:** Yalnızca seçili uygulamalar (*Discord & Roblox*) veya tüm engelli siteler.
  - **Canlı Erişim Kontrolü (Health Check):** Discord ve Roblox için anlık DNS + HTTPS erişilebilirlik ve gecikme (ms) testi.
  - **Kullanıcı Kontrollü Otomatik Başlatma:** İsteğe bağlı olarak uygulama içinden açılıp kapatılabilen *"Windows açılışında otomatik başlat"* seçeneği.
  - Saat yanına (System Tray) küçülerek arka planda sessiz çalışma.

---

## 🏗️ Mimari ve Proje Yapısı

```text
FastCon/
├── src/
│   ├── main.py                 # Uygulama giriş noktası ve UAC (Yönetici izni) kontrolü
│   ├── config.py               # İSS profilleri (Superonline/Genel), hedef domain listeleri ve ayarlar
│   ├── autostart.py            # Kullanıcı kontrollü Windows başlangıç yöneticisi
│   ├── engine/
│   │   ├── packet_parser.py    # TLS ClientHello (SNI) & HTTP Host ayrıştırıcı ve TCP parçalayıcı
│   │   ├── doh_resolver.py     # Yerel önbellekli DNS-over-HTTPS (DoH) çözümleyici
│   │   └── dpi_service.py      # pydivert (WinDivert) tabanlı arka plan paket işleme servisi
│   ├── diagnostics/
│   │   └── health_check.py     # Discord ve Roblox canlı bağlantı doğrulama modülü
│   └── ui/
│       ├── app_window.py       # CustomTkinter ana masaüstü penceresi
│       └── tray_icon.py        # Sistem tepsisi (System Tray) ikonu ve hızlı menü
├── tests/                      # Pytest birim ve entegrasyon testleri
└── requirements.txt            # Python bağımlılıkları
```

---

## 🛠️ Kurulum ve Çalıştırma

### Gereksinimler
- **İşletim Sistemi:** Windows 10 / Windows 11 (64-bit)
- **Python:** Python 3.11 veya üzeri
- **Yetki:** Ağ paketlerinin el sıkışma aşamasında yakalanabilmesi için **Yönetici (Administrator)** izni gereklidir.

### Adım Adım Kurulum

1. **Depoyu klonlayın:**
   ```powershell
   git clone https://github.com/zeussback/FastCon.git
   cd FastCon
   ```

2. **Bağımlılıkları yükleyin:**
   ```powershell
   pip install -r requirements.txt
   ```

3. **Uygulamayı başlatın:**
   - Klasördeki **`FastCon.bat`** dosyasına çift tıklayarak (otomatik olarak Windows Yönetici / UAC izni ister ve arka planda siyah konsol penceresi bırakmadan arayüzü açar), **veya**
   - Yönetici PowerShell / Terminal üzerinden:
   ```powershell
   python -m src.main
   ```

---

## 🧪 Test ve Kod Kalitesi

Projedeki paket ayrıştırma, SNI parçalama, DoH çözümleme ve erişim testi modüllerini doğrulamak için:

```powershell
# Birim testlerini kapsam (coverage) raporuyla çalıştır
pytest tests/ -v --cov=src

# Kod stili ve lint kontrolü
ruff check src/ tests/
```

---

## 🔒 Güvenlik ve Şeffaflık İlkeleri

- **Kalıcı Ağ Değişikliği Yoktur:** FastCon durdurulduğunda veya kapatıldığında `WinDivert` tutamacı anında serbest bırakılır; Windows DNS veya proxy ayarlarında hiçbir kalıntı bırakılmaz.
- **Sadece İlk Paket İşlenir:** Veri akışı, şifreler veya ses/oyun trafiği kaydedilmez, uzak bir sunucuya yönlendirilmez.

---

## 📄 Lisans

Bu proje kişisel kullanım ve eğitim amacıyla açık kaynaklı olarak geliştirilmiştir.
