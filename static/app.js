/**
 * Universal Statement Underwriting Engine — Unified Frontend Logic
 */

let providersRegistry = {};
let documentsStore = [];
let currentViewingDoc = null;

// Initialize Lucide icons
document.addEventListener("DOMContentLoaded", async () => {
  lucide.createIcons();
  await loadProviders();
  loadStoredSettings();
  loadDocumentsFromIndexedDB();
  setupDropZone();
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
  const provider = sessionStorage.getItem("underwriter_provider") || "openai";
  const model = sessionStorage.getItem("underwriter_model") || "gpt-4o";
  const key = sessionStorage.getItem("underwriter_key") || "";
  const baseUrl = sessionStorage.getItem("underwriter_base_url") || "";

  document.getElementById("settingProvider").value = provider;
  document.getElementById("settingApiKey").value = key;
  document.getElementById("settingBaseUrl").value = baseUrl;

  populateModelsDropdown();
  updateHeaderBadge(provider, model);
}

// In static/app.js, replace populateModelsDropdown:
function populateModelsDropdown() {
  const prov = document.getElementById("settingProvider").value;
  const cfg = providersRegistry[prov] || { models: [] };
  const sel = document.getElementById("settingModelSelect");
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

  const urlInput = document.getElementById("settingBaseUrl");
  if (cfg.base_url) {
    urlInput.value = cfg.base_url;
  }

  document.getElementById("customBaseUrlWrapper").style.display = (prov === "custom") ? "block" : "none";
}

function onModelSelectChanged() {
  const isOther = document.getElementById("settingModelSelect").value === "other";
  document.getElementById("settingModelCustom").style.display = isOther ? "block" : "none";
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
  const baseUrl = document.getElementById("settingBaseUrl").value.trim();

  sessionStorage.setItem("underwriter_provider", prov);
  sessionStorage.setItem("underwriter_key", key);
  sessionStorage.setItem("underwriter_model", finalModel);
  sessionStorage.setItem("underwriter_base_url", baseUrl);

  updateHeaderBadge(prov, finalModel);
  closeSettingsModal();
}

function updateHeaderBadge(prov, model) {
  const b = document.getElementById("activeModelBadge");
  const l = document.getElementById("activeModelLabel");
  b.classList.remove("hidden");
  l.textContent = `${prov.toUpperCase()}: ${model}`;
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
        "x-base-url": baseUrl
      }
    });
    const data = await res.json();
    if (res.ok) {
      alert("✅ " + data.message);
    } else {
      alert("❌ Connection Failed: " + data.detail);
    }
  } catch (err) {
    alert("Connection Error: " + err.message);
  } finally {
    btn.textContent = "Test Connection";
    btn.disabled = false;
  }
}

// ===========================================================================
// 2. FILE UPLOAD & INDEXEDDB LOCAL PERSISTENCE
// ===========================================================================
function setupDropZone() {
  const dz = document.getElementById("dropZone");
  const fin = document.getElementById("fileInput");

  dz.onclick = () => fin.click();
  fin.onchange = (e) => handleFiles(e.target.files);

  dz.ondragover = (e) => { e.preventDefault(); dz.classList.add("border-indigo-600"); };
  dz.ondragleave = () => dz.classList.remove("border-indigo-600");
  dz.ondrop = (e) => {
    e.preventDefault();
    dz.classList.remove("border-indigo-600");
    handleFiles(e.dataTransfer.files);
  };
}

function handleFiles(files) {
  Array.from(files).forEach((file) => {
    const docId = "doc_" + Math.random().toString(36).substr(2, 9);
    const newDoc = {
      id: docId,
      name: file.name,
      size: file.size,
      uploadDate: new Date().toISOString(),
      fileObject: file,
      status: "pending", // pending, extracting, reconciled, warning, failed
      result: null,
      warningReason: null
    };
    documentsStore.unshift(newDoc);
  });
  renderDocumentTree();
}

