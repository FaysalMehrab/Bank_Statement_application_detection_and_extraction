/**
 * Universal Statement Underwriting Engine — Unified Frontend Logic
 */

// Configure PDF.js worker
if (window.pdfjsLib) {
  pdfjsLib.GlobalWorkerOptions.workerSrc =
    "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";
}

let providersRegistry = {};
let documentsStore = [];
let currentViewingDoc = null;
let currentPdf = null;
let pdfZoom = 1.15;
let previewObjectUrl = null;
const DB_NAME = "universal-underwriter";
const DB_STORE = "source-files";
const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;

// Initialize on DOM load
document.addEventListener("DOMContentLoaded", async () => {
  if (window.lucide) lucide.createIcons();
  await loadProviders();
  loadStoredSettings();
  await loadStoredDocuments();
  setupDropZone();
  renderDocumentTree();
});

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
  }
}

function loadStoredSettings() {
  const provider = sessionStorage.getItem("underwriter_provider") || "openrouter";
  const model = sessionStorage.getItem("underwriter_model") || "openrouter/auto";
  const key = sessionStorage.getItem("underwriter_key") || "";
  const baseUrl = sessionStorage.getItem("underwriter_base_url") || "https://openrouter.ai/api/v1";

  const pEl = document.getElementById("settingProvider");
  const kEl = document.getElementById("settingApiKey");
  const bEl = document.getElementById("settingBaseUrl");

  if (pEl) pEl.value = provider;
  if (kEl) kEl.value = key;
  if (bEl) bEl.value = baseUrl;

  populateModelsDropdown();
  updateHeaderBadge(provider, model);
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
  otherOpt.textContent = "Other Model (Specify ID)...";
  sel.appendChild(otherOpt);

  const storedProvider = sessionStorage.getItem("underwriter_provider");
  const storedModel = sessionStorage.getItem("underwriter_model");
  const preferredModel = storedProvider === prov && storedModel ? storedModel : cfg.default_model;
  if ([...sel.options].some((option) => option.value === preferredModel)) {
    sel.value = preferredModel;
  } else if (preferredModel) {
    sel.value = "other";
    document.getElementById("settingModelCustom").value = preferredModel;
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
  const isOther = document.getElementById("settingModelSelect").value === "other";
  const customEl = document.getElementById("settingModelCustom");
  if (customEl) customEl.style.display = isOther ? "block" : "none";
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
    alert("Choose a model or enter a model ID.");
    return;
  }
  const baseUrl = document.getElementById("settingBaseUrl").value.trim();

  sessionStorage.setItem("underwriter_provider", prov);
  sessionStorage.setItem("underwriter_key", key);
  sessionStorage.setItem("underwriter_model", finalModel);
  sessionStorage.setItem("underwriter_base_url", baseUrl);

  updateHeaderBadge(prov, finalModel);
  closeSettingsModal();
  alert("Settings saved successfully!");
}

function updateHeaderBadge(prov, model) {
  const b = document.getElementById("activeModelBadge");
  const l = document.getElementById("activeModelLabel");
  if (b && l) {
    b.classList.remove("hidden");
    l.textContent = `${prov.toUpperCase()}: ${model}`;
  }
}

async function testConnection() {
  const prov = document.getElementById("settingProvider").value;
  const key = document.getElementById("settingApiKey").value.trim();
  const selModel = document.getElementById("settingModelSelect").value;
  const customModel = document.getElementById("settingModelCustom").value.trim();
  const finalModel = selModel === "other" ? customModel : selModel;
  const baseUrl = document.getElementById("settingBaseUrl").value.trim();

  const btn = document.getElementById("btnTestConn");
  btn.textContent = "Testing...";
  btn.disabled = true;

  try {
    const res = await fetch("/api/test-connection", {
      method: "POST",
      headers: {
        "x-provider": prov,
        "x-api-key": key,
        "x-model": finalModel,
        "x-base-url": baseUrl,
      },
    });
    const data = await res.json();
    if (res.ok) {
      alert("✅ " + data.message);
    } else {
      alert("❌ Connection Failed: " + (data.detail || data.message || "Unknown error"));
    }
  } catch (err) {
    alert("Connection Error: " + err.message);
  } finally {
    btn.textContent = "Test Connection";
    btn.disabled = false;
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
    dz.classList.add("border-indigo-600", "bg-indigo-50/30");
  };
  dz.ondragleave = () => {
    dz.classList.remove("border-indigo-600", "bg-indigo-50/30");
  };
  dz.ondrop = (e) => {
    e.preventDefault();
    dz.classList.remove("border-indigo-600", "bg-indigo-50/30");
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      handleFiles(e.dataTransfer.files);
    }
  };
}

