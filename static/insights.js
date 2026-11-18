/**
 * UnderwriteIQ — Insights layer (UI only)
 * Charts, portfolio view, transaction flags, command palette, printable summary.
 * Reads data already extracted by the backend; never calls the API.
 */

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------
const CAT_STYLE = {
  mca: { hex: "#f59e0b", short: "MCA", label: "MCA Positions" },
  loan: { hex: "#6366f1", short: "Loan", label: "Business Loans" },
  cc: { hex: "#3b82f6", short: "Card", label: "Credit Cards" },
  bnpl: { hex: "#14b8a6", short: "BNPL", label: "BNPL" },
  returned: { hex: "#f43f5e", short: "NSF", label: "Returned / NSF" },
};

let currentFlagMap = new Map();

/** Map trID -> {key, hex, short, label, name} for one extraction result. */
function buildFlagMap(res) {
  const map = new Map();
  (res?.underwriting?.analyzed_transactions || []).forEach((t) => {
    if (!t || typeof t !== "object") return;
    const cat = MCA_CATEGORIES.find((c) => c.test(t));
    if (!cat) return;
    const name = cat.name(t) || t.lenderName || t.loanLenderName || t.creditorName || t.bnplProvider || null;
    map.set(t.trID, { key: cat.key, ...CAT_STYLE[cat.key], name });
  });
  return map;
}

function setCurrentFlags(res) { currentFlagMap = buildFlagMap(res); }
function getFlag(trID) { return currentFlagMap.get(trID); }

function fmtK(v) {
  const n = Math.abs(Number(v) || 0);
  const sign = Number(v) < 0 ? "−" : "";
  if (n >= 1e6) return `${sign}$${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${sign}$${(n / 1e3).toFixed(n >= 1e4 ? 0 : 1)}k`;
  return `${sign}$${n.toFixed(0)}`;
}

function usageLabel(res) {
  const u = res?.usage;
  if (!u || !u.total_tokens) return "";
  const tokens = u.total_tokens >= 1000 ? `${(u.total_tokens / 1000).toFixed(1)}k tokens` : `${u.total_tokens} tokens`;
  return u.cost > 0 ? `${tokens} · $${u.cost.toFixed(4)}` : tokens;
}

function monthKey(dateStr) {
  const m = /^(\d{4})-(\d{2})/.exec(dateStr || "");
  return m ? `${m[1]}-${m[2]}` : null;
}

function monthLabel(key) {
  const [y, m] = key.split("-");
  return `${["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][Number(m) - 1]} ${y.slice(2)}`;
}

function isCreditTx(tx) { return tx.type === "credit"; }
function isDebitTx(tx) { return tx.type === "debit"; }

function aggregateMonthly(txns) {
  const months = new Map();
  txns.forEach((tx) => {
    const k = monthKey(tx.postingDate);
    if (!k) return;
    const row = months.get(k) || { key: k, in: 0, out: 0 };
    const amt = Math.abs(Number(tx.amount) || 0);
    if (isCreditTx(tx)) row.in += amt;
    else if (isDebitTx(tx)) row.out += amt;
    months.set(k, row);
  });
  return [...months.values()].sort((a, b) => a.key.localeCompare(b.key));
}

// ---------------------------------------------------------------------------
// SVG charts
// ---------------------------------------------------------------------------
function chartCard(title, icon, inner, note = "") {
  return `<section class="border border-slate-200 dark:border-slate-800 rounded-xl p-4 bg-white dark:bg-slate-850 shadow-sm min-w-0">
    <div class="flex items-center justify-between mb-2">
      <h4 class="text-xs font-semibold text-slate-700 dark:text-slate-200 flex items-center gap-1.5"><i data-lucide="${icon}" class="w-3.5 h-3.5 text-slate-400"></i>${title}</h4>
      <span class="text-[10px] text-slate-400">${note}</span>
    </div>${inner}</section>`;
}

function emptyChart(msg) {
  return `<p class="text-[11px] text-slate-400 py-8 text-center">${msg}</p>`;
}

