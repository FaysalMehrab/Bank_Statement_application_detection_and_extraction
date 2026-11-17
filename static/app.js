/**
 * UnderwriteIQ — Enterprise Financial Statement & Underwriting Engine
 * Frontend Client Architecture & Reactive State Management
 */

// Configure PDF.js worker
if (window.pdfjsLib) {
  pdfjsLib.GlobalWorkerOptions.workerSrc =
    "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js";
}

// Global Application State
let providersRegistry = {};
let documentsStore = [];
let currentViewingDoc = null;
let currentPdf = null;
let pdfZoom = 1.15;
let previewObjectUrl = null;
let activeEventSources = {};
let selectedAccountFilter = "all";

const DB_NAME = "universal-underwriter";
const DB_STORE = "source-files";
const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;

// Initialize on DOM load
document.addEventListener("DOMContentLoaded", async () => {
  initializeTheme();
  if (window.lucide) lucide.createIcons();
  
  await loadProviders();
  loadStoredSettings();
  await loadStoredDocuments();
  setupDropZone();
  setupKeyboardShortcuts();
  renderDocumentTree();
  updateDashboardStats();
});

// ===========================================================================
// 0. TOAST NOTIFICATION SYSTEM (Replaces intrusive browser alerts)
// ===========================================================================
function showToast(message, type = "info", duration = 3500) {
  const container = document.getElementById("toastContainer");
  if (!container) return;

  const toast = document.createElement("div");
  const isDark = document.body.classList.contains("theme-dark");
  
  const iconMap = {
    success: `<i data-lucide="check-circle-2" class="w-4 h-4 text-emerald-500 shrink-0"></i>`,
    error: `<i data-lucide="alert-circle" class="w-4 h-4 text-rose-500 shrink-0"></i>`,
    warning: `<i data-lucide="alert-triangle" class="w-4 h-4 text-amber-500 shrink-0"></i>`,
    info: `<i data-lucide="info" class="w-4 h-4 text-brand-500 shrink-0"></i>`
  };

  const borderColors = {
    success: "border-emerald-500/30",
    error: "border-rose-500/30",
    warning: "border-amber-500/30",
    info: "border-brand-500/30"
  };

  toast.className = `pointer-events-auto flex items-center space-x-2.5 px-4 py-3 rounded-xl shadow-xl text-xs font-medium border transition-all duration-200 transform translate-y-2 opacity-0 bg-white dark:bg-slate-800 text-slate-800 dark:text-slate-100 ${borderColors[type] || borderColors.info}`;
  toast.innerHTML = `
    ${iconMap[type] || iconMap.info}
    <span class="leading-snug">${message}</span>
    <button class="ml-2 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 p-0.5" onclick="this.parentElement.remove()">
      <i data-lucide="x" class="w-3.5 h-3.5"></i>
    </button>
  `;

  container.appendChild(toast);
  if (window.lucide) lucide.createIcons();

  // Animate in
  requestAnimationFrame(() => {
    toast.classList.remove("translate-y-2", "opacity-0");
    toast.classList.add("translate-y-0", "opacity-100");
  });

  // Auto-dismiss
  setTimeout(() => {
    toast.classList.add("opacity-0", "translate-y-2");
    setTimeout(() => toast.remove(), 250);
  }, duration);
}

// ===========================================================================
// 1. PROVIDERS & SETTINGS MANAGEMENT
// ===========================================================================
async function loadProviders() {
  try {
    const res = await fetch("/api/providers");
    providersRegistry = await res.json();
    populateModelsDropdown();
  } catch (e) {
    console.error("Failed to load providers:", e);
    showToast("Failed to load AI providers from server", "error");
  }
}

function loadStoredSettings() {
  const provider = sessionStorage.getItem("underwriter_provider") || "openai";
  const model = sessionStorage.getItem("underwriter_model") || "gpt-5.6-terra";
  const key = sessionStorage.getItem("underwriter_key") || "";
  const baseUrl = sessionStorage.getItem("underwriter_base_url") || "https://api.openai.com/v1";
  const effort = sessionStorage.getItem("underwriter_reasoning_effort") || "medium";

  const pEl = document.getElementById("settingProvider");
  const kEl = document.getElementById("settingApiKey");
  const bEl = document.getElementById("settingBaseUrl");

  if (pEl) pEl.value = provider;
  if (kEl) kEl.value = key;
  if (bEl) bEl.value = baseUrl;

  populateModelsDropdown();
  const effortEl = document.getElementById("settingReasoningEffort");
  if (effortEl && [...effortEl.options].some((option) => option.value === effort)) {
    effortEl.value = effort;
  }
  updateHeaderBadge(provider, model, Boolean(key));
}

function populateModelsDropdown() {
  const provEl = document.getElementById("settingProvider");
  if (!provEl) return;
  const prov = provEl.value;
  const cfg = providersRegistry[prov] || { models: [] };
  const sel = document.getElementById("settingModelSelect");
  if (!sel) return;
  sel.innerHTML = "";

  (cfg.models || []).forEach((m) => {
    const opt = document.createElement("option");
    opt.value = m.id;
    opt.textContent = `${m.label} ${m.vision ? "👁️ Vision" : "📝 Text Only"}`;
    sel.appendChild(opt);
  });

  const otherOpt = document.createElement("option");
  otherOpt.value = "other";
  otherOpt.textContent = "Other Model (Specify Custom ID)...";
  sel.appendChild(otherOpt);

  const storedProvider = sessionStorage.getItem("underwriter_provider");
  const storedModel = sessionStorage.getItem("underwriter_model");
  const preferredModel = storedProvider === prov && storedModel ? storedModel : cfg.default_model;
  
  if ([...sel.options].some((option) => option.value === preferredModel)) {
    sel.value = preferredModel;
  } else if (preferredModel) {
    sel.value = "other";
    const customInput = document.getElementById("settingModelCustom");
    if (customInput) customInput.value = preferredModel;
  }
  onModelSelectChanged();

  const urlInput = document.getElementById("settingBaseUrl");
  if (urlInput && cfg.base_url) {
    urlInput.value = cfg.base_url;
  }

  const wrap = document.getElementById("customBaseUrlWrapper");
  if (wrap) {
    wrap.style.display = (prov === "custom" || prov === "openrouter") ? "block" : "none";
  }
}

function onModelSelectChanged() {
  const modelSelect = document.getElementById("settingModelSelect");
  if (!modelSelect) return;
  const isOther = modelSelect.value === "other";
  const customEl = document.getElementById("settingModelCustom");
  if (customEl) customEl.style.display = isOther ? "block" : "none";
  updateReasoningEffortControl();
}

function updateReasoningEffortControl() {
  const provider = document.getElementById("settingProvider")?.value;
  const model = document.getElementById("settingModelSelect")?.value;
  const config = (providersRegistry[provider]?.models || []).find((item) => item.id === model);
  const levels = config?.effort_levels || [];
  const wrapper = document.getElementById("reasoningEffortWrapper");
  const select = document.getElementById("settingReasoningEffort");
  if (!wrapper || !select) return;

  if (!levels.length) {
    wrapper.classList.add("hidden");
    select.innerHTML = "";
    return;
  }

  const saved = sessionStorage.getItem("underwriter_reasoning_effort") || "medium";
  select.innerHTML = "";
  levels.forEach((level) => {
    const option = document.createElement("option");
    option.value = level;
    option.textContent = level === "xhigh" ? "Extra high (slowest, highest depth)" : `${level[0].toUpperCase()}${level.slice(1)}`;
    select.appendChild(option);
  });
  select.value = levels.includes(saved) ? saved : (levels.includes("medium") ? "medium" : levels[0]);
  wrapper.classList.remove("hidden");
}

function onProviderChanged() {
  populateModelsDropdown();
}

function saveSettings() {
  const prov = document.getElementById("settingProvider").value;
  const key = document.getElementById("settingApiKey").value.trim();
  const selModel = document.getElementById("settingModelSelect").value;
  const customModel = document.getElementById("settingModelCustom").value.trim();
  const finalModel = selModel === "other" ? customModel : selModel;
  
  if (!finalModel) {
    showToast("Please select a model or enter a custom model ID", "warning");
    return;
  }
  
  const baseUrl = document.getElementById("settingBaseUrl").value.trim();
  const effort = document.getElementById("settingReasoningEffort").value || "";

  sessionStorage.setItem("underwriter_provider", prov);
  sessionStorage.setItem("underwriter_key", key);
  sessionStorage.setItem("underwriter_model", finalModel);
  sessionStorage.setItem("underwriter_base_url", baseUrl);
  if (effort) sessionStorage.setItem("underwriter_reasoning_effort", effort);
  else sessionStorage.removeItem("underwriter_reasoning_effort");

  updateHeaderBadge(prov, finalModel, Boolean(key));
  closeSettingsModal();
  showToast("API settings saved and active!", "success");
}

function updateHeaderBadge(prov, model, hasKey = true) {
  const b = document.getElementById("activeModelBadge");
  const l = document.getElementById("activeModelLabel");
  if (b && l) {
    b.classList.remove("hidden");
    const cleanName = model.split("/").pop();
    l.textContent = `${prov.toUpperCase()}: ${cleanName}`;
    if (!hasKey) {
      l.textContent = "Configure API Key";
      b.classList.add("border-amber-300", "dark:border-amber-700");
    } else {
      b.classList.remove("border-amber-300", "dark:border-amber-700");
    }
  }
}

