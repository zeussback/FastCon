# ⚡ FastCon

**FastCon**, İnternet Servis Sağlayıcılarının uyguladığı **DPI (Derin Paket İncelemesi / SNI Filtreleme)** ve **DNS Zehirlemesi (DNS Hijacking/Poisoning)** engellerini; uzak bir VPN sunucusuna bağlanmadan, **0 ms ek gecikme (ping)** ve **%0 hız kaybı** ile aşmak için geliştirilmiş açık kaynaklı bir Windows masaüstü uygulamasıdır.

---

## 🎯 Neden FastCon?

| Yöntem | Ping / Hız Etkisi | Masaüstü Uygulama Desteği (Discord Ses & Roblox Player) | Kalıcılık & Güvenlik |
| :--- | :---: | :---: | :--- |
| **Klasik VPN / Tünel** | ❌ Yüksek Ping & Hız Düşüşü | ✅ Var | Tüm trafiği uzak sunucuya yönlendirir |
| **Standart DNS Değişikliği** | ✅ Etkilemez | ❌ Derin Paket İncelemesinde (DPI) Çalışmaz | Windows/İSS tarafından sıkça sıfırlanır veya yakalanır |
| **Hazır İkili (Binary) Araçlar** | ✅ Etkilemez | ✅ Var | ⚠️ Kapalı derleme / Antivirüs (HackTool) uyarıları |
| **⚡ FastCon** | ✅ **0 ms Ek Ping** | ✅ **Tam Destek (UDP Ses & Oyun)** | ✅ **%100 Şeffaf Python Kaynak Kodu & Modern GUI** |

---

## ✨ Temel Özellikler

- **🚀 Sıfır Gecikme (Zero-Latency Handshake Manipulation):** Oyun içi UDP paketleri (Roblox oyun trafiği, Discord WebRTC sesli sohbet ve yayın paketleri) hiçbir vekil sunucuya veya tünele sokulmaz. Yalnızca bağlantıyı başlatan ilk `TLS ClientHello` (SNI) ve `HTTP Host` paketleri işlenir.
- **🛡️ Çok Katmanlı DPI Aşma Motoru:** Giden TLS el sıkışma paketlerini RFC 5246 TLS Record katmanında ve TCP segment düzeyinde SNI uzantısı sınırından böler (`Standart Mod`, `Derin Paket Aşma`, `Hafif Mod`).
- **🔒 Yerel DNS-over-HTTPS (DoH) Çözümleyici:** Windows ağ bağdaştırıcısı ayarlarını kalıcı olarak bozmadan, giden DNS sorgularını şifreli HTTPS (Cloudflare `1.1.1.1` / Google `8.8.8.8` / Quad9 `9.9.9.9`) üzerinden çözer ve önbelleğe (TTL cache) alır.
- **🖥️ Modern Monokrom Arayüz & İki Sekmeli Tasarım (`Ana Ekran` / `Ayarlar`):**
  - **Ana Ekran:** Canlı sonar animasyonlu **BAŞLAT / DURDUR** güç butonu, Discord ve Roblox için anlık erişim ve gecikme (`ms`) testi (**Şimdi Test Et**) ve **Bağlantı Profili** seçici.
  - **Ayarlar Sekmesi:** Kapsam seçimi (*Sadece Seçili Uygulamalar* / *Tüm Siteler*), hedef alan adı düzenleyici, şifreli DNS (DoH) sağlayıcı seçimi, DNS önbellek temizleme, sistem tepsisi (System Tray) ve Windows başlangıçta otomatik çalışma tercihleri.

---

## 🏗️ Mimari ve Proje Yapısı

```text
FastCon/
├── src/
│   ├── main.py                 # Uygulama giriş noktası ve UAC (Yönetici izni) kontrolü
│   ├── config.py               # Bağlantı profilleri (Standart/Derin/Hafif), domain listeleri ve ayarlar
│   ├── autostart.py            # Kullanıcı kontrollü Windows başlangıç yöneticisi
│   ├── engine/
│   │   ├── packet_parser.py    # TLS ClientHello (SNI) & RFC 5246 Record / TCP parçalayıcı
│   │   ├── tls_proxy.py        # Yerel sıfır gecikmeli TLS Record bölme motoru
│   │   ├── doh_resolver.py     # Yerel önbellekli DNS-over-HTTPS (DoH) çözümleyici
│   │   └── dpi_service.py      # pydivert (WinDivert) tabanlı arka plan paket işleme servisi
│   ├── diagnostics/
│   │   └── health_check.py     # Discord ve Roblox canlı bağlantı ve ping (ms) doğrulama modülü
│   └── ui/
│       ├── app_window.py       # Monokrom koyu temalı, animasyonlu CustomTkinter masaüstü arayüzü
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
