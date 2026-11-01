/**
 * FastCon Desktop Interface — Modern Hardware-Accelerated Frontend Controller
 */

// Application State Cache
let appState = {
  version: "1.1.0",
  isAdmin: false,
  isRunning: false,
  activeProfileId: "standard",
  profiles: [],
  scopeMode: "selected_only",
  targetDomains: [],
  defaultDomains: [],
  dohProvider: "",
  dohProviders: [],
  minimizeToTray: true,
  autostartEnabled: false,
};

let statsPollInterval = null;
let isHealthCheckInFlight = false;

// DOM Elements
const el = (id) => document.getElementById(id);

document.addEventListener("DOMContentLoaded", async () => {
  try {
    await initApp();
  } catch (err) {
    console.error("FastCon UI başlatılırken hata oluştu:", err);
  }
});

/**
 * Fetch initial system state from Python via Eel bridge.
 */
async function initApp() {
  if (typeof eel === "undefined") {
    console.warn("Eel köprüsü bulunamadı (geliştirici önizleme modu)");
    return;
  }

  const state = await eel.get_initial_state()();
  if (!state) return;

  appState = { ...appState, ...state };

  // Set version tag
  el("appVersion").textContent = `v${appState.version}`;

  // Admin banner & Indicator
  if (appState.isAdmin) {
    el("adminBanner").style.display = "none";
    el("sysAdminDot").className = "indicator-dot online";
    el("sysAdminText").textContent = "Yetki: Yönetici yetkisi aktif (WinDivert hazır)";
  } else {
    el("adminBanner").style.display = "flex";
    el("sysAdminDot").className = "indicator-dot warning";
    el("sysAdminText").textContent = "Yetki: Standart kullanıcı (Yönetici izni gerekli)";
    el("headerStatusDot").className = "status-indicator warning";
  }

  // Populate Profiles Dropdown
  const profileSelect = el("profileSelect");
  profileSelect.innerHTML = "";
  appState.profiles.forEach((prof) => {
    const opt = document.createElement("option");
    opt.value = prof.id;
    opt.textContent = prof.display_name;
    if (prof.id === appState.activeProfileId) {
      opt.selected = true;
      el("profileDescription").textContent = prof.description;
    }
    profileSelect.appendChild(opt);
  });

  // Populate DoH Dropdown
  const dohSelect = el("dohSelect");
  dohSelect.innerHTML = "";
  appState.dohProviders.forEach((label) => {
    const opt = document.createElement("option");
    opt.value = label;
    opt.textContent = label;
    if (label === appState.dohProvider) {
      opt.selected = true;
    }
    dohSelect.appendChild(opt);
  });

  // Set Scope Segmented Control
  setScopeControlUI(appState.scopeMode);

  // Set Domain Count
  updateDomainCountUI(appState.targetDomains.length);

  // Set Switches
  el("switchMinimizeTray").checked = !!appState.minimizeToTray;
  el("switchAutostart").checked = !!appState.autostartEnabled;

  // Apply Engine Running State
  applyEngineState(appState.isRunning);

  // Apply Initial Health Check Results if available
  if (appState.healthResults && appState.healthResults.length > 0) {
    applyHealthResults(appState.healthResults);
  }

  // Start background stats poller (1 sec)
  startStatsPoller();
}

/**
 * Tab Navigation (Ana Ekran vs Ayarlar)
 */
function switchTab(tabName) {
  const isHome = tabName === "home";
  el("paneHome").classList.toggle("active", isHome);
  el("paneSettings").classList.toggle("active", !isHome);

  el("tabBtnHome").classList.toggle("active", isHome);
  el("tabBtnSettings").classList.toggle("active", !isHome);
}

/**
 * Start or Stop DPI Protection Service
 */
async function toggleEngine() {
  const powerBtn = el("powerBtn");
  powerBtn.disabled = true;

  try {
    const res = await eel.toggle_engine()();
    applyEngineState(res.is_running, res.message);
    if (res.is_running) {
      showToast("Bağlantı koruması başarıyla etkinleştirildi", "success");
      // Run quick health check 600ms after start
      setTimeout(runHealthCheck, 600);
    } else {
      showToast("Bağlantı koruması durduruldu", "info");
    }
  } catch (err) {
    console.error("Motor tetikleme hatası:", err);
    showToast("İşlem sırasında hata oluştu", "error");
  } finally {
    powerBtn.disabled = false;
  }
}

/**
 * Update UI according to Engine Running status
 */