async function testConnection() {
  const prov = document.getElementById("settingProvider").value;
  const key = document.getElementById("settingApiKey").value.trim();
  const selModel = document.getElementById("settingModelSelect").value;
  const customModel = document.getElementById("settingModelCustom").value.trim();
  const finalModel = selModel === "other" ? customModel : selModel;
  const baseUrl = document.getElementById("settingBaseUrl").value.trim();
  const effort = document.getElementById("settingReasoningEffort").value || "";
  const alertBox = document.getElementById("connStatusAlert");

  if (!key) {
    showToast("Please enter an API key before testing connection", "warning");
    if (alertBox) {
      alertBox.className = "p-3 rounded-xl text-xs font-medium bg-amber-50 text-amber-800 border border-amber-200 dark:bg-amber-950/60 dark:text-amber-200 dark:border-amber-800";
      alertBox.textContent = "Please enter an API key to test.";
      alertBox.classList.remove("hidden");
    }
    return;
  }

  const btn = document.getElementById("btnTestConn");
  const originalHtml = btn.innerHTML;
  btn.innerHTML = `<i data-lucide="loader-2" class="w-3.5 h-3.5 animate-spin"></i><span>Testing Handshake...</span>`;
  btn.disabled = true;
  if (window.lucide) lucide.createIcons();

  try {
    const res = await fetch("/api/test-connection", {
      method: "POST",
      headers: {
        "x-provider": prov,
        "x-api-key": key,
        "x-model": finalModel,
        "x-base-url": baseUrl,
        "x-reasoning-effort": effort,
      },
    });
    const data = await res.json();
    if (res.ok) {
      if (alertBox) {
        alertBox.className = "p-3 rounded-xl text-xs font-medium bg-emerald-50 text-emerald-800 border border-emerald-200 dark:bg-emerald-950/60 dark:text-emerald-200 dark:border-emerald-800";
        alertBox.textContent = "Handshake Successful: " + (data.message || "Connected to LLM!");
        alertBox.classList.remove("hidden");
      }
      showToast("Connection Successful!", "success");
    } else {
      const msg = data.detail || data.message || "Connection failed";
      if (alertBox) {
        alertBox.className = "p-3 rounded-xl text-xs font-medium bg-rose-50 text-rose-800 border border-rose-200 dark:bg-rose-950/60 dark:text-rose-200 dark:border-rose-800";
        alertBox.textContent = "Handshake Failed: " + msg;
        alertBox.classList.remove("hidden");
      }
      showToast("Handshake Failed", "error");
    }
  } catch (err) {
    if (alertBox) {
      alertBox.className = "p-3 rounded-xl text-xs font-medium bg-rose-50 text-rose-800 border border-rose-200 dark:bg-rose-950/60 dark:text-rose-200 dark:border-rose-800";
      alertBox.textContent = "Network Error: " + err.message;
      alertBox.classList.remove("hidden");
    }
    showToast("Network Error: " + err.message, "error");
  } finally {
    btn.innerHTML = originalHtml;
    btn.disabled = false;
    if (window.lucide) lucide.createIcons();
  }
}

// ===========================================================================
// 2. FILE UPLOAD & LOCAL STORAGE PERSISTENCE
// ===========================================================================
function setupDropZone() {
  const dz = document.getElementById("dropZone");
  const fin = document.getElementById("fileInput");

  if (!dz || !fin) return;

  dz.onclick = () => fin.click();
  fin.onchange = (e) => {
    if (e.target.files && e.target.files.length > 0) {
      handleFiles(e.target.files);
      fin.value = "";
    }
  };

  dz.ondragover = (e) => {
    e.preventDefault();
    dz.classList.add("border-brand-500", "bg-brand-50/20", "scale-[1.005]");
  };
  dz.ondragleave = () => {
    dz.classList.remove("border-brand-500", "bg-brand-50/20", "scale-[1.005]");
  };
  dz.ondrop = (e) => {
    e.preventDefault();
    dz.classList.remove("border-brand-500", "bg-brand-50/20", "scale-[1.005]");
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      handleFiles(e.dataTransfer.files);
    }
  };
}

function handleFiles(files) {
  let acceptedCount = 0;
  Array.from(files).forEach((file) => {
    const fn = (file.name || "").toLowerCase();
    const isAllowedExt = fn.endsWith(".pdf") || fn.endsWith(".png") || fn.endsWith(".jpg") || fn.endsWith(".jpeg");
    const isAllowedMime = ["application/pdf", "image/png", "image/jpeg", "image/jpg"].includes((file.type || "").toLowerCase());

    if ((!isAllowedExt && !isAllowedMime) || file.size > MAX_UPLOAD_BYTES) {
      showToast(`${file.name} was skipped. Allowed: PDF, PNG, JPG < 25MB`, "warning");
      return;
    }
    const docId = "doc_" + Math.random().toString(36).substr(2, 9);
    const newDoc = {
      id: docId,
      name: file.name,
      size: file.size,
      uploadDate: new Date().toISOString(),
      fileObject: file,
      status: "pending",
      result: null,
      warningReason: null,
      progressPct: 0,
      progressMsg: ""
    };
    documentsStore.unshift(newDoc);
    saveSourceFile(docId, file).catch((error) => console.warn("Could not retain source file:", error));
    acceptedCount++;
  });

  if (acceptedCount > 0) {
    showToast(`Added ${acceptedCount} document${acceptedCount > 1 ? "s" : ""} to queue`, "success");
    saveStoredDocuments();
    renderDocumentTree();
    updateDashboardStats();
  }
}

function saveStoredDocuments() {
  try {
    const toSave = documentsStore.map((d) => ({
      id: d.id,
      name: d.name,
      size: d.size,
      uploadDate: d.uploadDate,
      status: d.status,
      result: d.result,
      warningReason: d.warningReason,
    }));
    localStorage.setItem("underwriter_docs", JSON.stringify(toSave));
  } catch (e) {
    console.warn("Storage full or unavailable:", e);
  }
}

async function loadStoredDocuments() {
  try {
    const raw = localStorage.getItem("underwriter_docs");
    if (raw) {
      documentsStore = JSON.parse(raw);
      await Promise.all(
        documentsStore.map(async (doc) => {
          doc.fileObject = await getSourceFile(doc.id);
        })
      );
    }
  } catch (e) {
    documentsStore = [];
  }
}

function clearAllDocs() {
  if (documentsStore.length === 0) {
    showToast("Library is already empty", "info");
    return;
  }
  if (confirm("Are you sure you want to clear all documents from your local library?")) {
    documentsStore = [];
    localStorage.removeItem("underwriter_docs");
    clearSourceFiles().catch((error) => console.warn("Could not clear local source files:", error));
    renderDocumentTree();
    updateDashboardStats();
    showToast("Document library cleared", "info");
  }
}

function openSourceDb() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(DB_STORE);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function sourceDbAction(mode, action) {
  const db = await openSourceDb();
  return new Promise((resolve, reject) => {
    const transaction = db.transaction(DB_STORE, mode);
    const request = action(transaction.objectStore(DB_STORE));
    transaction.oncomplete = () => { db.close(); resolve(request?.result); };
    transaction.onerror = () => { db.close(); reject(transaction.error); };
  });
}

const saveSourceFile = (id, file) => sourceDbAction("readwrite", (store) => store.put(file, id));
const getSourceFile = (id) => sourceDbAction("readonly", (store) => store.get(id));
const deleteSourceFile = (id) => sourceDbAction("readwrite", (store) => store.delete(id));
const clearSourceFiles = () => sourceDbAction("readwrite", (store) => store.clear());

async function deleteDocument(docId) {
  const doc = documentsStore.find((item) => item.id === docId);
  if (!doc) return;
  
  if (activeEventSources[docId]) {
    activeEventSources[docId].close();
    delete activeEventSources[docId];
  }

  documentsStore = documentsStore.filter((item) => item.id !== docId);
  await deleteSourceFile(docId).catch((error) => console.warn("Could not delete source file:", error));
  saveStoredDocuments();
  renderDocumentTree();
  updateDashboardStats();
  showToast(`Deleted ${doc.name}`, "info");
}