function handleFiles(files) {
  Array.from(files).forEach((file) => {
    const allowed = ["application/pdf", "image/png", "image/jpeg"];
    if (!allowed.includes(file.type) || file.size > MAX_UPLOAD_BYTES) {
      alert(`${file.name} was skipped. Upload a PDF, PNG, or JPG smaller than 25 MB.`);
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
    };
    documentsStore.unshift(newDoc);
    saveSourceFile(docId, file).catch((error) => console.warn("Could not retain source file:", error));
  });
  saveStoredDocuments();
  renderDocumentTree();
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
      await Promise.all(documentsStore.map(async (doc) => {
        doc.fileObject = await getSourceFile(doc.id);
      }));
    }
  } catch (e) {
    documentsStore = [];
  }
}

function clearAllDocs() {
  if (confirm("Clear all documents from the library?")) {
    documentsStore = [];
    localStorage.removeItem("underwriter_docs");
    clearSourceFiles().catch((error) => console.warn("Could not clear local source files:", error));
    renderDocumentTree();
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
  if (!doc || !confirm(`Delete ${doc.name} and its locally stored source file?`)) return;
  documentsStore = documentsStore.filter((item) => item.id !== docId);
  await deleteSourceFile(docId).catch((error) => console.warn("Could not delete source file:", error));
  saveStoredDocuments();
  renderDocumentTree();
}

// ===========================================================================
// 3. DATE-GROUPED RENDERING
// ===========================================================================
function renderDocumentTree() {
  const container = document.getElementById("documentLibrary");
  if (!container) return;

  const searchInput = document.getElementById("searchInput");
  const statusFilter = document.getElementById("statusFilter");
  const search = searchInput ? searchInput.value.toLowerCase() : "";
  const filter = statusFilter ? statusFilter.value : "all";

  container.innerHTML = "";

  const filtered = documentsStore.filter((doc) => {
    const matchesSearch = doc.name.toLowerCase().includes(search);
    if (filter === "all") return matchesSearch;
    if (filter === "reconciled") return matchesSearch && doc.status === "reconciled";
    if (filter === "warning") return matchesSearch && doc.status === "warning";
    if (filter === "pending") return matchesSearch && doc.status === "pending";
    return matchesSearch;
  });

  if (filtered.length === 0) {
    container.innerHTML = `
      <div class="p-8 text-center text-slate-400 border border-slate-200 rounded-xl bg-white text-sm">
        No documents found. Drag & drop a bank statement above or click <strong class="text-indigo-600 cursor-pointer" onclick="loadSampleDemo()">"Demo Data"</strong>.
      </div>`;
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
    let label = docDate.toLocaleDateString();
    if (docDate.toDateString() === todayStr) label = "Today";
    else if (docDate.toDateString() === yestStr) label = "Yesterday";

    if (!groups[label]) groups[label] = [];
    groups[label].push(doc);
  });

  Object.entries(groups).forEach(([dateLabel, docs]) => {
    const groupCard = document.createElement("div");
    groupCard.className = "bg-white border border-slate-200 rounded-xl p-4 shadow-sm space-y-3";

    groupCard.innerHTML = `
      <div class="flex items-center space-x-2 text-xs font-bold text-slate-500 uppercase tracking-wider border-b pb-2">
        <i data-lucide="calendar" class="w-3.5 h-3.5"></i>
        <span>${dateLabel} (${docs.length})</span>
      </div>
      <div class="divide-y divide-slate-100 doc-rows-container"></div>
    `;

    container.appendChild(groupCard);
    const listContainer = groupCard.querySelector(".doc-rows-container");

    docs.forEach((doc) => {
      const row = document.createElement("div");
      row.className = "py-3 flex items-center justify-between";
      row.innerHTML = buildDocRowHTML(doc);
      listContainer.appendChild(row);
    });
  });

  if (window.lucide) lucide.createIcons();
}