function renderDocumentTree() {
  const container = document.getElementById("documentLibrary");
  const search = document.getElementById("searchInput").value.toLowerCase();
  const filter = document.getElementById("statusFilter").value;

  container.innerHTML = "";

  // Filter items
  const filtered = documentsStore.filter((doc) => {
    const matchesSearch = doc.name.toLowerCase().includes(search);
    if (filter === "all") return matchesSearch;
    if (filter === "reconciled") return matchesSearch && doc.status === "reconciled";
    if (filter === "warning") return matchesSearch && doc.status === "warning";
    if (filter === "pending") return matchesSearch && doc.status === "pending";
    return matchesSearch;
  });

  if (filtered.length === 0) {
    container.innerHTML = `<div class="p-8 text-center text-slate-400 border border-slate-200 rounded-xl bg-white text-sm">No matching documents found.</div>`;
    return;
  }

  // Group by Date (Today, Yesterday, or YYYY-MM-DD)
  const groups = {};
  filtered.forEach((doc) => {
    const d = new Date(doc.uploadDate);
    const today = new Date();
    let label = d.toLocaleDateString();
    if (d.toDateString() === today.toDateString()) label = "Today";
    else if (new Date(today.setDate(today.getDate() - 1)).toDateString() === d.toDateString()) label = "Yesterday";

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
      <div class="divide-y divide-slate-100" id="group_${dateLabel}"></div>
    `;

    container.appendChild(groupCard);
    const listContainer = groupCard.querySelector(`#group_${CSS.escape(dateLabel)}`);

    docs.forEach((doc) => {
      const row = document.createElement("div");
      row.className = "py-3 flex items-center justify-between";
      row.innerHTML = buildDocRowHTML(doc);
      listContainer.appendChild(row);
    });
  });

  lucide.createIcons();
}