// ===========================================================================
// 3. EXECUTIVE METRICS RIBBON (Quick Stats)
// ===========================================================================
function updateDashboardStats() {
  const totalEl = document.getElementById("statTotalDocs");
  const reconciledEl = document.getElementById("statReconciledDocs");
  const reconciledPctEl = document.getElementById("statReconciledPct");
  const warningEl = document.getElementById("statWarningDocs");
  const batchBtn = document.getElementById("batchExtractBtn");

  const total = documentsStore.length;
  const reconciled = documentsStore.filter((d) => d.status === "reconciled").length;
  const warning = documentsStore.filter((d) => d.status === "warning").length;
  const pending = documentsStore.filter((d) => d.status === "pending").length;

  if (totalEl) totalEl.textContent = total;
  if (reconciledEl) reconciledEl.textContent = reconciled;
  if (warningEl) warningEl.textContent = warning;

  if (reconciledPctEl) {
    const extracted = reconciled + warning;
    const pct = extracted > 0 ? Math.round((reconciled / extracted) * 100) : 100;
    reconciledPctEl.textContent = `${pct}% accuracy rate`;
  }

  // Calculate unique extracted accounts across library
  const uniqueAccountsMap = new Map();
  documentsStore.forEach((d) => {
    if (d.status === "reconciled" || d.status === "warning") {
      const ext = d.result?.extractedData;
      if (ext) {
        if (Array.isArray(ext.accounts) && ext.accounts.length > 0) {
          ext.accounts.forEach((acc) => {
            const num = acc.accountNumber || ext.accountNumber || "Unknown";
            const bank = acc.bankName || ext.bankName || "Unknown Bank";
            const holder = acc.accountHolder || ext.accountHolder || "Account Holder";
            const key = `${bank}__${num}`.toLowerCase();
            if (!uniqueAccountsMap.has(key)) {
              uniqueAccountsMap.set(key, { key, bank, accountNumber: num, holder, docId: d.id });
            }
          });
        } else if (ext.bankName || ext.accountNumber) {
          const num = ext.accountNumber || "Unknown";
          const bank = ext.bankName || "Unknown Bank";
          const holder = ext.accountHolder || "Account Holder";
          const key = `${bank}__${num}`.toLowerCase();
          if (!uniqueAccountsMap.has(key)) {
            uniqueAccountsMap.set(key, { key, bank, accountNumber: num, holder, docId: d.id });
          }
        }
      }
    }
  });

  const uniqueAccounts = Array.from(uniqueAccountsMap.values());
  const accountsCountEl = document.getElementById("statUniqueAccounts");
  const accountSelect = document.getElementById("accountFilterSelect");
  const viewAccountBtn = document.getElementById("viewSelectedAccountBtn");

  if (accountsCountEl) {
    accountsCountEl.textContent = `${uniqueAccounts.length} Account${uniqueAccounts.length === 1 ? "" : "s"}`;
  }

  if (accountSelect) {
    const prevVal = selectedAccountFilter || accountSelect.value || "all";
    accountSelect.innerHTML = "";

    const allOpt = document.createElement("option");
    allOpt.value = "all";
    allOpt.textContent = `All Extracted Accounts (${uniqueAccounts.length})`;
    accountSelect.appendChild(allOpt);

    uniqueAccounts.forEach((acc) => {
      const opt = document.createElement("option");
      opt.value = acc.key;
      const masked = acc.accountNumber && acc.accountNumber !== "Unknown" ? `••••${acc.accountNumber.slice(-4)}` : "N/A";
      opt.textContent = `${acc.bank} — ${masked} (${acc.holder})`;
      accountSelect.appendChild(opt);
    });

    if (uniqueAccounts.some((a) => a.key === prevVal)) {
      accountSelect.value = prevVal;
      selectedAccountFilter = prevVal;
      if (viewAccountBtn) viewAccountBtn.classList.remove("hidden");
    } else {
      accountSelect.value = "all";
      selectedAccountFilter = "all";
      if (viewAccountBtn) viewAccountBtn.classList.add("hidden");
    }
  }

  // Toggle batch extract button
  if (batchBtn) {
    if (pending > 0) {
      batchBtn.classList.remove("hidden");
      batchBtn.classList.add("inline-flex");
      batchBtn.querySelector("span").textContent = `Extract All (${pending})`;
    } else {
      batchBtn.classList.add("hidden");
      batchBtn.classList.remove("inline-flex");
    }
  }
}

function filterByAccount(accKey) {
  selectedAccountFilter = accKey;
  const viewAccountBtn = document.getElementById("viewSelectedAccountBtn");
  if (viewAccountBtn) {
    if (accKey && accKey !== "all") {
      viewAccountBtn.classList.remove("hidden");
    } else {
      viewAccountBtn.classList.add("hidden");
    }
  }
  renderDocumentTree();
}

function viewSelectedAccount() {
  if (selectedAccountFilter === "all") return;
  const doc = documentsStore.find((d) => {
    const ext = d.result?.extractedData;
    if (!ext) return false;
    const matchSingle = `${ext.bankName}__${ext.accountNumber}`.toLowerCase() === selectedAccountFilter;
    if (matchSingle) return true;
    if (Array.isArray(ext.accounts)) {
      return ext.accounts.some((a) => `${a.bankName || ext.bankName}__${a.accountNumber}`.toLowerCase() === selectedAccountFilter);
    }
    return false;
  });
  if (doc) {
    viewExtractedData(doc.id);
  } else {
    showToast("No extracted statement found for this account", "info");
  }
}

// ===========================================================================
// 4. DATE-GROUPED RENDERING & DOCUMENT CARDS
// ===========================================================================
function renderDocumentTree() {
  const container = document.getElementById("documentLibrary");
  if (!container) return;

  const searchInput = document.getElementById("searchInput");
  const clearSearchBtn = document.getElementById("clearSearchBtn");
  const statusFilter = document.getElementById("statusFilter");
  
  const search = searchInput ? searchInput.value.toLowerCase().trim() : "";
  if (clearSearchBtn) {
    clearSearchBtn.style.display = search ? "block" : "none";
  }
  const filter = statusFilter ? statusFilter.value : "all";

  container.innerHTML = "";

  const filtered = documentsStore.filter((doc) => {
    const nameMatch = doc.name.toLowerCase().includes(search);
    const bankMatch = doc.result?.extractedData?.bankName?.toLowerCase().includes(search);
    const holderMatch = doc.result?.extractedData?.accountHolder?.toLowerCase().includes(search);
    const matchesSearch = nameMatch || bankMatch || holderMatch;

    if (!matchesSearch) return false;

    // Filter by status
    if (filter === "reconciled" && doc.status !== "reconciled") return false;
    if (filter === "warning" && doc.status !== "warning") return false;
    if (filter === "pending" && doc.status !== "pending") return false;

    // Filter by extracted account dropdown
    if (selectedAccountFilter && selectedAccountFilter !== "all") {
      const ext = doc.result?.extractedData;
      if (!ext) return false;
      const key = `${ext.bankName}__${ext.accountNumber}`.toLowerCase();
      if (key === selectedAccountFilter) return true;
      if (Array.isArray(ext.accounts)) {
        return ext.accounts.some((a) => `${a.bankName || ext.bankName}__${a.accountNumber}`.toLowerCase() === selectedAccountFilter);
      }
      return false;
    }

    return true;
  });

  if (filtered.length === 0) {
    container.innerHTML = `
      <div class="glass-card rounded-2xl p-12 text-center border border-dashed border-slate-300 dark:border-slate-800 shadow-sm space-y-3">
        <div class="w-12 h-12 rounded-xl bg-slate-100 dark:bg-slate-800 text-slate-400 mx-auto flex items-center justify-center">
          <i data-lucide="folder-search" class="w-6 h-6"></i>
        </div>
        <h4 class="text-sm font-semibold text-slate-700 dark:text-slate-200">No documents found</h4>
        <p class="text-xs text-slate-400 max-w-sm mx-auto">
          ${search || filter !== "all" || selectedAccountFilter !== "all" ? "No files match your search criteria. Try resetting filters." : "Upload a bank statement above or load synthetic demo data to test the engine."}
        </p>
        ${!search && filter === "all" && selectedAccountFilter === "all" ? `
          <button onclick="loadSampleDemo()" class="mt-2 inline-flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-brand-50 hover:bg-brand-100 text-brand-700 dark:bg-brand-950 dark:text-brand-300 transition">
            <i data-lucide="sparkles" class="w-3.5 h-3.5 text-amber-500"></i>
            <span>Load Demo Data</span>
          </button>` : ""}
      </div>`;
    if (window.lucide) lucide.createIcons();
    return;
  }

  const now = new Date();
  const todayStr = now.toDateString();
  const yest = new Date(now);
  yest.setDate(yest.getDate() - 1);
  const yestStr = yest.toDateString();

  const groups = {};
  filtered.forEach((doc) => {
    const docDate = new Date(doc.uploadDate || Date.now());
    let label = docDate.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
    if (docDate.toDateString() === todayStr) label = "Today";
    else if (docDate.toDateString() === yestStr) label = "Yesterday";

    if (!groups[label]) groups[label] = [];
    groups[label].push(doc);
  });

  Object.entries(groups).forEach(([dateLabel, docs]) => {
    const groupCard = document.createElement("div");
    groupCard.className = "glass-card rounded-2xl p-4 sm:p-5 shadow-card-light space-y-3";

    groupCard.innerHTML = `
      <div class="flex items-center justify-between text-xs font-bold text-slate-400 uppercase tracking-wider border-b border-slate-100 dark:border-slate-800 pb-2.5">
        <div class="flex items-center space-x-2">
          <i data-lucide="calendar" class="w-3.5 h-3.5 text-brand-500"></i>
          <span>${dateLabel}</span>
        </div>
        <span class="text-[11px] font-mono px-2 py-0.5 rounded-full bg-slate-100 dark:bg-slate-800 text-slate-500 font-semibold">${docs.length} ${docs.length === 1 ? "file" : "files"}</span>
      </div>
      <div class="divide-y divide-slate-100 dark:divide-slate-800 doc-rows-container"></div>
    `;

    container.appendChild(groupCard);
    const listContainer = groupCard.querySelector(".doc-rows-container");

    docs.forEach((doc) => {
      const row = document.createElement("div");
      row.className = "py-3.5 flex flex-col sm:flex-row sm:items-center justify-between gap-3 group/row transition-colors";
      row.id = `doc-row-${doc.id}`;
      row.innerHTML = buildDocRowHTML(doc);
      listContainer.appendChild(row);
    });
  });

  if (window.lucide) lucide.createIcons();
}