function buildDocRowHTML(doc) {
  let badgeHTML = "";

  if (doc.status === "pending") {
    badgeHTML = `<span class="bg-slate-100 text-slate-600 text-xs px-2.5 py-1 rounded-full font-medium">Not Extracted</span>`;
  } else if (doc.status === "reconciled") {
    badgeHTML = `
      <span class="bg-emerald-100 text-emerald-800 text-xs px-2.5 py-1 rounded-full font-semibold flex items-center space-x-1">
        <i data-lucide="check-circle" class="w-3.5 h-3.5"></i><span>Reconciled ($0.00 Diff)</span>
      </span>`;
  } else if (doc.status === "warning") {
    badgeHTML = `
      <div class="relative has-tooltip">
        <span class="bg-amber-100 text-amber-900 text-xs px-2.5 py-1 rounded-full font-semibold flex items-center space-x-1 cursor-pointer">
          <i data-lucide="alert-triangle" class="w-3.5 h-3.5 text-amber-600"></i><span>Math Warning (Hover)</span>
        </span>
        <div class="tooltip absolute z-30 bottom-full left-1/2 -translate-x-1/2 mb-2 w-80 p-2.5 bg-slate-900 text-white text-xs rounded-lg shadow-2xl leading-relaxed pointer-events-none">
          ${doc.warningReason || "Discrepancy detected between line items and printed totals."}
        </div>
      </div>`;
  } else if (doc.status === "extracting") {
    badgeHTML = `<span class="bg-indigo-100 text-indigo-800 text-xs px-2.5 py-1 rounded-full font-medium animate-pulse">Extracting...</span>`;
  } else if (doc.status === "failed") {
    badgeHTML = `
      <div class="relative has-tooltip">
        <span class="bg-rose-100 text-rose-800 text-xs px-2.5 py-1 rounded-full font-semibold flex items-center space-x-1 cursor-pointer">
          <i data-lucide="x-circle" class="w-3.5 h-3.5"></i><span>Failed</span>
        </span>
        <div class="tooltip absolute z-30 bottom-full left-1/2 -translate-x-1/2 mb-2 w-72 p-2.5 bg-slate-900 text-white text-xs rounded-lg shadow-xl leading-relaxed">
          ${doc.warningReason || "Extraction request failed."}
        </div>
      </div>`;
  }

  const isExtracted = doc.status === "reconciled" || doc.status === "warning";

  return `
    <div class="flex items-center space-x-3 truncate">
      <div class="w-8 h-8 rounded-lg bg-slate-100 flex items-center justify-center text-slate-500">
        <i data-lucide="file-text" class="w-4 h-4"></i>
      </div>
      <div class="truncate">
        <h4 class="text-sm font-semibold text-slate-800 truncate">${doc.name}</h4>
        <span class="text-xs text-slate-400">${(doc.size / 1024).toFixed(1)} KB</span>
      </div>
    </div>

    <div class="flex items-center space-x-2">
      ${badgeHTML}
      ${
        isExtracted
          ? `<button onclick="viewExtractedData('${doc.id}')" class="px-3.5 py-1.5 text-xs bg-indigo-50 hover:bg-indigo-100 text-indigo-700 font-semibold rounded-lg transition border border-indigo-200">View Data</button>`
          : `<button onclick="triggerExtraction('${doc.id}')" class="px-3.5 py-1.5 text-xs bg-indigo-600 hover:bg-indigo-700 text-white font-semibold rounded-lg shadow-sm transition">Extract</button>`
      }
      <button onclick="deleteDocument('${doc.id}')" class="p-1.5 text-slate-400 hover:text-rose-600 hover:bg-rose-50 rounded-lg transition" title="Delete ${doc.name}"><i data-lucide="trash-2" class="w-4 h-4"></i></button>
    </div>
  `;
}