function buildDocRowHTML(doc) {
  let badgeHTML = "";

  if (doc.status === "pending") {
    badgeHTML = `<span class="bg-slate-100 text-slate-600 text-xs px-2.5 py-1 rounded-full font-medium">Not Extracted</span>`;
  } else if (doc.status === "reconciled") {
    badgeHTML = `<span class="bg-emerald-100 text-emerald-800 text-xs px-2.5 py-1 rounded-full font-semibold flex items-center space-x-1">
      <i data-lucide="check-circle" class="w-3.5 h-3.5"></i><span>Reconciled ($0.00 Diff)</span>
    </span>`;
  } else if (doc.status === "warning") {
    badgeHTML = `
      <div class="relative has-tooltip">
        <span class="bg-amber-100 text-amber-900 text-xs px-2.5 py-1 rounded-full font-semibold flex items-center space-x-1 cursor-pointer">
          <i data-lucide="alert-triangle" class="w-3.5 h-3.5 text-amber-600"></i><span>Math Warning</span>
        </span>
        <div class="tooltip absolute z-30 bottom-full left-1/2 -translate-x-1/2 mb-2 w-72 p-2.5 bg-slate-900 text-white text-xs rounded-lg shadow-xl leading-relaxed">
          ${doc.warningReason || "Discrepancy detected between transactions and printed totals."}
        </div>
      </div>
    `;
  } else if (doc.status === "extracting") {
    badgeHTML = `<span class="bg-indigo-100 text-indigo-800 text-xs px-2.5 py-1 rounded-full font-medium animate-pulse">Extracting...</span>`;
  }

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

    <div class="flex items-center space-x-3">
      ${badgeHTML}
      ${
        doc.status === "pending" || doc.status === "extracting"
          ? `<button onclick="triggerExtraction('${doc.id}')" class="px-3.5 py-1.5 text-xs bg-indigo-600 hover:bg-indigo-700 text-white font-semibold rounded-lg shadow-sm transition">Extract</button>`
          : `<button onclick="viewExtractedData('${doc.id}')" class="px-3.5 py-1.5 text-xs bg-slate-100 hover:bg-slate-200 text-slate-700 font-semibold rounded-lg transition">View Data</button>`
      }
    </div>
  `;
}

// ===========================================================================
// 3. EXECUTE UNIFIED EXTRACTION PIPELINE & SSE PROGRESS
// ===========================================================================
async function triggerExtraction(docId) {
  const doc = documentsStore.find((d) => d.id === docId);
  const key = sessionStorage.getItem("underwriter_key");
  const prov = sessionStorage.getItem("underwriter_provider") || "openai";
  const model = sessionStorage.getItem("underwriter_model") || "gpt-4o";
  const baseUrl = sessionStorage.getItem("underwriter_base_url") || "";

  if (!key) {
    openSettingsModal();
    alert("Please provide an API key to extract.");
    return;
  }

  doc.status = "extracting";
  renderDocumentTree();

  const jobId = "job_" + Math.random().toString(36).substr(2, 9);

  // Connect SSE for real-time stage updates
  const evtSource = new EventSource(`/api/progress/${jobId}`);
  evtSource.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.done) evtSource.close();
  };

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
        "x-base-url": baseUrl
      },
      body: formData
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
    alert("Error extracting " + doc.name + ": " + err.message);
  } finally {
    renderDocumentTree();
  }
}

// ===========================================================================
// 4. SPLIT VIEWER & DATA WORKSPACE
// ===========================================================================
function viewExtractedData(docId) {
  const doc = documentsStore.find((d) => d.id === docId);
  if (!doc || !doc.result) return;
  currentViewingDoc = doc;

  document.getElementById("modalDocTitle").textContent = doc.name;
  const res = doc.result;
  const data = res.extractedData || {};

  // Render KPIs
  document.getElementById("kpiBegBal").textContent = money(data.beginningBalance);
  document.getElementById("kpiDeposits").textContent = money(data.totalDeposits);
  document.getElementById("kpiWithdrawals").textContent = money(data.totalWithdrawals);
  document.getElementById("kpiEndBal").textContent = money(data.endingBalance);

  document.getElementById("valBank").textContent = data.bankName || "N/A";
  document.getElementById("valAccNum").textContent = data.accountNumber || "N/A";
  document.getElementById("valHolder").textContent = data.accountHolder || "N/A";
  document.getElementById("valPeriod").textContent = `${data.startdate || ""} to ${data.enddate || ""}`;

  // Render Audit Tab
  renderAuditTab(res.reconciliation);

  // Render Transactions DataGrid
  renderTransactionsGrid(data.transactions || []);

  // Render MCA Tab
  renderMcaTab(res.underwriting || {});

  // Load PDF Preview
  loadDocPreview(doc.fileObject);

  document.getElementById("dataViewerModal").classList.remove("hidden");
  lucide.createIcons();
}

function renderAuditTab(audit) {
  const banner = document.getElementById("auditBanner");
  const tbody = document.getElementById("auditTableBody");
  tbody.innerHTML = "";

  if (!audit) return;

  banner.className = `p-4 rounded-xl flex items-start space-x-3 ${audit.passed ? "bg-emerald-50 text-emerald-900 border border-emerald-200" : "bg-amber-50 text-amber-900 border border-amber-200"}`;
  banner.innerHTML = `
    <i data-lucide="${audit.passed ? "check-circle" : "alert-triangle"}" class="w-5 h-5 flex-shrink-0 mt-0.5"></i>
    <div>
      <h4 class="font-bold text-sm">${audit.passed ? "Mathematical Verification: PASSED" : "Mathematical Discrepancy Detected"}</h4>
      <p class="text-xs mt-1">${audit.warningReason || "All extracted lines reconciled with the statement summary to $0.00."}</p>
    </div>
  `;

  (audit.checks || []).forEach((c) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="py-3 px-4 font-medium text-slate-800">${c.name}</td>
      <td class="py-3 px-4"><span class="px-2 py-0.5 rounded text-xs font-semibold ${c.passed ? "bg-emerald-100 text-emerald-800" : "bg-rose-100 text-rose-800"}">${c.passed ? "PASS" : "FAIL"}</span></td>
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
    tr.className = "hover:bg-slate-50";
    tr.innerHTML = `
      <td class="py-2 px-3 text-slate-400">${tx.trID}</td>
      <td class="py-2 px-3 text-slate-600">${tx.postingDate}</td>
      <td class="py-2 px-4 text-slate-800 font-sans truncate max-w-xs" title="${tx.description}">${tx.description}</td>
      <td class="py-2 px-3 text-right font-bold ${tx.type === "credit" ? "text-emerald-600" : "text-slate-800"}">${money(tx.amount)}</td>
      <td class="py-2 px-3"><span class="px-2 py-0.5 rounded text-[10px] font-semibold uppercase ${tx.type === "credit" ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-700"}">${tx.type}</span></td>
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

  (uw.analyzed_transactions || []).filter((t) => t.isMca || t.isBusinessLoan || t.isCreditCard).forEach((item) => {
    const card = document.createElement("div");
    card.className = "p-3 border border-slate-200 rounded-xl bg-slate-50 text-xs flex justify-between items-center";
    card.innerHTML = `
      <div>
        <span class="font-bold text-slate-800">${item.lenderName || item.loanLenderName || item.creditorName || "Financing Position"}</span>
        <span class="ml-2 px-2 py-0.5 rounded bg-amber-100 text-amber-800 font-semibold">${item.trCategory}</span>
      </div>
      <span class="font-mono text-slate-500">${item.trID}</span>
    `;
    container.appendChild(card);
  });
}