function buildDocRowHTML(doc) {
  // Classification & Topology Badges
  const docType = doc.category || doc.documentType || doc.result?.documentType;
  const isConsolidated = doc.isConsolidated !== undefined ? doc.isConsolidated : (doc.result?.isConsolidated || doc.topology === "multi");
  const isPartial = doc.result?.isPartialStatement || doc.result?.reconciliation?.is_partial_statement;
  const coveragePct = doc.result?.coveragePct || doc.result?.reconciliation?.coverage_pct;

  let typeBadges = [];
  if (docType === "bank_statement") {
    typeBadges.push(`
      <span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-blue-50 text-blue-700 dark:bg-blue-950/70 dark:text-blue-300 border border-blue-200/60 dark:border-blue-800 flex items-center space-x-1 shrink-0">
        <i data-lucide="landmark" class="w-3 h-3"></i>
        <span>Bank Statement</span>
      </span>
    `);
    if (isConsolidated) {
      typeBadges.push(`
        <span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-indigo-50 text-indigo-700 dark:bg-indigo-950/70 dark:text-indigo-300 border border-indigo-200/60 dark:border-indigo-800 flex items-center space-x-1 shrink-0">
          <i data-lucide="layers" class="w-3 h-3"></i>
          <span>Consolidated (Multi-Account)</span>
        </span>
      `);
    } else {
      typeBadges.push(`
        <span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-teal-50 text-teal-700 dark:bg-teal-950/70 dark:text-teal-300 border border-teal-200/60 dark:border-teal-800 flex items-center space-x-1 shrink-0">
          <i data-lucide="user-check" class="w-3 h-3"></i>
          <span>Single Account</span>
        </span>
      `);
    }
  } else if (docType === "application_form") {
    typeBadges.push(`
      <span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-purple-50 text-purple-700 dark:bg-purple-950/70 dark:text-purple-300 border border-purple-200/60 dark:border-purple-800 flex items-center space-x-1 shrink-0">
        <i data-lucide="file-check" class="w-3 h-3"></i>
        <span>Application Form</span>
      </span>
    `);
  } else if (docType === "voided_check") {
    typeBadges.push(`
      <span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-50 text-emerald-700 dark:bg-emerald-950/70 dark:text-emerald-300 border border-emerald-200/60 dark:border-emerald-800 flex items-center space-x-1 shrink-0">
        <i data-lucide="credit-card" class="w-3 h-3"></i>
        <span>Voided Check</span>
      </span>
    `);
  } else if (docType === "other_document") {
    typeBadges.push(`
      <span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400 border border-slate-200 dark:border-slate-700 flex items-center space-x-1 shrink-0">
        <i data-lucide="help-circle" class="w-3 h-3"></i>
        <span>Other Document</span>
      </span>
    `);
  }

  // Partial statement coverage badge
  if (isPartial) {
    typeBadges.push(`
      <div class="relative has-tooltip shrink-0">
        <span class="bg-amber-100 text-amber-900 dark:bg-amber-950/80 dark:text-amber-200 border border-amber-300 dark:border-amber-700 text-[10px] px-2 py-0.5 rounded-full font-semibold flex items-center space-x-1 cursor-pointer">
          <i data-lucide="alert-circle" class="w-3 h-3 text-amber-600 dark:text-amber-400"></i>
          <span>Partial Statement (${coveragePct !== undefined ? coveragePct + "%" : "≤70%"})</span>
        </span>
        <div class="tooltip absolute z-30 bottom-full left-1/2 -translate-x-1/2 mb-2 w-72 p-2.5 bg-slate-900 text-white text-[11px] rounded-xl shadow-2xl leading-relaxed pointer-events-none">
          <span class="font-bold block text-amber-400 mb-0.5">Partial Statement Notice:</span>
          ${doc.result?.reconciliation?.coverage_reason || "Transactions cover ≤ 70% of the statement duration. Full statement is missing or this is only a partial statement."}
        </div>
      </div>
    `);
  }

  let badgeHTML = "";
  if (doc.status === "pending") {
    badgeHTML = `<span class="bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400 text-xs px-2.5 py-1 rounded-full font-medium inline-flex items-center space-x-1"><i data-lucide="clock" class="w-3 h-3"></i><span>Not Extracted</span></span>`;
  } else if (doc.status === "reconciled") {
    badgeHTML = `
      <span class="bg-emerald-50 dark:bg-emerald-950/70 text-emerald-700 dark:text-emerald-300 border border-emerald-200/60 dark:border-emerald-800 text-xs px-2.5 py-1 rounded-full font-semibold flex items-center space-x-1.5 shadow-sm">
        <i data-lucide="check-circle" class="w-3.5 h-3.5 text-emerald-600 dark:text-emerald-400"></i>
        <span>Reconciled ($0.00 Diff)</span>
      </span>`;
  } else if (doc.status === "warning") {
    badgeHTML = `
      <div class="relative has-tooltip">
        <span class="bg-amber-50 dark:bg-amber-950/70 text-amber-800 dark:text-amber-300 border border-amber-200/60 dark:border-amber-800 text-xs px-2.5 py-1 rounded-full font-semibold flex items-center space-x-1.5 cursor-pointer shadow-sm">
          <i data-lucide="alert-triangle" class="w-3.5 h-3.5 text-amber-600"></i>
          <span>Verification Warning</span>
        </span>
        <div class="tooltip absolute z-30 bottom-full left-1/2 -translate-x-1/2 mb-2 w-80 p-3 bg-slate-900 text-white text-xs rounded-xl shadow-2xl leading-relaxed pointer-events-none">
          <span class="font-bold block text-amber-400 mb-1">Discrepancy Details:</span>
          ${doc.warningReason || doc.result?.warningReason || "Discrepancy detected between line items and printed totals."}
        </div>
      </div>`;
  } else if (doc.status === "extracting") {
    const pct = doc.progressPct || 10;
    const msg = doc.progressMsg || "Analyzing statement...";
    badgeHTML = `
      <div class="flex flex-col space-y-1 min-w-[160px]">
        <div class="flex items-center justify-between text-[11px] text-brand-600 dark:text-brand-400 font-semibold">
          <span class="truncate max-w-[120px]">${msg}</span>
          <span>${pct}%</span>
        </div>
        <div class="w-full bg-slate-200 dark:bg-slate-700 h-1.5 rounded-full overflow-hidden">
          <div class="bg-brand-600 h-full rounded-full transition-all duration-300" style="width: ${pct}%"></div>
        </div>
      </div>`;
  } else if (doc.status === "failed") {
    badgeHTML = `
      <div class="relative has-tooltip">
        <span class="bg-rose-50 dark:bg-rose-950/70 text-rose-700 dark:text-rose-300 border border-rose-200/60 dark:border-rose-800 text-xs px-2.5 py-1 rounded-full font-semibold flex items-center space-x-1.5 cursor-pointer">
          <i data-lucide="x-circle" class="w-3.5 h-3.5"></i>
          <span>Failed</span>
        </span>
        <div class="tooltip absolute z-30 bottom-full left-1/2 -translate-x-1/2 mb-2 w-72 p-3 bg-slate-900 text-white text-xs rounded-xl shadow-xl leading-relaxed">
          ${doc.warningReason || "Extraction request failed."}
        </div>
      </div>`;
  }

  // Action buttons per User Requirements:
  // After extraction finished:
  // - If warnings exist -> Extract button becomes "Extract Again"
  // - If no warnings -> Says "Transaction Extracted"
  let actionButtonsHTML = "";
  if (doc.status === "reconciled") {
    actionButtonsHTML = `
      <span class="text-xs text-emerald-600 dark:text-emerald-400 font-semibold flex items-center space-x-1 px-2.5 py-1.5 rounded-lg bg-emerald-50 dark:bg-emerald-950/60 border border-emerald-200 dark:border-emerald-800/60">
        <i data-lucide="check-check" class="w-3.5 h-3.5 text-emerald-600 dark:text-emerald-400"></i>
        <span>Transaction Extracted</span>
      </span>
      <button onclick="viewExtractedData('${doc.id}')" class="px-3.5 py-1.5 text-xs bg-brand-50 hover:bg-brand-100 dark:bg-brand-950/80 dark:hover:bg-brand-900 text-brand-700 dark:text-brand-300 font-semibold rounded-lg transition border border-brand-200/80 dark:border-brand-800 flex items-center space-x-1.5 shadow-sm active:scale-95">
        <i data-lucide="eye" class="w-3.5 h-3.5"></i>
        <span>View Data</span>
      </button>
    `;
  } else if (doc.status === "warning") {
    actionButtonsHTML = `
      <button onclick="triggerExtraction('${doc.id}')" class="px-3.5 py-1.5 text-xs bg-amber-500 hover:bg-amber-600 text-white font-semibold rounded-lg shadow-sm transition active:scale-95 flex items-center space-x-1.5" title="Re-run extraction">
        <i data-lucide="refresh-cw" class="w-3.5 h-3.5"></i>
        <span>Extract Again</span>
      </button>
      <button onclick="viewExtractedData('${doc.id}')" class="px-3.5 py-1.5 text-xs bg-brand-50 hover:bg-brand-100 dark:bg-brand-950/80 dark:hover:bg-brand-900 text-brand-700 dark:text-brand-300 font-semibold rounded-lg transition border border-brand-200/80 dark:border-brand-800 flex items-center space-x-1.5 shadow-sm active:scale-95">
        <i data-lucide="eye" class="w-3.5 h-3.5"></i>
        <span>View Data</span>
      </button>
    `;
  } else if (doc.status === "pending") {
    actionButtonsHTML = `
      <button onclick="triggerExtraction('${doc.id}')" class="px-3.5 py-1.5 text-xs bg-brand-600 hover:bg-brand-700 text-white font-semibold rounded-lg shadow-sm transition active:scale-95 flex items-center space-x-1.5">
        <i data-lucide="sparkles" class="w-3.5 h-3.5 text-brand-200"></i>
        <span>Extract</span>
      </button>
    `;
  } else if (doc.status === "failed") {
    actionButtonsHTML = `
      <button onclick="triggerExtraction('${doc.id}')" class="px-3.5 py-1.5 text-xs bg-rose-600 hover:bg-rose-700 text-white font-semibold rounded-lg shadow-sm transition active:scale-95 flex items-center space-x-1.5">
        <i data-lucide="refresh-cw" class="w-3.5 h-3.5"></i>
        <span>Retry Extract</span>
      </button>
    `;
  }

  const bankName = doc.result?.extractedData?.bankName;
  const endingBal = doc.result?.extractedData?.endingBalance;
  const isPdf = doc.name.toLowerCase().endsWith(".pdf");

  return `
    <div class="flex items-center space-x-3.5 truncate min-w-0">
      <div class="w-10 h-10 rounded-xl ${isPdf ? "bg-rose-50 text-rose-600 dark:bg-rose-950/40 dark:text-rose-400" : "bg-blue-50 text-blue-600 dark:bg-blue-950/40 dark:text-blue-400"} flex items-center justify-center shrink-0 border border-slate-200/60 dark:border-slate-800">
        <i data-lucide="${isPdf ? "file-text" : "image"}" class="w-5 h-5"></i>
      </div>
      <div class="truncate">
        <div class="flex flex-wrap items-center gap-1.5">
          <h4 class="text-xs sm:text-sm font-semibold text-slate-800 dark:text-slate-100 truncate">${doc.name}</h4>
          ${bankName ? `<span class="hidden md:inline-flex px-2 py-0.2 rounded-md bg-slate-100 dark:bg-slate-800 text-[10px] font-semibold text-slate-600 dark:text-slate-300 uppercase">${bankName}</span>` : ""}
          ${typeBadges.join("")}
        </div>
        <div class="flex items-center space-x-2 text-[11px] text-slate-400 mt-1">
          <span>${(doc.size / 1024).toFixed(1)} KB</span>
          ${endingBal !== undefined ? `<span>·</span><span class="font-mono font-medium text-slate-600 dark:text-slate-300">Ending: ${money(endingBal)}</span>` : ""}
        </div>
      </div>
    </div>

    <div class="flex items-center space-x-2.5 shrink-0 self-end sm:self-auto">
      ${badgeHTML}
      ${actionButtonsHTML}
      <button onclick="deleteDocument('${doc.id}')" class="p-1.5 text-slate-400 hover:text-rose-600 hover:bg-rose-50 dark:hover:bg-rose-950/40 rounded-lg transition" title="Delete document">
        <i data-lucide="trash-2" class="w-4 h-4"></i>
      </button>
    </div>
  `;
}