// ===========================================================================
// 4. EXTRACTION EXECUTION
// ===========================================================================
async function triggerExtraction(docId) {
  const doc = documentsStore.find((d) => d.id === docId);
  if (!doc) return;

  const key = sessionStorage.getItem("underwriter_key");
  const prov = sessionStorage.getItem("underwriter_provider") || "openrouter";
  const model = sessionStorage.getItem("underwriter_model") || "openrouter/auto";
  const baseUrl = sessionStorage.getItem("underwriter_base_url") || "";

  if (!key) {
    openSettingsModal();
    alert("Please enter an API key in settings before extracting.");
    return;
  }

  if (!doc.fileObject) {
    alert("File binary not found in browser memory (re-upload the file to extract live).");
    return;
  }

  doc.status = "extracting";
  renderDocumentTree();

  const jobId = "job_" + Math.random().toString(36).substr(2, 9);
  const formData = new FormData();
  formData.append("file", doc.fileObject);
  formData.append("job_id", jobId);

  try {
    const res = await fetch("/api/extract", {
      method: "POST",
      headers: {
        "x-provider": prov,
        "x-api-key": key,
        "x-model": model,
        "x-base-url": baseUrl,
      },
      body: formData,
    });

    const payload = await res.json();
    if (!res.ok) throw new Error(payload.message || "Extraction failed");

    const r = payload.result;
    doc.result = r;
    doc.status = r.status === "success" ? "reconciled" : r.status === "warning" ? "warning" : "failed";
    doc.warningReason = r.warningReason;
  } catch (err) {
    doc.status = "failed";
    doc.warningReason = err.message;
    alert("Error: " + err.message);
  } finally {
    saveStoredDocuments();
    renderDocumentTree();
  }
}

// ===========================================================================
// 5. DEMO DATA GENERATOR
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
  alert("Loaded 2 demo statements into the library! Click 'View Data' on either document to explore.");
}

// ===========================================================================
// 6. SPLIT VIEWER & MODAL WORKSPACE
// ===========================================================================
function viewExtractedData(docId) {
  const doc = documentsStore.find((d) => d.id === docId);
  if (!doc || !doc.result) return;
  currentViewingDoc = doc;
  const originalButton = document.getElementById("downloadOriginalBtn");
  originalButton.disabled = !doc.fileObject;
  originalButton.classList.toggle("opacity-50", !doc.fileObject);
  originalButton.classList.toggle("cursor-not-allowed", !doc.fileObject);

  document.getElementById("modalDocTitle").textContent = doc.name;
  const res = doc.result;
  const data = res.extractedData || {};

  document.getElementById("kpiBegBal").textContent = money(data.beginningBalance);
  document.getElementById("kpiDeposits").textContent = money(data.totalDeposits);
  document.getElementById("kpiWithdrawals").textContent = money(data.totalWithdrawals);
  document.getElementById("kpiEndBal").textContent = money(data.endingBalance);

  document.getElementById("valBank").textContent = data.bankName || "N/A";
  document.getElementById("valAccNum").textContent = data.accountNumber || "N/A";
  document.getElementById("valHolder").textContent = data.accountHolder || "N/A";
  document.getElementById("valPeriod").textContent = `${data.startdate || ""} to ${data.enddate || ""}`;

  renderAuditTab(res.reconciliation);
  renderTransactionsGrid(data.transactions || []);
  renderMcaTab(res.underwriting || {});
  loadDocPreview(doc.fileObject);

  switchTab("summary");
  document.getElementById("dataViewerModal").classList.remove("hidden");
  if (window.lucide) lucide.createIcons();
}