function applyEngineState(isRunning, customMessage = "") {
  appState.isRunning = isRunning;
  const powerWrapper = document.querySelector(".power-orb-wrapper");
  const title = el("heroStatusTitle");
  const sub = el("heroStatusSub");
  const btnText = el("powerBtnText");
  const btnSub = el("powerBtnSub");
  const headerDot = el("headerStatusDot");

  if (isRunning) {
    powerWrapper.classList.add("active");
    title.textContent = "Bağlantı Koruması Aktif";
    title.className = "hero-title active";
    sub.textContent = customMessage || "Oyun ve ses sunucuları için 0 ms sıfır gecikmeli DPI atlatma.";
    btnText.textContent = "DURDUR";
    btnSub.textContent = "● AKTİF";
    headerDot.className = "status-indicator active";
  } else {
    powerWrapper.classList.remove("active");
    title.textContent = "Bağlantı Koruması Kapalı";
    title.className = "hero-title";
    sub.textContent = customMessage || "Başlatmak için butona dokunun — oyun ve ses gecikmesi 0 ms.";
    btnText.textContent = "BAŞLAT";
    btnSub.textContent = "HAZIR";
    headerDot.className = appState.isAdmin ? "status-indicator" : "status-indicator warning";
  }
}

/**
 * Polling real-time packet fragmentation & DNS resolution statistics
 */
function startStatsPoller() {
  if (statsPollInterval) clearInterval(statsPollInterval);

  statsPollInterval = setInterval(async () => {
    if (typeof eel === "undefined") return;
    try {
      const stats = await eel.poll_engine_stats()();
      if (stats) {
        el("statTls").textContent = stats.tls_total;
        el("statDns").textContent = stats.dns_resolved;
        el("statLastDomain").textContent = stats.last_domain || "-";
      }
    } catch (_err) {
      // Ignored during normal app lifecycle
    }
  }, 1000);
}

/**
 * Health Prober (Discord & Roblox)
 */
async function runHealthCheck() {
  if (isHealthCheckInFlight) return;
  isHealthCheckInFlight = true;

  const btn = el("btnTestHealth");
  btn.classList.add("loading");
  btn.disabled = true;
  el("testBtnLabel").textContent = "Ölçülüyor...";

  setServiceRowState("Discord", "measuring", "Ölçülüyor...");
  setServiceRowState("Roblox", "measuring", "Ölçülüyor...");

  try {
    const results = await eel.run_health_check()();
    applyHealthResults(results);
  } catch (err) {
    console.error("Sağlık testi hatası:", err);
    showToast("Erişim testi gerçekleştirilemedi", "error");
  } finally {
    isHealthCheckInFlight = false;
    btn.classList.remove("loading");
    btn.disabled = false;
    el("testBtnLabel").textContent = "Şimdi Test Et";
  }
}

function applyHealthResults(results) {
  if (!results) return;
  results.forEach((item) => {
    let statusClass = "offline";
    if (item.reachable) {
      statusClass = "online";
    } else if (item.is_partial) {
      statusClass = "partial";
    }
    setServiceRowState(item.service_name, statusClass, item.detail);
  });
}

function setServiceRowState(serviceName, status, labelText) {
  const isDiscord = serviceName.toLowerCase().includes("discord");
  const dot = el(isDiscord ? "dotDiscord" : "dotRoblox");
  const badge = el(isDiscord ? "badgeDiscord" : "badgeRoblox");

  if (!dot || !badge) return;

  dot.className = `service-dot ${status}`;
  badge.className = `service-badge ${status}`;
  badge.textContent = labelText;
}

/**
 * Profile Change Handler
 */
async function onProfileChange(newProfileId) {
  try {
    const res = await eel.change_profile(newProfileId)();
    appState.activeProfileId = newProfileId;
    el("profileDescription").textContent = res.description;
    showToast(`Bağlantı profili güncellendi: ${res.display_name}`, "success");
  } catch (err) {
    console.error("Profil güncelleme hatası:", err);
  }
}

/**
 * Scope Mode Change Handler
 */
async function onScopeChange(mode) {
  try {
    await eel.change_scope(mode)();
    appState.scopeMode = mode;
    setScopeControlUI(mode);
    const modeName = mode === "selected_only" ? "Sadece Seçili Uygulamalar" : "Tüm Siteler";
    showToast(`Etki kapsamı değiştirildi: ${modeName}`, "success");
  } catch (err) {
    console.error("Kapsam değiştirme hatası:", err);
  }
}