// ===========================================================================
// 5. LIVE SSE PROGRESS STREAMING & EXTRACTION
// ===========================================================================
async function triggerExtraction(docId) {
  const doc = documentsStore.find((d) => d.id === docId);
  if (!doc) return;

  const key = sessionStorage.getItem("underwriter_key");
  const prov = sessionStorage.getItem("underwriter_provider") || "openai";
  const model = sessionStorage.getItem("underwriter_model") || "gpt-5.6-terra";
  const baseUrl = sessionStorage.getItem("underwriter_base_url") || "";
  const effort = sessionStorage.getItem("underwriter_reasoning_effort") || "medium";

  if (!key) {
    openSettingsModal();
    showToast("Please enter and save your API key to extract live", "warning");
    return;
  }

  if (!doc.fileObject) {
    showToast("Source file binary not in memory. Please re-upload.", "error");
    return;
  }

  doc.status = "extracting";
  doc.progressPct = 10;
  doc.progressMsg = "Initializing pipeline...";
  renderDocumentTree();

  const jobId = "job_" + Math.random().toString(36).substr(2, 9);
  const formData = new FormData();
  formData.append("file", doc.fileObject);
  formData.append("job_id", jobId);

  // Connect to SSE stream for real-time progress events
  try {
    const sse = new EventSource(`/api/progress/${jobId}`);
    activeEventSources[docId] = sse;

    sse.onmessage = (event) => {
      try {
        const state = JSON.parse(event.data);
        if (state.pct !== undefined) doc.progressPct = state.pct;
        if (state.msg) doc.progressMsg = state.msg;
        
        // Re-render row with animated progress bar
        const row = document.getElementById(`doc-row-${docId}`);
        if (row) {
          row.innerHTML = buildDocRowHTML(doc);
          if (window.lucide) lucide.createIcons();
        }

        if (state.done) {
          sse.close();
          delete activeEventSources[docId];
        }
      } catch (err) {
        console.warn("SSE parse error:", err);
      }
    };

    sse.onerror = () => {
      sse.close();
      delete activeEventSources[docId];
    };
  } catch (e) {
    console.warn("Could not initiate SSE stream:", e);
  }

  try {
    const res = await fetch("/api/extract", {
      method: "POST",
      headers: {
        "x-provider": prov,
        "x-api-key": key,
        "x-model": model,
        "x-base-url": baseUrl,
        "x-reasoning-effort": effort,
      },
      body: formData,
    });

    const payload = await res.json();
    if (!res.ok) throw new Error(payload.message || "Extraction failed");

    const r = payload.result;
    doc.result = r;
    if (r.status === "other_no_info") {
      doc.status = "failed";
      doc.warningReason = r.message || "Document not recognized as actionable financial statement.";
      showToast("Document contains no actionable statement data", "warning");
    } else {
      doc.status = r.status === "success" ? "reconciled" : r.status === "warning" ? "warning" : "failed";
      doc.warningReason = r.warningReason;
      showToast(`Extraction complete for ${doc.name}`, "success");
    }
  } catch (err) {
    doc.status = "failed";
    doc.warningReason = err.message;
    showToast("Extraction error: " + err.message, "error");
  } finally {
    if (activeEventSources[docId]) {
      activeEventSources[docId].close();
      delete activeEventSources[docId];
    }
    saveStoredDocuments();
    renderDocumentTree();
    updateDashboardStats();
  }
}

// Batch extract all pending documents sequentially
async function extractAllPending() {
  const pendingDocs = documentsStore.filter((d) => d.status === "pending");
  if (!pendingDocs.length) {
    showToast("No pending documents to extract", "info");
    return;
  }

  const key = sessionStorage.getItem("underwriter_key");
  if (!key) {
    openSettingsModal();
    showToast("Please enter an API key before batch extraction", "warning");
    return;
  }

  showToast(`Starting batch extraction for ${pendingDocs.length} files...`, "info");
  for (const doc of pendingDocs) {
    await triggerExtraction(doc.id);
  }
}