function renderAuditTab(audit) {
  const banner = document.getElementById("auditBanner");
  const tbody = document.getElementById("auditTableBody");
  tbody.innerHTML = "";

  if (!audit) return;

  banner.className = `p-4 rounded-xl flex items-start space-x-3 ${
    audit.passed
      ? "bg-emerald-50 text-emerald-900 border border-emerald-200"
      : "bg-amber-50 text-amber-900 border border-amber-200"
  }`;
  banner.innerHTML = `
    <i data-lucide="${audit.passed ? "check-circle" : "alert-triangle"}" class="w-5 h-5 flex-shrink-0 mt-0.5"></i>
    <div>
      <h4 class="font-bold text-sm">${audit.passed ? "Mathematical Verification: PASSED" : "Mathematical Warning / Discrepancy"}</h4>
      <p class="text-xs mt-1">${audit.warningReason || "All extracted lines reconciled with the statement summary to $0.00."}</p>
    </div>
  `;

  (audit.checks || []).forEach((c) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="py-3 px-4 font-medium text-slate-800">${c.name}</td>
      <td class="py-3 px-4"><span class="px-2 py-0.5 rounded text-xs font-semibold ${
        c.passed ? "bg-emerald-100 text-emerald-800" : "bg-rose-100 text-rose-800"
      }">${c.passed ? "PASS" : "FAIL"}</span></td>
      <td class="py-3 px-4 text-right font-mono">${c.diff !== null ? money(c.diff) : "N/A"}</td>
    `;
    tbody.appendChild(tr);
  });
}

function renderTransactionsGrid(txns) {
  const tbody = document.getElementById("txnsTableBody");
  tbody.innerHTML = "";

  txns.forEach((tx) => {
    const tr = document.createElement("tr");
    const isPositive = Number(tx.amount) > 0 || tx.type === "credit";
    const isReturned = tx.type === "returned_item";
    tr.className = "hover:bg-slate-50 cursor-pointer";
    tr.dataset.transactionId = tx.trID || "";
    tr.onclick = () => focusTransaction(tx.trID);
    tr.innerHTML = `
      <td class="py-2 px-3 text-slate-400">${tx.trID}</td>
      <td class="py-2 px-3 text-slate-600">${tx.postingDate}</td>
      <td class="py-2 px-4 text-slate-800 font-sans truncate max-w-xs" title="${tx.description}">${tx.description}</td>
      <td class="py-2 px-3 text-right font-bold ${isPositive ? "text-emerald-600" : "text-rose-600"}">${signedMoney(tx.amount, isPositive)}</td>
      <td class="py-2 px-3"><span class="px-2 py-0.5 rounded text-[10px] font-semibold uppercase ${
        isPositive ? "bg-emerald-50 text-emerald-700" : isReturned ? "bg-rose-50 text-rose-700" : "bg-rose-50 text-rose-700"
      }">${(tx.type || "unclassified").replace("_", " ")}</span></td>
      <td class="py-2 px-3 text-right text-slate-500">${money(tx.balance)}</td>
    `;
    tbody.appendChild(tr);
  });
}

function renderMcaTab(uw) {
  document.getElementById("mcaCount").textContent = uw.mca_count || 0;
  document.getElementById("loanCount").textContent = uw.loan_count || 0;
  document.getElementById("nsfCount").textContent = uw.returned_count || 0;

  const container = document.getElementById("mcaDetailsList");
  container.innerHTML = "";

  const matchingTransactions = currentViewingDoc?.result?.extractedData?.transactions || [];
  const items = (uw.analyzed_transactions || []).filter((t) => t.isMca || t.isBusinessLoan || t.isCreditCard || t.isBnpl || t.isReturnedItem);
  if (!items.length) {
    container.innerHTML = '<div class="rounded-xl border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">No MCA, loan, card, BNPL, or returned-item risks were detected.</div>';
    return;
  }
  items.forEach((item) => {
      const transaction = matchingTransactions.find((tx) => tx.trID === item.trID) || {};
      const kind = item.isMca ? "MCA" : item.isBusinessLoan ? "Business loan" : item.isCreditCard ? "Credit card" : item.isBnpl ? "BNPL" : "Returned item";
      const style = item.isMca ? "amber" : item.isReturnedItem ? "rose" : "blue";
      const card = document.createElement("div");
      card.className =
        "p-4 border border-slate-200 rounded-xl bg-white text-xs shadow-sm hover:border-indigo-300 transition";
      card.innerHTML = `
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0"><div class="flex flex-wrap items-center gap-2"><span class="font-bold text-slate-900">${item.lenderName || item.loanLenderName || item.creditorName || item.bnplProvider || "Risk signal"}</span><span class="px-2 py-0.5 rounded bg-${style}-100 text-${style}-800 font-semibold">${kind}</span></div>
          <p class="mt-1 text-slate-500 truncate" title="${transaction.description || ""}">${transaction.description || item.trCategory || "Classified financial transaction"}</p></div>
          <button class="shrink-0 text-indigo-600 hover:text-indigo-800 font-semibold" onclick="focusTransaction('${item.trID}')">View transaction</button>
        </div>
        <div class="mt-3 flex items-center justify-between border-t pt-2 text-slate-500"><span>${transaction.postingDate || "Date unavailable"} · ${item.trID}</span><span class="font-mono font-bold ${Number(transaction.amount) < 0 ? "text-rose-600" : "text-emerald-600"}">${transaction.amount === undefined ? "" : signedMoney(transaction.amount)}</span></div>
      `;
      container.appendChild(card);
    });
}

function focusTransaction(trID) {
  switchTab("txns");
  const row = document.querySelector(`[data-transaction-id="${trID}"]`);
  if (row) {
    row.scrollIntoView({ behavior: "smooth", block: "center" });
    row.classList.add("bg-indigo-50");
    window.setTimeout(() => row.classList.remove("bg-indigo-50"), 1600);
  }
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
    pages.innerHTML = '<div class="w-80 rounded-xl border border-dashed border-slate-300 bg-white p-8 text-center text-sm text-slate-500">Preview is available for uploaded source files. Demo records do not include an original document.</div>';
    imgEl.classList.add("hidden");
    if (indicator) indicator.textContent = "Synthetic Demo";
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
      pages.innerHTML = '<div class="rounded-xl bg-rose-50 p-4 text-sm text-rose-700">The PDF could not be rendered in this browser. You can still download the original file.</div>';
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
    wrapper.className = "relative rounded bg-white p-1 shadow-md";
    const label = document.createElement("span");
    label.className = "absolute right-2 top-2 rounded bg-slate-900/75 px-2 py-0.5 text-[10px] text-white";
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
  pdfZoom = Math.min(2, Math.max(0.6, +(pdfZoom + change).toFixed(2)));
  renderAllPdfPages();
}

function scrollPdfPage(direction) {
  if (!currentPdf) return;
  const container = document.getElementById("pdfViewerContainer");
  const sections = [...document.querySelectorAll("[id^='pdf-page-']")];
  const currentIndex = sections.reduce((best, node, index) => Math.abs(node.offsetTop - container.scrollTop) < Math.abs(sections[best].offsetTop - container.scrollTop) ? index : best, 0);
  sections[Math.max(0, Math.min(sections.length - 1, currentIndex + direction))]?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function switchTab(tabId) {
  ["summary", "audit", "txns", "mca"].forEach((t) => {
    const content = document.getElementById(`tabContent-${t}`);
    const btn = document.getElementById(`tabBtn-${t}`);
    if (content) content.classList.add("hidden");
    if (btn) {
      btn.classList.remove("border-indigo-600", "text-indigo-600", "font-semibold");
      btn.classList.add("border-transparent", "text-slate-500");
    }
  });

  const activeContent = document.getElementById(`tabContent-${tabId}`);
  const activeBtn = document.getElementById(`tabBtn-${tabId}`);
  if (activeContent) activeContent.classList.remove("hidden");
  if (activeBtn) {
    activeBtn.classList.add("border-indigo-600", "text-indigo-600", "font-semibold");
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

function openSettingsModal() {
  document.getElementById("settingsModal").classList.remove("hidden");
}

function closeSettingsModal() {
  document.getElementById("settingsModal").classList.add("hidden");
}

function togglePasswordVisibility() {
  const el = document.getElementById("settingApiKey");
  if (el) el.type = el.type === "password" ? "text" : "password";
}

function money(v) {
  return v === null || v === undefined
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
    alert("The original file is not available for this demo or legacy record.");
    return;
  }
  const href = URL.createObjectURL(file);
  const a = document.createElement("a");
  a.href = href;
  a.download = currentViewingDoc.name || file.name;
  a.click();
  window.setTimeout(() => URL.revokeObjectURL(href), 1000);
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
}