function barChartSVG(months) {
  if (!months.length) return emptyChart("No dated transactions to chart.");
  const W = 600, H = 220, padL = 44, padR = 8, padT = 10, padB = 28;
  const max = Math.max(...months.map((m) => Math.max(m.in, m.out)), 1);
  const innerW = W - padL - padR, innerH = H - padT - padB;
  const group = innerW / months.length;
  const barW = Math.min(26, group / 2.6);
  const y = (v) => padT + innerH - (v / max) * innerH;
  let out = `<svg viewBox="0 0 ${W} ${H}" class="w-full h-auto text-slate-400" role="img" aria-label="Monthly deposits versus withdrawals">`;
  for (let i = 0; i <= 4; i++) {
    const v = (max / 4) * i, yy = y(v);
    out += `<line x1="${padL}" x2="${W - padR}" y1="${yy}" y2="${yy}" stroke="currentColor" stroke-opacity=".15"/><text x="${padL - 6}" y="${yy + 3}" text-anchor="end" class="chart-text">${fmtK(v)}</text>`;
  }
  months.forEach((m, i) => {
    const cx = padL + group * i + group / 2;
    out += `<rect x="${cx - barW - 1}" y="${y(m.in)}" width="${barW}" height="${padT + innerH - y(m.in)}" rx="3" fill="#10b981"><title>${monthLabel(m.key)} deposits: ${money(m.in)}</title></rect>`;
    out += `<rect x="${cx + 1}" y="${y(m.out)}" width="${barW}" height="${padT + innerH - y(m.out)}" rx="3" fill="#f43f5e"><title>${monthLabel(m.key)} withdrawals: ${money(m.out)}</title></rect>`;
    out += `<text x="${cx}" y="${H - 10}" text-anchor="middle" class="chart-text">${monthLabel(m.key)}</text>`;
  });
  return out + "</svg>" + legend([["Deposits", "#10b981"], ["Withdrawals", "#f43f5e"]]);
}

function legend(items) {
  return `<div class="flex flex-wrap gap-x-4 gap-y-1 mt-2 text-[11px] text-slate-500 dark:text-slate-400">${items
    .map(([l, c]) => `<span class="flex items-center gap-1.5"><span class="w-2 h-2 rounded-full" style="background:${c}"></span>${l}</span>`).join("")}</div>`;
}