// ===========================================================================
// 6. SYNTHETIC DEMO DATA GENERATOR
// ===========================================================================
function loadSampleDemo() {
  const sample1 = {
    id: "demo_1",
    name: "Needham_Bank_x5737_Commercial_Sept2026.pdf",
    size: 245760,
    uploadDate: new Date().toISOString(),
    fileObject: null,
    status: "reconciled",
    warningReason: null,
    result: {
      documentType: "bank_statement",
      isConsolidated: false,
      status: "success",
      warningReason: null,
      extractedData: {
        bankName: "Needham Bank",
        accountHolder: "Northeast Distribution Partners LLC",
        accountNumber: "5737",
        startdate: "2026-09-01",
        enddate: "2026-09-30",
        beginningBalance: 12450.50,
        totalDeposits: 58240.00,
        totalWithdrawals: 42310.25,
        endingBalance: 28380.25,
        transactions: [
          { trID: "TR00001", postingDate: "2026-09-02", description: "STRIPE PAYMENTS SETTLEMENT TRANSFER", amount: 14200.00, type: "credit", balance: 26650.50 },
          { trID: "TR00002", postingDate: "2026-09-04", description: "ACH DEBIT EXPANSION CAP REPAYMENT", amount: -450.00, type: "debit", balance: 26200.50 },
          { trID: "TR00003", postingDate: "2026-09-05", description: "ACH DEBIT EXPANSION CAP REPAYMENT", amount: -450.00, type: "debit", balance: 25750.50 },
          { trID: "TR00004", postingDate: "2026-09-08", description: "WIRE TRANSFER FROM CLIENT CORP", amount: 24040.00, type: "credit", balance: 49790.50 },
          { trID: "TR00005", postingDate: "2026-09-10", description: "AMEX EPAYMENT ACH PMT", amount: -3200.00, type: "debit", balance: 46590.50 },
          { trID: "TR00006", postingDate: "2026-09-15", description: "PAYROLL DIRECT DEPOSIT RUN", amount: -18210.25, type: "debit", balance: 28380.25 },
        ],
      },
      reconciliation: {
        passed: true,
        credit_sum: 58240.00,
        debit_sum: 42310.25,
        checks: [
          { name: "Summary Balance Reconciliation", passed: true, diff: 0.00 },
          { name: "Deposits vs Summary Check", passed: true, diff: 0.00 },
          { name: "Withdrawals vs Summary Check", passed: true, diff: 0.00 },
        ],
      },
      underwriting: {
        mca_count: 2,
        loan_count: 1,
        returned_count: 0,
        analyzed_transactions: [
          { trID: "TR00002", trCategory: "Expense", isMca: true, lenderName: "Expansion Capital", isCreditCard: false, isBusinessLoan: false, isBnpl: false, isReturnedItem: false },
          { trID: "TR00003", trCategory: "Expense", isMca: true, lenderName: "Expansion Capital", isCreditCard: false, isBusinessLoan: false, isBnpl: false, isReturnedItem: false },
          { trID: "TR00005", trCategory: "Expense", isMca: false, lenderName: null, isCreditCard: true, creditorName: "American Express", isBusinessLoan: false, isBnpl: false, isReturnedItem: false },
        ],
      },
    },
  };

  const sample2 = {
    id: "demo_2",
    name: "Truist_Bank_x3718_Consolidated.pdf",
    size: 412890,
    uploadDate: new Date(Date.now() - 86400000).toISOString(),
    fileObject: null,
    status: "warning",
    warningReason: "Withdrawals mismatch: Extracted $18,436.00 vs Summary $18,400.00 (diff: +$36.00) | Check consecutive fee line on page 3",
    result: {
      documentType: "bank_statement",
      isConsolidated: true,
      status: "warning",
      warningReason: "Withdrawals mismatch: Extracted $18,436.00 vs Summary $18,400.00 (diff: +$36.00)",
      extractedData: {
        bankName: "Truist Bank",
        accountHolder: "Southern Precision Logistics LLC",
        accountNumber: "3718",
        startdate: "2026-08-01",
        enddate: "2026-08-31",
        beginningBalance: 4200.00,
        totalDeposits: 31200.00,
        totalWithdrawals: 18400.00,
        endingBalance: 17000.00,
        transactions: [
          { trID: "TR00001", postingDate: "2026-08-03", description: "CUSTOMER INVOICE DEPOSIT", amount: 15200.00, type: "credit", balance: 19400.00 },
          { trID: "TR00002", postingDate: "2026-08-12", description: "OVERDRAFT FEE NOT CHARGED 36", amount: -36.00, type: "debit", balance: 19364.00 },
          { trID: "TR00003", postingDate: "2026-08-18", description: "ACH GIGGLE FINANCE REPAYMENT", amount: -18400.00, type: "debit", balance: 17000.00 },
        ],
      },
      reconciliation: {
        passed: false,
        warningReason: "Withdrawals mismatch: Extracted $18,436.00 vs Summary $18,400.00 (diff: +$36.00)",
        checks: [
          { name: "Summary Balance Reconciliation", passed: true, diff: 0.00 },
          { name: "Deposits vs Summary Check", passed: true, diff: 0.00 },
          { name: "Withdrawals vs Summary Check", passed: false, diff: 36.00 },
        ],
      },
      underwriting: {
        mca_count: 1,
        loan_count: 0,
        returned_count: 0,
        analyzed_transactions: [
          { trID: "TR00003", trCategory: "Expense", isMca: true, lenderName: "Giggle Finance", isCreditCard: false, isBusinessLoan: false, isBnpl: false, isReturnedItem: false },
        ],
      },
    },
  };

  documentsStore = [sample1, sample2];
  saveStoredDocuments();
  renderDocumentTree();
  updateDashboardStats();
  showToast("Loaded 2 demonstration statements into your library!", "success");
}

// ===========================================================================
// 7. SPLIT VIEWER & MODAL WORKSPACE
// ===========================================================================
function viewExtractedData(docId) {
  const doc = documentsStore.find((d) => d.id === docId);
  if (!doc || !doc.result) return;
  currentViewingDoc = doc;

  const originalButton = document.getElementById("downloadOriginalBtn");
  if (originalButton) {
    originalButton.disabled = !doc.fileObject;
    originalButton.classList.toggle("opacity-50", !doc.fileObject);
    originalButton.classList.toggle("cursor-not-allowed", !doc.fileObject);
  }

  // Set modal headers
  document.getElementById("modalDocTitle").textContent = doc.name;
  const badgeEl = document.getElementById("modalDocBadge");
  if (badgeEl) {
    if (doc.status === "reconciled") {
      badgeEl.className = "px-2.5 py-0.5 rounded-full text-xs font-bold bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300";
      badgeEl.textContent = "Reconciled ($0.00 Diff)";
    } else {
      badgeEl.className = "px-2.5 py-0.5 rounded-full text-xs font-bold bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-300";
      badgeEl.textContent = "Math Warning";
    }
  }

  const res = doc.result;
  const data = res.extractedData || {};

  // Tab 1: KPIs
  document.getElementById("kpiBegBal").textContent = money(data.beginningBalance);
  document.getElementById("kpiDeposits").textContent = money(data.totalDeposits);
  document.getElementById("kpiWithdrawals").textContent = money(data.totalWithdrawals);
  document.getElementById("kpiEndBal").textContent = money(data.endingBalance);

  // Cashflow Ratio Bar
  const deposits = Number(data.totalDeposits) || 0;
  const withdrawals = Number(data.totalWithdrawals) || 0;
  const totalFlow = deposits + withdrawals;
  const depPct = totalFlow > 0 ? (deposits / totalFlow) * 100 : 50;
  const withPct = totalFlow > 0 ? (withdrawals / totalFlow) * 100 : 50;

  const depBar = document.getElementById("cashflowDepositBar");
  const withBar = document.getElementById("cashflowWithdrawalBar");
  const netEl = document.getElementById("cashflowNetChange");

  if (depBar) depBar.style.width = `${depPct.toFixed(1)}%`;
  if (withBar) withBar.style.width = `${withPct.toFixed(1)}%`;
  
  if (netEl) {
    const net = deposits - withdrawals;
    netEl.textContent = `${net >= 0 ? "+" : "−"}${money(Math.abs(net))} Net ${net >= 0 ? "Inflow" : "Outflow"}`;
    netEl.className = `font-mono font-bold text-xs ${net >= 0 ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400"}`;
  }

  // Account details
  document.getElementById("valBank").textContent = data.bankName || "Unspecified Bank";
  document.getElementById("valAccNum").textContent = data.accountNumber ? `•••• ${data.accountNumber.slice(-4)}` : "N/A";
  document.getElementById("valHolder").textContent = data.accountHolder || "N/A";
  document.getElementById("valPeriod").textContent = data.startdate && data.enddate ? `${data.startdate} → ${data.enddate}` : "Not stated";

  // Tab counters
  const txns = data.transactions || [];
  const txnCountBadge = document.getElementById("tabTxnCountBadge");
  if (txnCountBadge) txnCountBadge.textContent = txns.length;

  const mcaCountBadge = document.getElementById("tabMcaCountBadge");
  const mcaTotal = (res.underwriting?.mca_count || 0) + (res.underwriting?.returned_count || 0);
  if (mcaCountBadge) {
    if (mcaTotal > 0) {
      mcaCountBadge.textContent = mcaTotal;
      mcaCountBadge.classList.remove("hidden");
    } else {
      mcaCountBadge.classList.add("hidden");
    }
  }

  renderAuditTab(res.reconciliation, data);
  renderTransactionsGrid(txns);
  renderMcaTab(res.underwriting || {});
  loadDocPreview(doc.fileObject);

  switchTab("summary");
  document.getElementById("dataViewerModal").classList.remove("hidden");
  if (window.lucide) lucide.createIcons();
}