// PDF.js In-Browser Rendering
async function loadDocPreview(file) {
  if (!file) return;
  const canvas = document.getElementById("pdfCanvas");
  const imgEl = document.getElementById("imageViewer");

  if (file.type.includes("pdf")) {
    canvas.classList.remove("hidden");
    imgEl.classList.add("hidden");
    const arrayBuffer = await file.arrayBuffer();
    const pdf = await pdfjsLib.getDocument({ data: arrayBuffer }).promise;
    const page = await pdf.getPage(1);
    const viewport = page.getViewport({ scale: 1.2 });
    canvas.height = viewport.height;
    canvas.width = viewport.width;
    await page.render({ canvasContext: canvas.getContext("2d"), viewport }).promise;
  } else {
    canvas.classList.add("hidden");
    imgEl.classList.remove("hidden");
    imgEl.src = URL.createObjectURL(file);
  }
}

// Tab Switching
function switchTab(tabId) {
  ["summary", "audit", "txns", "mca"].forEach((t) => {
    document.getElementById(`tabContent-${t}`).classList.add("hidden");
    document.getElementById(`tabBtn-${t}`).classList.remove("border-indigo-600", "text-indigo-600", "font-semibold");
    document.getElementById(`tabBtn-${t}`).classList.add("border-transparent", "text-slate-500");
  });
  document.getElementById(`tabContent-${tabId}`).classList.remove("hidden");
  document.getElementById(`tabBtn-${tabId}`).classList.add("border-indigo-600", "text-indigo-600", "font-semibold");
}

function closeViewerModal() {
  document.getElementById("dataViewerModal").classList.add("hidden");
}

function openSettingsModal() {
  document.getElementById("settingsModal").classList.remove("hidden");
}

function closeSettingsModal() {
  document.getElementById("settingsModal").classList.add("hidden");
}

function togglePasswordVisibility() {
  const el = document.getElementById("settingApiKey");
  el.type = el.type === "password" ? "text" : "password";
}

function money(v) {
  return v === null || v === undefined ? "N/A" : `$${parseFloat(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

// Exports
function downloadJsonExport() {
  if (!currentViewingDoc) return;
  const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(currentViewingDoc.result, null, 2));
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
    csv += `"${t.trID}","${t.postingDate}","${t.description.replace(/"/g, '""')}",${t.amount},"${t.type}",${t.balance}\n`;
  });
  const a = document.createElement("a");
  a.href = "data:text/csv;charset=utf-8," + encodeURIComponent(csv);
  a.download = `${currentViewingDoc.name}_transactions.csv`;
  a.click();
}