function lineChartSVG(txns) {
  const pts = txns
    .filter((t) => t.postingDate && t.balance !== null && t.balance !== undefined && !Number.isNaN(Number(t.balance)))
    .map((t, i) => ({ d: t.postingDate, v: Number(t.balance), i }))
    .sort((a, b) => a.d.localeCompare(b.d) || a.i - b.i);
  if (pts.length < 2) return emptyChart("This statement doesn't print running balances.");
  const W = 600, H = 200, padL = 44, padR = 8, padT = 10, padB = 24;
  const min = Math.min(...pts.map((p) => p.v)), max = Math.max(...pts.map((p) => p.v));
  const span = max - min || 1;
  const x = (i) => padL + (i / (pts.length - 1)) * (W - padL - padR);
  const y = (v) => padT + (1 - (v - min) / span) * (H - padT - padB);
  const line = pts.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.v).toFixed(1)}`).join(" ");
  const area = `${line} L${x(pts.length - 1)},${H - padB} L${x(0)},${H - padB} Z`;
  const lowIdx = pts.reduce((b, p, i) => (p.v < pts[b].v ? i : b), 0);
  let out = `<svg viewBox="0 0 ${W} ${H}" class="w-full h-auto text-slate-400" role="img" aria-label="Running balance over time">`;
  for (let i = 0; i <= 3; i++) {
    const v = min + (span / 3) * i, yy = y(v);
    out += `<line x1="${padL}" x2="${W - padR}" y1="${yy}" y2="${yy}" stroke="currentColor" stroke-opacity=".15"/><text x="${padL - 6}" y="${yy + 3}" text-anchor="end" class="chart-text">${fmtK(v)}</text>`;
  }
  out += `<path d="${area}" fill="#6366f1" fill-opacity=".12"/><path d="${line}" fill="none" stroke="#6366f1" stroke-width="2" stroke-linejoin="round"/>`;
  out += `<circle cx="${x(lowIdx)}" cy="${y(pts[lowIdx].v)}" r="4" fill="#f43f5e"><title>Lowest: ${money(pts[lowIdx].v)} on ${pts[lowIdx].d}</title></circle>`;
  out += `<text x="${padL}" y="${H - 6}" class="chart-text">${pts[0].d}</text><text x="${W - padR}" y="${H - 6}" text-anchor="end" class="chart-text">${pts[pts.length - 1].d}</text></svg>`;
  return out + legend([["Running balance", "#6366f1"], [`Lowest ${money(pts[lowIdx].v)}`, "#f43f5e"]]);
}

function donutSVG(segments) {
  const total = segments.reduce((s, x) => s + x.value, 0);
  if (!total) return emptyChart("No MCA, loan, card or BNPL debits detected.");
  const r = 54, c = 2 * Math.PI * r;
  let offset = 0;
  const arcs = segments.filter((s) => s.value > 0).map((s) => {
    const len = (s.value / total) * c;
    const el = `<circle r="${r}" cx="70" cy="70" fill="none" stroke="${s.color}" stroke-width="20" stroke-dasharray="${len.toFixed(2)} ${(c - len).toFixed(2)}" stroke-dashoffset="${(-offset).toFixed(2)}" transform="rotate(-90 70 70)"><title>${s.label}: ${money(s.value)} (${((s.value / total) * 100).toFixed(0)}%)</title></circle>`;
    offset += len;
    return el;
  }).join("");
  return `<div class="flex items-center gap-4 flex-wrap justify-center">
    <svg viewBox="0 0 140 140" class="w-36 h-36 shrink-0" role="img" aria-label="Debt-related debits by category">${arcs}
      <text x="70" y="67" text-anchor="middle" class="chart-text text-slate-500" style="font-size:9px">Total</text>
      <text x="70" y="82" text-anchor="middle" style="font-size:12px;font-weight:700;fill:currentColor">${fmtK(total)}</text></svg>
    <ul class="text-[11px] space-y-1.5 min-w-0">${segments.filter((s) => s.value > 0).map((s) =>
      `<li class="flex items-center gap-2"><span class="w-2.5 h-2.5 rounded-sm shrink-0" style="background:${s.color}"></span><span class="text-slate-600 dark:text-slate-300">${s.label}</span><span class="font-mono text-slate-400 ml-auto pl-3">${money(s.value)}</span></li>`).join("")}</ul></div>`;
}

function categoryTotals(txns, flagMap) {
  const totals = {};
  txns.forEach((tx) => {
    const f = flagMap.get(tx.trID);
    if (!f || f.key === "returned") return;
    totals[f.key] = (totals[f.key] || 0) + Math.abs(Number(tx.amount) || 0);
  });
  return ["mca", "loan", "cc", "bnpl"].map((k) => ({ label: CAT_STYLE[k].label, color: CAT_STYLE[k].hex, value: totals[k] || 0 }));
}

/** Overview-tab charts for the document open in the viewer. */
function renderCharts(doc) {
  const host = document.getElementById("chartsPanel");
  if (!host) return;
  const txns = doc.result?.extractedData?.transactions || [];
  if (!txns.length) { host.innerHTML = ""; return; }
  host.innerHTML =
    chartCard("Monthly Deposits vs Withdrawals", "bar-chart-3", barChartSVG(aggregateMonthly(txns)), "Hover bars for exact values") +
    `<div class="grid grid-cols-1 xl:grid-cols-2 gap-4">` +
    chartCard("Running Balance", "activity", lineChartSVG(txns)) +
    chartCard("Debt-Related Debits", "pie-chart", donutSVG(categoryTotals(txns, currentFlagMap))) +
    `</div>`;
  if (window.lucide) lucide.createIcons();
}

// ---------------------------------------------------------------------------
// Portfolio insights (across every extracted statement)
// ---------------------------------------------------------------------------
function accountKey(bank, num) { return `${bank}__${num}`.toLowerCase(); }

function collectScopes(filterKey) {
  const scopes = [];
  documentsStore.forEach((doc) => {
    if (!isBankDoc(doc) || !doc.result?.extractedData) return;
    const ext = doc.result.extractedData;
    const flagMap = buildFlagMap(doc.result);
    const push = (key, label, txns, summary) => {
      if (filterKey === "all" || filterKey === key) scopes.push({ doc, ext, key, label, txns, summary, flagMap });
    };
    if (Array.isArray(ext.accounts) && ext.accounts.length) {
      ext.accounts.forEach((a) => {
        const bank = a.bankName || ext.bankName || "Bank";
        push(accountKey(bank, a.accountNumber), `${bank} ••••${String(a.accountNumber || "").slice(-4)}`, a.transactions || [], a);
      });
    } else {
      const bank = ext.bankName || "Bank";
      push(accountKey(bank, ext.accountNumber), `${bank} ••••${String(ext.accountNumber || "").slice(-4)}`, ext.transactions || [], ext);
    }
  });
  return scopes;
}

function openPortfolio() {
  const select = document.getElementById("portfolioAccountSelect");
  const all = collectScopes("all");
  if (!all.length) { showToast("Extract a bank statement first to see portfolio insights", "info"); return; }
  const seen = new Map();
  all.forEach((s) => { if (!seen.has(s.key)) seen.set(s.key, { label: s.label, holder: s.ext.accountHolder }); });
  select.innerHTML = `<option value="all">All accounts (${seen.size})</option>` +
    [...seen].map(([k, v]) => `<option value="${escapeHtml(k)}">${escapeHtml(v.label)}${v.holder ? ` — ${escapeHtml(v.holder)}` : ""}</option>`).join("");
  select.value = seen.has(selectedAccountFilter) ? selectedAccountFilter : "all";
  document.getElementById("portfolioModal").classList.remove("hidden");
  renderPortfolio(select.value);
}

function closePortfolio() { document.getElementById("portfolioModal")?.classList.add("hidden"); }

function renderPortfolio(filterKey) {
  const body = document.getElementById("portfolioBody");
  const scopes = collectScopes(filterKey);
  if (!scopes.length) { body.innerHTML = emptyChart("No statements for this account."); return; }

  const allTx = scopes.flatMap((s) => s.txns);
  const monthly = aggregateMonthly(allTx);
  const deposits = allTx.filter(isCreditTx).reduce((a, t) => a + Math.abs(Number(t.amount) || 0), 0);
  const withdrawals = allTx.filter(isDebitTx).reduce((a, t) => a + Math.abs(Number(t.amount) || 0), 0);
  const avgMonthlyDep = monthly.length ? deposits / monthly.length : 0;
  const balances = allTx.map((t) => Number(t.balance)).filter((n) => !Number.isNaN(n) && n !== null);
  const lowest = balances.length ? Math.min(...balances) : null;
  const endings = scopes.map((s) => Number(s.summary?.endingBalance)).filter((n) => !Number.isNaN(n));
  const avgEnding = endings.length ? endings.reduce((a, b) => a + b, 0) / endings.length : null;

  const totals = { mca: 0, loan: 0, cc: 0, bnpl: 0 };
  let nsf = 0;
  const lenders = new Map();
  scopes.forEach((s) => {
    s.txns.forEach((tx) => {
      const f = s.flagMap.get(tx.trID);
      if (!f) return;
      if (f.key === "returned") { nsf++; return; }
      const amt = Math.abs(Number(tx.amount) || 0);
      totals[f.key] += amt;
      const name = f.name || "Unidentified";
      const k = `${f.key}|${name}`;
      const row = lenders.get(k) || { name, key: f.key, count: 0, total: 0 };
      row.count++; row.total += amt;
      lenders.set(k, row);
    });
  });
  const burden = totals.mca + totals.loan + totals.bnpl;
  const burdenPct = deposits ? (burden / deposits) * 100 : 0;
  const burdenCls = burdenPct >= 25 ? "text-rose-600 dark:text-rose-400" : burdenPct >= 10 ? "text-amber-600 dark:text-amber-400" : "text-emerald-600 dark:text-emerald-400";

  const docs = [...new Set(scopes.map((s) => s.doc))];
  const starts = scopes.map((s) => s.ext.startdate).filter(Boolean).sort();
  const ends = scopes.map((s) => s.ext.enddate).filter(Boolean).sort();
  const warnings = docs.filter((d) => d.status === "warning").length;
  const partials = docs.filter((d) => d.result?.isPartialStatement).length;

  const tile = (label, value, sub = "", cls = "text-slate-800 dark:text-white") =>
    `<div class="p-3.5 rounded-xl bg-slate-50 dark:bg-slate-800/60 border border-slate-200 dark:border-slate-700 min-w-0">
      <span class="text-[10px] uppercase tracking-wider font-medium text-slate-500 dark:text-slate-400">${label}</span>
      <p class="text-base sm:text-lg font-extrabold font-mono mt-0.5 truncate ${cls}">${value}</p>
      <span class="text-[10px] text-slate-400">${sub}</span></div>`;

  const lenderRows = [...lenders.values()].sort((a, b) => b.total - a.total).map((l) => `
    <tr class="border-b border-slate-100 dark:border-slate-800 last:border-0">
      <td class="py-2 px-3 font-semibold text-slate-800 dark:text-slate-100">${escapeHtml(l.name)}</td>
      <td class="py-2 px-3"><span class="px-2 py-0.5 rounded text-[10px] font-bold" style="background:${CAT_STYLE[l.key].hex}22;color:${CAT_STYLE[l.key].hex}">${CAT_STYLE[l.key].short}</span></td>
      <td class="py-2 px-3 text-right font-mono">${l.count}</td>
      <td class="py-2 px-3 text-right font-mono">${money(l.total / l.count)}</td>
      <td class="py-2 px-3 text-right font-mono font-semibold">${money(l.total)}</td></tr>`).join("");

  const stmtRows = docs.map((d) => {
    const e = d.result.extractedData;
    const st = d.status === "reconciled" ? ["Reconciled", "emerald"] : ["Warning", "amber"];
    return `<tr class="border-b border-slate-100 dark:border-slate-800 last:border-0 hover:bg-slate-50 dark:hover:bg-slate-800/40 cursor-pointer" onclick="closePortfolio();viewExtractedData('${d.id}')">
      <td class="py-2 px-3 truncate max-w-[220px]" title="${escapeHtml(d.name)}">${escapeHtml(d.name)}</td>
      <td class="py-2 px-3 font-mono text-slate-500 whitespace-nowrap">${escapeHtml(e.startdate || "?")} → ${escapeHtml(e.enddate || "?")}</td>
      <td class="py-2 px-3"><span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-${st[1]}-100 text-${st[1]}-800 dark:bg-${st[1]}-950 dark:text-${st[1]}-300">${st[0]}${d.result.isPartialStatement ? " · Partial" : ""}</span></td>
      <td class="py-2 px-3 text-right font-mono">${money(e.endingBalance)}</td></tr>`;
  }).join("");

  body.innerHTML = `
    <div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
      ${tile("Statements", docs.length, `${starts[0] || "?"} → ${ends[ends.length - 1] || "?"}`)}
      ${tile("Avg monthly deposits", money(avgMonthlyDep), `${monthly.length} month${monthly.length === 1 ? "" : "s"} of data`, "text-emerald-600 dark:text-emerald-400")}
      ${tile("Lowest balance", lowest === null ? "N/A" : money(lowest), avgEnding === null ? "" : `Avg ending ${money(avgEnding)}`, lowest !== null && lowest < 0 ? "text-rose-600 dark:text-rose-400" : "")}
      ${tile("Debt burden", `${burdenPct.toFixed(1)}%`, `${money(burden)} of deposits`, burdenCls)}
    </div>
    <div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
      ${tile("Total deposits", money(deposits))}
      ${tile("Total withdrawals", money(withdrawals))}
      ${tile("NSF / returned", nsf, "items across period", nsf ? "text-rose-600 dark:text-rose-400" : "")}
      ${tile("Needs review", warnings, partials ? `${partials} partial statement${partials === 1 ? "" : "s"}` : "Verification warnings", warnings ? "text-amber-600 dark:text-amber-400" : "")}
    </div>
    <div class="grid grid-cols-1 lg:grid-cols-5 gap-4">
      <div class="lg:col-span-3">${chartCard("Monthly Deposits vs Withdrawals", "bar-chart-3", barChartSVG(monthly))}</div>
      <div class="lg:col-span-2">${chartCard("Debt-Related Debits", "pie-chart", donutSVG(["mca", "loan", "cc", "bnpl"].map((k) => ({ label: CAT_STYLE[k].label, color: CAT_STYLE[k].hex, value: totals[k] }))))}</div>
    </div>
    ${lenderRows ? `<section class="border border-slate-200 dark:border-slate-800 rounded-xl overflow-hidden">
      <h4 class="px-4 py-2.5 text-xs font-bold uppercase tracking-wider text-slate-500 bg-slate-50 dark:bg-slate-800/50">Detected Obligations</h4>
      <div class="overflow-x-auto"><table class="w-full text-xs min-w-[480px]"><thead class="text-left text-[10px] uppercase tracking-wider text-slate-400"><tr><th class="py-2 px-3">Counterparty</th><th class="py-2 px-3">Type</th><th class="py-2 px-3 text-right">Payments</th><th class="py-2 px-3 text-right">Avg</th><th class="py-2 px-3 text-right">Total</th></tr></thead><tbody>${lenderRows}</tbody></table></div></section>` : ""}
    <section class="border border-slate-200 dark:border-slate-800 rounded-xl overflow-hidden">
      <h4 class="px-4 py-2.5 text-xs font-bold uppercase tracking-wider text-slate-500 bg-slate-50 dark:bg-slate-800/50">Statements in Scope <span class="normal-case font-normal text-slate-400">(click to open)</span></h4>
      <div class="overflow-x-auto"><table class="w-full text-xs min-w-[480px]"><tbody>${stmtRows}</tbody></table></div></section>`;
  if (window.lucide) lucide.createIcons();
}

// ---------------------------------------------------------------------------
// Command palette (Ctrl/Cmd + K)
// ---------------------------------------------------------------------------
let paletteIndex = 0;
let paletteItems = [];

function paletteCommands() {
  const cmds = [
    { label: "Add API key / change model", hint: "Settings", icon: "settings", run: openSettingsModal },
    { label: "Toggle dark / light theme", hint: "Appearance", icon: "moon", run: toggleTheme },
    { label: "Extract all pending documents", hint: "Action", icon: "sparkles", run: extractAllPending },
    { label: "Portfolio insights", hint: "Analysis", icon: "line-chart", run: openPortfolio },
    { label: "How it works", hint: "Help", icon: "help-circle", run: openHowToModal },
    { label: "Load demo data", hint: "Action", icon: "flask-conical", run: loadSampleDemo },
    { label: "Clear all documents", hint: "Danger", icon: "trash-2", run: clearAllDocs },
  ];
  const docs = documentsStore.map((d) => ({
    label: d.name,
    hint: d.result ? (DOC_TYPE_META[docTypeOf(d)]?.label || "Document") : "Not extracted",
    icon: d.result ? "file-text" : "clock",
    extra: [d.result?.extractedData?.bankName, d.result?.extractedData?.accountHolder, d.result?.extractedData?.entityName].filter(Boolean).join(" "),
    run: () => (d.result ? viewExtractedData(d.id) : document.getElementById(`doc-row-${d.id}`)?.scrollIntoView({ behavior: "smooth", block: "center" })),
  }));
  return [...docs, ...cmds];
}

function openCommandPalette() {
  let root = document.getElementById("commandPalette");
  if (!root) {
    root = document.createElement("div");
    root.id = "commandPalette";
    root.className = "fixed inset-0 z-[60] bg-slate-900/60 backdrop-blur-sm flex items-start justify-center pt-[14vh] px-4";
    root.setAttribute("role", "dialog");
    root.setAttribute("aria-label", "Quick actions");
    root.innerHTML = `
      <div class="w-full max-w-lg rounded-2xl bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 shadow-2xl overflow-hidden">
        <div class="flex items-center gap-2 px-4 border-b border-slate-200 dark:border-slate-800">
          <i data-lucide="search" class="w-4 h-4 text-slate-400"></i>
          <input id="paletteInput" type="text" autocomplete="off" spellcheck="false" placeholder="Search documents or type a command…" class="flex-1 py-3.5 bg-transparent text-sm text-slate-800 dark:text-slate-100 focus:outline-none">
          <kbd class="text-[10px] font-mono text-slate-400 border border-slate-200 dark:border-slate-700 rounded px-1.5 py-0.5">Esc</kbd>
        </div>
        <ul id="paletteList" class="max-h-[50vh] overflow-auto py-1.5" role="listbox"></ul>
      </div>`;
    document.body.appendChild(root);
    root.addEventListener("mousedown", (e) => { if (e.target === root) closeCommandPalette(); });
    const input = root.querySelector("#paletteInput");
    input.addEventListener("input", () => { paletteIndex = 0; renderPalette(); });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { e.stopPropagation(); closeCommandPalette(); }
      else if (e.key === "ArrowDown") { e.preventDefault(); paletteIndex = Math.min(paletteItems.length - 1, paletteIndex + 1); renderPalette(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); paletteIndex = Math.max(0, paletteIndex - 1); renderPalette(); }
      else if (e.key === "Enter") { e.preventDefault(); runPaletteItem(paletteIndex); }
    });
  }
  root.classList.remove("hidden");
  const input = root.querySelector("#paletteInput");
  input.value = "";
  paletteIndex = 0;
  renderPalette();
  input.focus();
}

function closeCommandPalette() { document.getElementById("commandPalette")?.classList.add("hidden"); }

function renderPalette() {
  const q = (document.getElementById("paletteInput")?.value || "").toLowerCase().trim();
  paletteItems = paletteCommands().filter((c) => !q || `${c.label} ${c.extra || ""} ${c.hint}`.toLowerCase().includes(q));
  const list = document.getElementById("paletteList");
  list.innerHTML = paletteItems.length
    ? paletteItems.map((c, i) => `
      <li role="option" data-i="${i}" class="mx-1.5 px-3 py-2 rounded-lg flex items-center gap-3 cursor-pointer text-sm ${i === paletteIndex ? "bg-brand-50 dark:bg-brand-950/60 text-brand-800 dark:text-brand-200" : "text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-800"}">
        <i data-lucide="${c.icon}" class="w-4 h-4 shrink-0 text-slate-400"></i>
        <span class="truncate flex-1">${escapeHtml(c.label)}</span>
        <span class="text-[10px] uppercase tracking-wider text-slate-400 shrink-0">${escapeHtml(c.hint)}</span>
      </li>`).join("")
    : `<li class="px-4 py-6 text-center text-xs text-slate-400">No matches</li>`;
  list.querySelectorAll("li[data-i]").forEach((li) => li.addEventListener("mousedown", (e) => { e.preventDefault(); runPaletteItem(Number(li.dataset.i)); }));
  list.querySelector(`[data-i="${paletteIndex}"]`)?.scrollIntoView({ block: "nearest" });
  if (window.lucide) lucide.createIcons();
}

function runPaletteItem(i) {
  const item = paletteItems[i];
  if (!item) return;
  closeCommandPalette();
  setTimeout(item.run, 0);
}

document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
    e.preventDefault();
    openCommandPalette();
  } else if (e.key === "Escape") {
    closePortfolio();
  }
});

// ---------------------------------------------------------------------------
// Printable one-page summary
// ---------------------------------------------------------------------------
function printSummary() {
  const doc = currentViewingDoc;
  if (!doc?.result) return;
  const res = doc.result;
  const d = res.extractedData || {};
  const area = document.getElementById("printArea");
  const stamp = new Date().toLocaleString();
  let html = `<h1>${escapeHtml(doc.name)}</h1><div>${escapeHtml(DOC_TYPE_META[docTypeOf(doc)]?.label || "Document")} · printed ${escapeHtml(stamp)}</div>`;

  if (isBankDoc(doc)) {
    const flags = buildFlagMap(res);
    const flagged = (d.transactions || []).filter((t) => flags.has(t.trID));
    html += `<h2>Account</h2><table><tr><th>Bank</th><td>${escapeHtml(d.bankName || "N/A")}</td><th>Holder</th><td>${escapeHtml(d.accountHolder || "N/A")}</td></tr>
      <tr><th>Account</th><td>${d.accountNumber ? "••••" + escapeHtml(String(d.accountNumber).slice(-4)) : "N/A"}</td><th>Period</th><td>${escapeHtml(d.startdate || "?")} → ${escapeHtml(d.enddate || "?")}</td></tr></table>
      <h2>Balances</h2><div class="kpis">
      <div class="kpi">Beginning<b>${money(d.beginningBalance)}</b></div><div class="kpi">Deposits<b>${money(d.totalDeposits)}</b></div>
      <div class="kpi">Withdrawals<b>${money(d.totalWithdrawals)}</b></div><div class="kpi">Ending<b>${money(d.endingBalance)}</b></div></div>
      <h2>Verification — ${res.reconciliation?.passed ? "Passed" : "Warnings"}</h2>
      ${res.warningReason ? `<p>${escapeHtml(res.warningReason)}</p>` : ""}
      <table>${(res.reconciliation?.checks || []).map((c) => `<tr><td>${escapeHtml(c.name)}</td><td>${c.passed ? "PASS" : "FAIL"}</td><td class="num">${c.diff === null || c.diff === undefined ? "" : money(c.diff)}</td></tr>`).join("")}</table>
      <h2>Flagged Activity (${flagged.length})</h2>
      ${flagged.length ? `<table><tr><th>Date</th><th>Type</th><th>Counterparty</th><th>Description</th><th class="num">Amount</th></tr>${flagged.map((t) => { const f = flags.get(t.trID); return `<tr><td>${escapeHtml(t.postingDate || "")}</td><td>${f.short}</td><td>${escapeHtml(f.name || "")}</td><td>${escapeHtml(t.description || "")}</td><td class="num">${money(Math.abs(t.amount))}</td></tr>`; }).join("")}</table>` : "<p>No MCA, loan, card, BNPL or returned items detected.</p>"}`;
  } else {
    const rows = [];
    const walk = (obj, prefix) => Object.entries(obj || {}).forEach(([k, v]) => {
      if (v === null || v === "" || v === undefined) return;
      const key = prefix ? `${prefix} › ${k}` : k;
      if (Array.isArray(v)) v.forEach((x, i) => (x && typeof x === "object" ? walk(x, `${key} ${i + 1}`) : rows.push([key, x])));
      else if (typeof v === "object") walk(v, key);
      else rows.push([key, /ssn|dob/i.test(k) ? "••••" + String(v).slice(-4) : v]);
    });
    walk(d, "");
    html += `<h2>Extracted Details</h2><table>${rows.map(([k, v]) => `<tr><th>${escapeHtml(k.replace(/_/g, " "))}</th><td>${escapeHtml(v)}</td></tr>`).join("")}</table>`;
  }
  area.innerHTML = html;
  const prevTitle = document.title;
  document.title = `${doc.name} — Underwriting Summary`;
  const restore = () => { document.title = prevTitle; area.innerHTML = ""; window.removeEventListener("afterprint", restore); };
  window.addEventListener("afterprint", restore);
  window.print();
}