function renderAuditTab(audit, data = {}) {
  const banner = document.getElementById("auditBanner");
  const tbody = document.getElementById("auditTableBody");
  tbody.innerHTML = "";

  // Update equation labels
  const eqBeg = document.getElementById("eqBeg");
  const eqDep = document.getElementById("eqDep");
  const eqWith = document.getElementById("eqWith");
  const eqEnd = document.getElementById("eqEnd");

  if (eqBeg) eqBeg.textContent = money(data.beginningBalance);
  if (eqDep) eqDep.textContent = money(data.totalDeposits);
  if (eqWith) eqWith.textContent = money(data.totalWithdrawals);
  if (eqEnd) eqEnd.textContent = money(data.endingBalance);

  if (!audit) return;

  banner.className = `p-4 sm:p-5 rounded-xl flex items-start space-x-3.5 ${
    audit.passed
      ? "bg-emerald-50 text-emerald-900 border border-emerald-200 dark:bg-emerald-950/60 dark:text-emerald-200 dark:border-emerald-800"
      : "bg-amber-50 text-amber-900 border border-amber-200 dark:bg-amber-950/60 dark:text-amber-200 dark:border-amber-800"
  }`;
  banner.innerHTML = `
    <i data-lucide="${audit.passed ? "check-circle-2" : "alert-triangle"}" class="w-5 h-5 flex-shrink-0 mt-0.5 text-${audit.passed ? "emerald-600" : "amber-600"}"></i>
    <div>
      <h4 class="font-bold text-sm sm:text-base">${audit.passed ? "Mathematical Verification: PASSED (100% Reconciled)" : "Mathematical Warning / Discrepancy Flagged"}</h4>
      <p class="text-xs sm:text-sm mt-1 text-${audit.passed ? "emerald-800 dark:text-emerald-300" : "amber-800 dark:text-amber-300"}">${audit.warningReason || "All extracted statement line items reconciled with the printed balance summary to exactly $0.00."}</p>
    </div>
  `;

  (audit.checks || []).forEach((c) => {
    const tr = document.createElement("tr");
    tr.className = "hover:bg-slate-50/50 dark:hover:bg-slate-800/50";
    tr.innerHTML = `
      <td class="py-3 px-4 font-medium text-slate-800 dark:text-slate-200">${c.name}</td>
      <td class="py-3 px-4"><span class="px-2 py-0.5 rounded text-xs font-semibold ${
        c.passed ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300" : "bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-300"
      }">${c.passed ? "PASS" : "FAIL"}</span></td>
      <td class="py-3 px-4 text-right font-mono font-semibold ${c.diff > 0 ? "text-amber-600" : "text-slate-600 dark:text-slate-300"}">${c.diff !== null ? money(c.diff) : "N/A"}</td>
    `;
    tbody.appendChild(tr);
  });
}

function renderTransactionsGrid(txns) {
  const tbody = document.getElementById("txnsTableBody");
  tbody.innerHTML = "";

  const searchInput = document.getElementById("txnSearchInput");
  const typeFilter = document.getElementById("txnTypeFilter");
  const summaryEl = document.getElementById("txnFilterSummary");

  const query = searchInput ? searchInput.value.toLowerCase().trim() : "";
  const filter = typeFilter ? typeFilter.value : "all";

  const filtered = txns.filter((tx) => {
    const matchText = (tx.description || "").toLowerCase().includes(query) || (tx.trID || "").toLowerCase().includes(query);
    const isCredit = Number(tx.amount) > 0 || tx.type === "credit";
    const isDebit = Number(tx.amount) < 0 || tx.type === "debit";
    const isReturned = tx.type === "returned_item";

    if (filter === "credit") return matchText && isCredit;
    if (filter === "debit") return matchText && isDebit;
    if (filter === "returned") return matchText && isReturned;
    return matchText;
  });

  if (summaryEl) {
    summaryEl.textContent = `Showing ${filtered.length} of ${txns.length} rows`;
  }

  if (filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" class="text-center py-8 text-slate-400 font-sans text-xs">No transactions match your search filter.</td></tr>`;
    return;
  }

  filtered.forEach((tx) => {
    const tr = document.createElement("tr");
    const isPositive = Number(tx.amount) > 0 || tx.type === "credit";
    const isReturned = tx.type === "returned_item";
    tr.className = "hover:bg-slate-50 dark:hover:bg-slate-800/60 cursor-pointer transition-colors";
    tr.dataset.transactionId = tx.trID || "";
    tr.onclick = () => focusTransaction(tx.trID);
    tr.innerHTML = `
      <td class="py-2.5 px-3 text-slate-400 font-mono text-[11px]">${tx.trID}</td>
      <td class="py-2.5 px-3 text-slate-600 dark:text-slate-300 whitespace-nowrap">${tx.postingDate || "N/A"}</td>
      <td class="py-2.5 px-4 text-slate-800 dark:text-slate-100 font-sans truncate max-w-xs" title="${tx.description}">${tx.description}</td>
      <td class="py-2.5 px-3 text-right font-bold whitespace-nowrap ${isPositive ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400"}">${signedMoney(tx.amount, isPositive)}</td>
      <td class="py-2.5 px-3 whitespace-nowrap"><span class="px-2 py-0.5 rounded text-[10px] font-semibold uppercase ${
        isReturned ? "bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-300" : isPositive ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300" : "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300"
      }">${(tx.type || "unclassified").replace("_", " ")}</span></td>
      <td class="py-2.5 px-3 text-right text-slate-500 dark:text-slate-400 whitespace-nowrap font-mono">${money(tx.balance)}</td>
    `;
    tbody.appendChild(tr);
  });
}

function filterTransactionsGrid() {
  const txns = currentViewingDoc?.result?.extractedData?.transactions || [];
  renderTransactionsGrid(txns);
}

function renderMcaTab(uw) {
  document.getElementById("mcaCount").textContent = uw.mca_count || 0;
  document.getElementById("loanCount").textContent = uw.loan_count || 0;
  document.getElementById("nsfCount").textContent = uw.returned_count || 0;

  const container = document.getElementById("mcaDetailsList");
  container.innerHTML = "";

  const matchingTransactions = currentViewingDoc?.result?.extractedData?.transactions || [];
  const items = (uw.analyzed_transactions || []).filter(
    (t) => t.isMca || t.isBusinessLoan || t.isCreditCard || t.isBnpl || t.isReturnedItem
  );

  if (!items.length) {
    container.innerHTML = `
      <div class="rounded-2xl border border-dashed border-slate-300 dark:border-slate-800 p-8 text-center text-xs text-slate-500 dark:text-slate-400 bg-slate-50/50 dark:bg-slate-850">
        <i data-lucide="shield-check" class="w-8 h-8 text-emerald-500 mx-auto mb-2"></i>
        <p class="font-semibold text-slate-700 dark:text-slate-200">Clean Underwriting Profile</p>
        <p class="text-[11px] mt-0.5">No predatory MCAs, stacked loans, or returned NSF items were detected in this statement.</p>
      </div>`;
    if (window.lucide) lucide.createIcons();
    return;
  }

  items.forEach((item) => {
    const transaction = matchingTransactions.find((tx) => tx.trID === item.trID) || {};
    const kind = item.isMca ? "MCA Position" : item.isBusinessLoan ? "Business Loan" : item.isCreditCard ? "Credit Card" : item.isBnpl ? "BNPL" : "Returned NSF Item";
    const badgeColor = item.isMca ? "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300" : item.isReturnedItem ? "bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-300" : "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300";
    
    const card = document.createElement("div");
    card.className = "p-4 border border-slate-200 dark:border-slate-800 rounded-xl bg-white dark:bg-slate-850 text-xs shadow-sm hover:border-brand-300 dark:hover:border-brand-700 transition";
    card.innerHTML = `
      <div class="flex items-start justify-between gap-3">
        <div class="min-w-0">
          <div class="flex flex-wrap items-center gap-2">
            <span class="font-bold text-slate-900 dark:text-white text-sm">${item.lenderName || item.loanLenderName || item.creditorName || item.bnplProvider || "Classified Risk Signal"}</span>
            <span class="px-2 py-0.5 rounded text-[10px] font-semibold ${badgeColor}">${kind}</span>
          </div>
          <p class="mt-1 text-slate-500 dark:text-slate-400 truncate" title="${transaction.description || ""}">${transaction.description || item.trCategory || "Classified debit transaction"}</p>
        </div>
        <button class="shrink-0 text-brand-600 dark:text-brand-400 hover:underline font-semibold flex items-center space-x-1" onclick="focusTransaction('${item.trID}')">
          <span>View in Grid</span>
          <i data-lucide="arrow-right" class="w-3 h-3"></i>
        </button>
      </div>
      <div class="mt-3 flex items-center justify-between border-t border-slate-100 dark:border-slate-800 pt-2 text-slate-500 dark:text-slate-400 text-[11px]">
        <span>Posting Date: ${transaction.postingDate || "N/A"} · ID: ${item.trID}</span>
        <span class="font-mono font-bold ${Number(transaction.amount) < 0 ? "text-rose-600 dark:text-rose-400" : "text-emerald-600 dark:text-emerald-400"}">${transaction.amount === undefined ? "" : signedMoney(transaction.amount)}</span>
      </div>
    `;
    container.appendChild(card);
  });

  if (window.lucide) lucide.createIcons();
}

function focusTransaction(trID) {
  switchTab("txns");
  
  // Clear any active transaction search filter so the target row is rendered
  const searchInput = document.getElementById("txnSearchInput");
  const typeFilter = document.getElementById("txnTypeFilter");
  if (searchInput) searchInput.value = "";
  if (typeFilter) typeFilter.value = "all";
  filterTransactionsGrid();

  setTimeout(() => {
    const row = document.querySelector(`[data-transaction-id="${trID}"]`);
    if (row) {
      row.scrollIntoView({ behavior: "smooth", block: "center" });
      row.classList.add("bg-brand-100", "dark:bg-brand-950");
      window.setTimeout(() => row.classList.remove("bg-brand-100", "dark:bg-brand-950"), 1800);
    }
  }, 100);
}