function setScopeControlUI(mode) {
  const btns = document.querySelectorAll("#scopeSegmented .seg-btn");
  btns.forEach((b) => {
    b.classList.toggle("active", b.getAttribute("data-scope") === mode);
  });
}

/**
 * Domain Editor Modal
 */
function openDomainModal() {
  const textarea = el("domainsTextarea");
  textarea.value = appState.targetDomains.join("\n");
  el("domainModalBackdrop").classList.add("open");
}

function closeDomainModal() {
  el("domainModalBackdrop").classList.remove("open");
}

function resetDomainDefaults() {
  el("domainsTextarea").value = appState.defaultDomains.join("\n");
}

async function saveDomains() {
  const raw = el("domainsTextarea").value;
  const lines = raw
    .split("\n")
    .map((s) => s.trim().toLowerCase())
    .filter((s) => s.length > 0);

  // Deduplicate
  const uniqueDomains = Array.from(new Set(lines));

  try {
    const saved = await eel.save_domains(uniqueDomains)();
    appState.targetDomains = saved;
    updateDomainCountUI(saved.length);
    closeDomainModal();
    showToast(`${saved.length} hedef alan adı kaydedildi`, "success");
  } catch (err) {
    console.error("Alan adları kaydedilemedi:", err);
    showToast("Alan adları kaydedilirken hata oluştu", "error");
  }
}

function updateDomainCountUI(count) {
  el("domainCountText").textContent = `${count} alan adı aktif`;
}

/**
 * DoH Provider Change
 */
async function onDohChange(providerLabel) {
  try {
    await eel.change_doh(providerLabel)();
    appState.dohProvider = providerLabel;
    showToast(`DoH sağlayıcısı değiştirildi: ${providerLabel}`, "success");
  } catch (err) {
    console.error("DoH sağlayıcısı değiştirilemedi:", err);
  }
}

/**
 * Flush Windows & Local DNS Cache
 */
async function flushDns() {
  const btn = el("btnFlushDns");
  btn.disabled = true;
  try {
    await eel.flush_dns()();
    showToast("✓ Windows ve DoH DNS önbelleği temizlendi", "success");
  } catch (err) {
    console.error("DNS temizleme hatası:", err);
  } finally {
    setTimeout(() => {
      btn.disabled = false;
    }, 1500);
  }
}

/**
 * Preferences Toggles
 */
async function onTrayPrefToggle(checked) {
  try {
    await eel.toggle_tray_pref(checked)();
    appState.minimizeToTray = checked;
  } catch (err) {
    console.error("Tepsi ayarı güncellenemedi:", err);
  }
}

async function onAutostartToggle(checked) {
  try {
    const res = await eel.toggle_autostart(checked)();
    appState.autostartEnabled = res.enabled;
    el("switchAutostart").checked = res.enabled;
    showToast(res.message, res.enabled ? "success" : "warning");
  } catch (err) {
    console.error("Başlangıç ayarı güncellenemedi:", err);
  }
}

/**
 * Copy Diagnostic Report to Clipboard
 */
async function copyDiagnosticReport() {
  try {
    const reportText = await eel.get_diagnostic_report()();
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(reportText);
    } else {
      // Fallback
      const ta = document.createElement("textarea");
      ta.value = reportText;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
    }
    showToast("✓ Tanı raporu panoya kopyalandı!", "success");
  } catch (err) {
    console.error("Tanı raporu kopyalanamadı:", err);
    showToast("Rapor kopyalanırken hata oluştu", "error");
  }
}

/**
 * Open Application Log Directory
 */
async function openLogFolder() {
  try {
    await eel.open_log_dir()();
  } catch (err) {
    console.error("Log klasörü açılamadı:", err);
  }
}

/**
 * Trigger Administrator Elevation Relaunch
 */
async function triggerElevate() {
  try {
    await eel.trigger_elevation()();
  } catch (err) {
    console.error("Yükseltme tetiklenemedi:", err);
  }
}

/**
 * Modern Floating Toast Notification
 */
function showToast(message, type = "success") {
  const container = el("toastContainer");
  if (!container) return;

  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;

  let iconSvg = "";
  if (type === "success") {
    iconSvg = `<svg class="toast-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"></polyline></svg>`;
  } else if (type === "warning") {
    iconSvg = `<svg class="toast-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>`;
  } else {
    iconSvg = `<svg class="toast-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>`;
  }

  toast.innerHTML = `${iconSvg}<span>${message}</span>`;
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateY(12px)";
    setTimeout(() => {
      if (toast.parentNode) toast.parentNode.removeChild(toast);
    }, 260);
  }, 2800);
}