async function loadDocPreview(file) {
  const pages = document.getElementById("pdfPages");
  const imgEl = document.getElementById("imageViewer");
  const indicator = document.getElementById("previewPageIndicator");
  const controls = document.getElementById("pdfControls");
  
  pages.innerHTML = "";
  currentPdf = null;
  if (previewObjectUrl) URL.revokeObjectURL(previewObjectUrl);
  previewObjectUrl = null;
  controls.classList.add("hidden");

  if (!file) {
    pages.innerHTML = `
      <div class="max-w-xs rounded-2xl border border-dashed border-slate-700 bg-slate-850 p-8 text-center text-xs text-slate-400 space-y-2">
        <i data-lucide="file-question" class="w-8 h-8 text-slate-500 mx-auto"></i>
        <p class="font-semibold text-slate-300">Preview not available for demo record</p>
        <p class="text-[11px]">Demo records are pre-computed synthetic benchmarks without an underlying binary file.</p>
      </div>`;
    imgEl.classList.add("hidden");
    if (indicator) indicator.textContent = "Synthetic Demo";
    if (window.lucide) lucide.createIcons();
    return;
  }

  if (file.type && file.type.includes("pdf")) {
    imgEl.classList.add("hidden");
    try {
      const arrayBuffer = await file.arrayBuffer();
      currentPdf = await pdfjsLib.getDocument({ data: arrayBuffer }).promise;
      controls.classList.remove("hidden");
      await renderAllPdfPages();
    } catch (e) {
      console.warn("Could not render PDF preview:", e);
      pages.innerHTML = '<div class="rounded-xl bg-rose-950/80 p-4 text-xs text-rose-300 border border-rose-800">The PDF preview could not be rendered. You can still download the original file.</div>';
    }
  } else {
    imgEl.classList.remove("hidden");
    previewObjectUrl = URL.createObjectURL(file);
    imgEl.src = previewObjectUrl;
    if (indicator) indicator.textContent = "Image Document";
  }
}

async function renderAllPdfPages() {
  if (!currentPdf) return;
  const pages = document.getElementById("pdfPages");
  const indicator = document.getElementById("previewPageIndicator");
  pages.innerHTML = "";
  
  for (let number = 1; number <= currentPdf.numPages; number += 1) {
    const page = await currentPdf.getPage(number);
    const viewport = page.getViewport({ scale: pdfZoom });
    const wrapper = document.createElement("section");
    wrapper.id = `pdf-page-${number}`;
    wrapper.className = "relative rounded-lg bg-white p-1 shadow-2xl overflow-hidden";
    
    const label = document.createElement("span");
    label.className = "absolute right-2 top-2 rounded bg-slate-900/80 backdrop-blur-sm px-2 py-0.5 text-[10px] text-white font-mono";
    label.textContent = `Page ${number}`;
    
    const canvas = document.createElement("canvas");
    canvas.width = viewport.width;
    canvas.height = viewport.height;
    await page.render({ canvasContext: canvas.getContext("2d"), viewport }).promise;
    
    wrapper.append(canvas, label);
    pages.appendChild(wrapper);
  }
  if (indicator) indicator.textContent = `${currentPdf.numPages} pages · ${Math.round(pdfZoom * 100)}%`;
}

function changePdfZoom(change) {
  pdfZoom = Math.min(2.2, Math.max(0.6, +(pdfZoom + change).toFixed(2)));
  renderAllPdfPages();
}

function scrollPdfPage(direction) {
  if (!currentPdf) return;
  const container = document.getElementById("pdfViewerContainer");
  const sections = [...document.querySelectorAll("[id^='pdf-page-']")];
  const currentIndex = sections.reduce(
    (best, node, index) =>
      Math.abs(node.offsetTop - container.scrollTop) < Math.abs(sections[best].offsetTop - container.scrollTop)
        ? index
        : best,
    0
  );
  sections[Math.max(0, Math.min(sections.length - 1, currentIndex + direction))]?.scrollIntoView({
    behavior: "smooth",
    block: "start",
  });
}

function switchTab(tabId) {
  ["summary", "audit", "txns", "mca"].forEach((t) => {
    const content = document.getElementById(`tabContent-${t}`);
    const btn = document.getElementById(`tabBtn-${t}`);
    if (content) content.classList.add("hidden");
    if (btn) {
      btn.classList.remove("active");
      btn.classList.add("border-transparent", "text-slate-500");
    }
  });

  const activeContent = document.getElementById(`tabContent-${tabId}`);
  const activeBtn = document.getElementById(`tabBtn-${tabId}`);
  if (activeContent) activeContent.classList.remove("hidden");
  if (activeBtn) {
    activeBtn.classList.add("active");
    activeBtn.classList.remove("border-transparent", "text-slate-500");
  }
}

function closeViewerModal() {
  document.getElementById("dataViewerModal").classList.add("hidden");
  if (previewObjectUrl) {
    URL.revokeObjectURL(previewObjectUrl);
    previewObjectUrl = null;
  }
}

// ===========================================================================
// 8. THEME & ACCESSIBILITY MANAGEMENT
// ===========================================================================
function initializeTheme() {
  const savedTheme = localStorage.getItem("underwriter_theme");
  const prefersDark = window.matchMedia?.("(prefers-color-scheme: dark)").matches;
  setTheme(savedTheme || (prefersDark ? "dark" : "light"));
}

function setTheme(theme) {
  const dark = theme === "dark";
  document.body.classList.toggle("theme-dark", dark);
  document.documentElement.classList.toggle("dark", dark);
  
  const toggle = document.getElementById("themeToggle");
  if (toggle) {
    toggle.setAttribute("aria-label", `Switch to ${dark ? "light" : "dark"} mode`);
    toggle.setAttribute("title", `Switch to ${dark ? "light" : "dark"} mode`);
    toggle.innerHTML = `<i data-lucide="${dark ? "sun" : "moon"}" class="w-4 h-4"></i>`;
  }
  localStorage.setItem("underwriter_theme", theme);
  if (window.lucide) lucide.createIcons();
}

function toggleTheme() {
  setTheme(document.body.classList.contains("theme-dark") ? "light" : "dark");
}

function setupKeyboardShortcuts() {
  document.addEventListener("keydown", (e) => {
    // ESC closes modals
    if (e.key === "Escape") {
      closeViewerModal();
      closeSettingsModal();
      closeHowToModal();
    }
    // '/' focuses search if not typing in input
    if (e.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) {
      e.preventDefault();
      document.getElementById("searchInput")?.focus();
    }
  });
}

function openSettingsModal() {
  const alertBox = document.getElementById("connStatusAlert");
  if (alertBox) alertBox.classList.add("hidden");
  document.getElementById("settingsModal").classList.remove("hidden");
}

function closeSettingsModal() {
  document.getElementById("settingsModal").classList.add("hidden");
}

function openHowToModal() {
  document.getElementById("howToModal").classList.remove("hidden");
  if (window.lucide) lucide.createIcons();
}

function closeHowToModal() {
  document.getElementById("howToModal").classList.add("hidden");
}

function togglePasswordVisibility() {
  const el = document.getElementById("settingApiKey");
  const icon = document.getElementById("eyeIcon");
  if (el) {
    const isPass = el.type === "password";
    el.type = isPass ? "text" : "password";
    if (icon) icon.setAttribute("data-lucide", isPass ? "eye-off" : "eye");
    if (window.lucide) lucide.createIcons();
  }
}

function money(v) {
  return v === null || v === undefined || Number.isNaN(Number(v))
    ? "N/A"
    : `$${parseFloat(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function signedMoney(v, positive) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "N/A";
  const amount = Number(v);
  const sign = positive === undefined ? (amount < 0 ? "−" : "+") : (positive ? "+" : "−");
  return `${sign}$${Math.abs(amount).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function downloadOriginalFile() {
  const file = currentViewingDoc?.fileObject;
  if (!file) {
    showToast("Source file not available for demo benchmarks", "warning");
    return;
  }
  const href = URL.createObjectURL(file);
  const a = document.createElement("a");
  a.href = href;
  a.download = currentViewingDoc.name || file.name;
  a.click();
  window.setTimeout(() => URL.revokeObjectURL(href), 1000);
  showToast("Downloading original file", "info");
}

function downloadJsonExport() {
  if (!currentViewingDoc || !currentViewingDoc.result) return;
  const dataStr =
    "data:text/json;charset=utf-8," +
    encodeURIComponent(JSON.stringify(currentViewingDoc.result, null, 2));
  const a = document.createElement("a");
  a.href = dataStr;
  a.download = `${currentViewingDoc.name}_underwriting_output.json`;
  a.click();
  showToast("Exported JSON structure", "success");
}

function downloadCsvExport() {
  if (!currentViewingDoc || !currentViewingDoc.result) return;
  const txs = currentViewingDoc.result.extractedData?.transactions || [];
  let csv = "trID,postingDate,description,amount,type,balance\n";
  txs.forEach((t) => {
    csv += `"${t.trID}","${t.postingDate}","${(t.description || "").replace(/"/g, '""')}",${t.amount},"${t.type}",${t.balance}\n`;
  });
  const a = document.createElement("a");
  a.href = "data:text/csv;charset=utf-8," + encodeURIComponent(csv);
  a.download = `${currentViewingDoc.name}_transactions.csv`;
  a.click();
  showToast("Exported CSV transactions", "success");
}