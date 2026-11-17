"""
Unified Pipeline Orchestrator:
Micro-Batched Execution (Tier 1 Text / Tier 2 Vision) -> Audit -> MCA Analysis
Strictly adheres to the proven micro-batched architecture from the research scripts.
Fully hardened against NoneType math, scanned/image documents, and unhandled list responses.
"""

import base64
import io
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date
import pdfplumber
try:
    import pymupdf as fitz  # Self-contained PDF rasterizer, no Poppler binary required
except ImportError:
    fitz = None

from app.config import IMAGE_DPI, IMAGE_DETAIL, RECONCILIATION_TOLERANCE
from app.gateway import UniversalLLMGateway
from app.prompts import (
    TRIAGE_SYSTEM_PROMPT,
    FREEFORM_SUMMARY_PROMPT,
    TOPOLOGY_SYSTEM_PROMPT,
    SUMMARY_INSTRUCTIONS,
    BATCH_INSTRUCTIONS,
    MULTI_ACCOUNT_SYSTEM_PROMPT,
    UNDERWRITING_SYSTEM_PROMPT,
)
from app.schemas import (
    CLASSIFICATION_SCHEMA,
    FREEFORM_ENTITY_SCHEMA,
    TOPOLOGY_SCHEMA,
    SUMMARY_SCHEMA,
    BATCH_TRANSACTIONS_SCHEMA,
    V4_METADATA_SCHEMA,
    V4_BATCH_SCHEMA,
    MCA_ANALYSIS_SCHEMA,
)

# Proven batch size from root research files
TEXT_BATCH_SIZE = 2
MAX_CONCURRENT_WORKERS = 4


def _pil_to_base64_data_uri(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode('utf-8')}"


def _get_page_image_data_uri(file_path: str, page_num_1based: int) -> str:
    """Return a specific 1-based page as a PNG data URI using PyMuPDF."""
    if fitz is None:
        raise RuntimeError(
            "PDF image rendering requires PyMuPDF. Run `pip install -r requirements.txt` "
            "in the project virtual environment, then restart the server."
        )
    try:
        document = fitz.open(file_path)
        if document.page_count < page_num_1based or page_num_1based < 1:
            raise ValueError(f"Page {page_num_1based} does not exist in {file_path}")
        page = document.load_page(page_num_1based - 1)
        dpi = IMAGE_DPI or 150
        pixmap = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
        return f"data:image/png;base64,{base64.b64encode(pixmap.tobytes('png')).decode('utf-8')}"
    except Exception as exc:
        raise RuntimeError(f"Could not render PDF page {page_num_1based}: {exc}") from exc


def _render_first_pdf_page(file_path: str) -> str:
    """Return page one as a PNG data URI."""
    return _get_page_image_data_uri(file_path, 1)


def _parse_num(val):
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        cleaned = val.replace("$", "").replace(",", "").strip()
        # Handle trailing minus signs (e.g., '1,500.00-')
        if cleaned.endswith("-"):
            cleaned = "-" + cleaned[:-1].strip()
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _money(val):
    return "N/A" if val is None else f"${float(val):,.2f}"


def _parse_date(d_str: str) -> date | None:
    if not d_str or not isinstance(d_str, str):
        return None
    d_str = d_str.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(d_str, fmt).date()
        except ValueError:
            pass
    return None


def _check_partial_statement_coverage(startdate_str: str, enddate_str: str, transactions: list) -> dict:
    """
    Checks if transactions cover <= 70% of the statement duration.
    E.g., duration 29 June to 29 July (30 days), transactions only up to 20 July (21 days / 30 = 70.0%) -> raises warning.
    """
    start_d = _parse_date(startdate_str)
    end_d = _parse_date(enddate_str)

    if not start_d or not end_d or end_d <= start_d:
        return {"is_partial": False, "ratio": 1.0, "pct": 100.0, "reason": None}

    total_period_days = (end_d - start_d).days
    if total_period_days < 7:
        return {"is_partial": False, "ratio": 1.0, "pct": 100.0, "reason": None}

    txn_dates = []
    for tx in transactions:
        if isinstance(tx, dict) and tx.get("postingDate"):
            d = _parse_date(tx["postingDate"])
            if d:
                txn_dates.append(d)

    if not txn_dates:
        return {
            "is_partial": True,
            "ratio": 0.0,
            "pct": 0.0,
            "latest_date": None,
            "reason": f"Partial Statement Warning: Full statement is missing. No transactions posted within period {startdate_str} to {enddate_str}."
        }

    latest_txn_d = max(txn_dates)
    covered_days = max(1, (latest_txn_d - start_d).days)
    ratio = min(1.0, covered_days / total_period_days)
    pct = round(ratio * 100, 1)

    # 70% threshold rule requested by user
    if ratio <= 0.70:
        return {
            "is_partial": True,
            "ratio": ratio,
            "pct": pct,
            "latest_date": str(latest_txn_d),
            "reason": (
                f"Partial Statement Warning: Full statement is missing or this is only a portion of a statement. "
                f"Transactions only extend up to {latest_txn_d} ({pct}% of statement period {startdate_str} to {enddate_str} <= 70%)."
            )
        }

    return {"is_partial": False, "ratio": ratio, "pct": pct, "reason": None}


class PipelineOrchestrator:
    def __init__(self, gateway: UniversalLLMGateway, progress_callback=None):
        self.gateway = gateway
        self.emit = progress_callback or (lambda msg, pct: None)
        self.usage_acc = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost": 0.0}

    def _track_usage(self, usage: dict):
        for k in self.usage_acc:
            self.usage_acc[k] += usage.get(k, 0)

    def run(self, file_path: str) -> dict:
        t0 = time.time()
        ext = os.path.splitext(file_path)[1].lower()
        is_pdf = ext == ".pdf"

        # --------------------------------------------------------------------
        # STAGE 1: ZERO-SHOT TRIAGE & CLASSIFICATION
        # --------------------------------------------------------------------
        self.emit("Triaging document type...", 10)
        triage_content = []
        is_scanned = False
        pages_text = []
        total_pages = 1

        if is_pdf:
            try:
                with pdfplumber.open(file_path) as pdf:
                    total_pages = len(pdf.pages)
                    for idx, p in enumerate(pdf.pages, start=1):
                        p_text = (p.extract_text(layout=True) or p.extract_text() or "").strip()
                        pages_text.append((idx, p_text))
            except Exception:
                pages_text = [(1, "")]

            total_extracted_text = "".join(p[1] for p in pages_text).strip()
            # If total extracted text across the document is sparse, document is a scan/image
            is_scanned = len(total_extracted_text) < 150 or (len(total_extracted_text) / max(total_pages, 1)) < 50

            if not is_scanned and len(pages_text[0][1]) >= 100:
                triage_content.append({"type": "input_text", "text": f"Page 1 Text:\n\n{pages_text[0][1]}"})
            else:
                triage_content.append({"type": "input_image", "image_url": _get_page_image_data_uri(file_path, 1)})
        else:
            is_scanned = True
            with open(file_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
            mime = "image/png" if ext == ".png" else "image/jpeg"
            triage_content.append({"type": "input_image", "image_url": f"data:{mime};base64,{b64}"})

        triage_res, u1 = self.gateway.call_structured(TRIAGE_SYSTEM_PROMPT, triage_content, CLASSIFICATION_SCHEMA, "triage_classification")
        self._track_usage(u1)

        # Defensive normalization
        if isinstance(triage_res, list) and len(triage_res) > 0 and isinstance(triage_res[0], dict):
            triage_res = triage_res[0]
        elif not isinstance(triage_res, dict):
            triage_res = {"category": "bank_statement"}

        # Extract category from enum or boolean flags
        category = triage_res.get("category")
        if not category or category == "other_document":
            if triage_res.get("is_bank_statement") or triage_res.get("is_mtb"):
                category = "bank_statement"
            elif triage_res.get("is_application_form"):
                category = "application_form"
            elif triage_res.get("is_driving_licence"):
                category = "driving_licence"
            elif triage_res.get("is_voided_check"):
                category = "voided_check"
            elif triage_res.get("is_other_document"):
                category = "other_document"

        # Contextual check: if bank keywords are prominent in the filename or text, override other_document
        fn_lower = os.path.basename(file_path).lower()
        p1_lower = (pages_text[0][1] if pages_text else "").lower()
        if category == "other_document":
            bank_indicators = ("community bank", "checking account", "bank statement", "detail transactions", "deposit accounts", "beginning balance")
            if any(term in fn_lower or term in p1_lower for term in bank_indicators):
                category = "bank_statement"

        if not category:
            category = "bank_statement"

        # Emit classification metadata immediately so document card reflects it
        self.emit(f"Document classified as {category.replace('_', ' ').title()}", 20, {"category": category, "documentType": category})

        # ROUTE A: APPLICATION FORM
        if category == "application_form":
            self.emit("Funding Application identified. Form data extracted.", 100, {"category": category, "documentType": category})
            return {
                "documentType": "application_form",
                "status": "success",
                "extractedData": triage_res,
                "usage": self.usage_acc,
                "elapsed": round(time.time() - t0, 2),
            }

        # ROUTE B: IDENTITY, VOIDED CHECK, OR OFFICIAL ENTITY DOCUMENT
        if category in ("driving_licence", "voided_check") or (category == "other_document" and not is_pdf):
            self.emit("Extracting structured summary for entity document...", 50, {"category": category, "documentType": category})
            entity_res, u_ent = self.gateway.call_structured(FREEFORM_SUMMARY_PROMPT, triage_content, FREEFORM_ENTITY_SCHEMA, "entity_summary")
            self._track_usage(u_ent)
            if isinstance(entity_res, list) and len(entity_res) > 0 and isinstance(entity_res[0], dict):
                entity_res = entity_res[0]
            elif not isinstance(entity_res, dict):
                entity_res = {"documentType": category, "summary": "Extracted entity"}
            return {
                "documentType": category,
                "status": "success",
                "extractedData": entity_res,
                "usage": self.usage_acc,
                "elapsed": round(time.time() - t0, 2),
            }

        # ROUTE C: OTHER DOCUMENT (NO ACTIONABLE INFO)
        if category == "other_document":
            self.emit("No actionable financial information detected.", 100, {"category": category, "documentType": category})
            return {
                "documentType": "other_document",
                "status": "other_no_info",
                "message": "Other Type (No Useful information Here to Extract)",
                "extractedData": None,
                "usage": self.usage_acc,
                "elapsed": round(time.time() - t0, 2),
            }

        # --------------------------------------------------------------------
        # STAGE 2: TOPOLOGY DETECTION (SINGLE vs. MULTI-ACCOUNT)
        # --------------------------------------------------------------------
        self.emit("Bank Statement confirmed. Detecting statement topology...", 25)

        if not is_scanned:
            header_text = "\n\n".join(f"=== PAGE {p[0]} ===\n{p[1]}" for p in pages_text[:min(2, total_pages)])
            topo_content = [{"type": "input_text", "text": f"Statement header pages:\n\n{header_text}"}]
        else:
            topo_content = [{"type": "input_text", "text": "Analyze the statement header page image(s) for document topology:"}]
            for p in pages_text[:min(2, total_pages)]:
                if is_pdf:
                    topo_content.append({"type": "input_image", "image_url": _get_page_image_data_uri(file_path, p[0])})
                else:
                    topo_content.extend(triage_content)

        topo_res, u_topo = self.gateway.call_structured(TOPOLOGY_SYSTEM_PROMPT, topo_content, TOPOLOGY_SCHEMA, "topology_schema")
        self._track_usage(u_topo)

        # Defensive normalization
        if isinstance(topo_res, list) and len(topo_res) > 0 and isinstance(topo_res[0], dict):
            topo_res = topo_res[0]
        elif not isinstance(topo_res, dict):
            topo_res = {"isConsolidated": False, "evidence": "Single account default"}

        is_consolidated = bool(topo_res.get("isConsolidated", False))

        # Emit topology metadata immediately so document card updates
        self.emit(
            f"Topology: {'Consolidated Multi-Account' if is_consolidated else 'Single-Account'}",
            35,
            {"isConsolidated": is_consolidated, "topology": "multi" if is_consolidated else "single", "category": category, "documentType": category}
        )

        # --------------------------------------------------------------------
        # STAGE 3: MICRO-BATCHED STATEMENT EXTRACTION
        # --------------------------------------------------------------------
        if is_consolidated:
            self.emit("Consolidated Multi-Account Statement detected. Running V4 Engine...", 35)
            stmt_data = self._extract_multi_account_batched(file_path, pages_text, total_pages, is_scanned)
        else:
            self.emit("Single-Account Statement confirmed. Running V3 Engine...", 35)
            stmt_data = self._extract_single_account_batched(file_path, pages_text, total_pages, is_scanned)

        # --------------------------------------------------------------------
        # STAGE 4: MATHEMATICAL RECONCILIATION AUDIT (NULL-SAFE)
        # --------------------------------------------------------------------
        self.emit("Performing mathematical reconciliation audit...", 75)
        audit_res = self._audit_reconciliation(stmt_data)

        # --------------------------------------------------------------------
        # STAGE 5: FINANCIAL POSITION & MCA UNDERWRITING PASS
        # --------------------------------------------------------------------
        self.emit("Running Financial Position & MCA Underwriting detection...", 90)
        underwriting_data = self._run_underwriting(stmt_data)

        self.emit("Extraction and audit completed successfully!", 100)
        return {
            "documentType": category,
            "isConsolidated": is_consolidated,
            "isPartialStatement": audit_res.get("is_partial_statement", False),
            "coveragePct": audit_res.get("coverage_pct", 100.0),
            "topologyEvidence": topo_res.get("evidence"),
            "status": "success" if audit_res["passed"] else "warning",
            "warningReason": audit_res["warningReason"],
            "extractedData": stmt_data,
            "reconciliation": audit_res,
            "underwriting": underwriting_data,
            "usage": self.usage_acc,
            "elapsed": round(time.time() - t0, 2),
        }

    # ------------------------------------------------------------------------
    # MICRO-BATCHED SINGLE ACCOUNT ENGINE (Tier 1 Text / Tier 2 Vision)
    # ------------------------------------------------------------------------
    def _extract_single_account_batched(self, file_path: str, pages_text: list, total_pages: int, is_scanned: bool) -> dict:
        is_pdf = file_path.lower().endswith(".pdf")

        # Step A: Extract statement summary metadata from header pages 1-2 (~150 tokens)
        self.emit("Extracting statement summary and balance metadata...", 40)
        if not is_scanned:
            header_text = "\n\n".join(f"=== PAGE {p[0]} ===\n{p[1]}" for p in pages_text[:min(2, total_pages)])
            header_content = [{"type": "input_text", "text": f"Statement header:\n\n{header_text}"}]
        else:
            header_content = [{"type": "input_text", "text": "Extract high-level statement summary and balance metadata from the header page(s):"}]
            if is_pdf:
                for p in pages_text[:min(2, total_pages)]:
                    header_content.append({"type": "input_image", "image_url": _get_page_image_data_uri(file_path, p[0])})
            else:
                with open(file_path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("utf-8")
                mime = "image/png" if file_path.lower().endswith(".png") else "image/jpeg"
                header_content.append({"type": "input_image", "image_url": f"data:{mime};base64,{b64}"})

        summary_data, u_sum = self.gateway.call_structured(SUMMARY_INSTRUCTIONS, header_content, SUMMARY_SCHEMA, "statement_summary")
        self._track_usage(u_sum)

        if isinstance(summary_data, list) and len(summary_data) > 0 and isinstance(summary_data[0], dict):
            summary_data = summary_data[0]
        elif not isinstance(summary_data, dict):
            summary_data = {}

        # Step B: Micro-batch transaction pages (1-2 pages per batch)
        batches = [pages_text[i:i + TEXT_BATCH_SIZE] for i in range(0, total_pages, TEXT_BATCH_SIZE)]
        all_txs = []

        for b_idx, batch_slice in enumerate(batches, start=1):
            pct = 40 + int((b_idx / len(batches)) * 30)
            self.emit(f"Extracting transactions (Pages {batch_slice[0][0]}-{batch_slice[-1][0]} of {total_pages})...", pct)

            if not is_scanned:
                b_text = "\n\n".join(f"=== PAGE {p[0]} ===\n{p[1]}" for p in batch_slice)
                prompt = (
                    f"Period: {summary_data.get('startdate')} to {summary_data.get('enddate')}\n"
                    f"Text from Pages {batch_slice[0][0]}-{batch_slice[-1][0]} of {total_pages}:\n\n{b_text}"
                )
                b_content = [{"type": "input_text", "text": prompt}]
            else:
                prompt = (
                    f"Period: {summary_data.get('startdate')} to {summary_data.get('enddate')}\n"
                    f"Extract all individual transaction line items from Pages {batch_slice[0][0]}-{batch_slice[-1][0]} of {total_pages}:"
                )
                b_content = [{"type": "input_text", "text": prompt}]
                if is_pdf:
                    for p in batch_slice:
                        b_content.append({"type": "input_image", "image_url": _get_page_image_data_uri(file_path, p[0])})
                else:
                    with open(file_path, "rb") as f:
                        b64 = base64.b64encode(f.read()).decode("utf-8")
                    mime = "image/png" if file_path.lower().endswith(".png") else "image/jpeg"
                    b_content.append({"type": "input_image", "image_url": f"data:{mime};base64,{b64}"})

            b_res, u_b = self.gateway.call_structured(BATCH_INSTRUCTIONS, b_content, BATCH_TRANSACTIONS_SCHEMA, "batch_transactions")
            self._track_usage(u_b)

            if isinstance(b_res, list):
                b_txs = b_res
            elif isinstance(b_res, dict):
                b_txs = b_res.get("transactions", []) or []
            else:
                b_txs = []

            all_txs.extend([tx for tx in b_txs if isinstance(tx, dict)])

        doc = {
            **summary_data,
            "extractionScope": f"All {total_pages} pages extracted ({'Vision' if is_scanned else 'Text'})",
            "transactions": all_txs,
        }
        return self._tag_transaction_ids(doc)

    # ------------------------------------------------------------------------
    # MICRO-BATCHED MULTI-ACCOUNT ENGINE (Parallel Batches from V4)
    # ------------------------------------------------------------------------
    def _extract_multi_account_batched(self, file_path: str, pages_text: list, total_pages: int, is_scanned: bool) -> dict:
        is_pdf = file_path.lower().endswith(".pdf")
        self.emit("Extracting multi-account document metadata...", 40)

        if not is_scanned:
            header_text = "\n\n".join(f"=== PAGE {p[0]} ===\n{p[1]}" for p in pages_text[:min(2, total_pages)])
            meta_content = [{"type": "input_text", "text": f"TASK: DOCUMENT_METADATA\n\nStatement header pages:\n\n{header_text}"}]
        else:
            meta_content = [{"type": "input_text", "text": "TASK: DOCUMENT_METADATA\n\nStatement header pages:"}]
            if is_pdf:
                for p in pages_text[:min(2, total_pages)]:
                    meta_content.append({"type": "input_image", "image_url": _get_page_image_data_uri(file_path, p[0])})
            else:
                with open(file_path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("utf-8")
                mime = "image/png" if file_path.lower().endswith(".png") else "image/jpeg"
                meta_content.append({"type": "input_image", "image_url": f"data:{mime};base64,{b64}"})

        metadata, u_meta = self.gateway.call_structured(MULTI_ACCOUNT_SYSTEM_PROMPT, meta_content, V4_METADATA_SCHEMA, "v4_metadata")
        self._track_usage(u_meta)

        if isinstance(metadata, list) and len(metadata) > 0 and isinstance(metadata[0], dict):
            metadata = metadata[0]
        elif not isinstance(metadata, dict):
            metadata = {}

        batches = [pages_text[i:i + TEXT_BATCH_SIZE] for i in range(0, total_pages, TEXT_BATCH_SIZE)]
        all_summaries = []
        all_txs = []

        def _process_batch(b_slice):
            if not is_scanned:
                b_text = "\n\n".join(f"=== PAGE {p[0]} ===\n{p[1]}" for p in b_slice)
                prompt = (
                    f"TASK: BATCH_EXTRACTION\n"
                    f"Period: {metadata.get('startdate')} to {metadata.get('enddate')}\n"
                    f"Primary Account: {metadata.get('primaryAccountNumber')}\n"
                    f"Pages {b_slice[0][0]}-{b_slice[-1][0]} of {total_pages}:\n\n{b_text}"
                )
                b_content = [{"type": "input_text", "text": prompt}]
            else:
                prompt = (
                    f"TASK: BATCH_EXTRACTION\n"
                    f"Period: {metadata.get('startdate')} to {metadata.get('enddate')}\n"
                    f"Primary Account: {metadata.get('primaryAccountNumber')}\n"
                    f"Pages {b_slice[0][0]}-{b_slice[-1][0]} of {total_pages}:"
                )
                b_content = [{"type": "input_text", "text": prompt}]
                if is_pdf:
                    for p in b_slice:
                        b_content.append({"type": "input_image", "image_url": _get_page_image_data_uri(file_path, p[0])})
                else:
                    with open(file_path, "rb") as f:
                        b64 = base64.b64encode(f.read()).decode("utf-8")
                    mime = "image/png" if file_path.lower().endswith(".png") else "image/jpeg"
                    b_content.append({"type": "input_image", "image_url": f"data:{mime};base64,{b64}"})

            res, u = self.gateway.call_structured(MULTI_ACCOUNT_SYSTEM_PROMPT, b_content, V4_BATCH_SCHEMA, "v4_batch")
            return res, u

        with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_WORKERS) as executor:
            futures = [executor.submit(_process_batch, b) for b in batches]
            for f in as_completed(futures):
                try:
                    res, u = f.result()
                    self._track_usage(u)
                    if isinstance(res, dict):
                        all_summaries.extend(res.get("accountSummaries", []) or [])
                        all_txs.extend(res.get("transactions", []) or [])
                except Exception as batch_err:
                    pass

        accounts_map = {}
        for s in all_summaries:
            if not isinstance(s, dict):
                continue
            acc_num = s.get("accountNumber")
            if acc_num and acc_num not in accounts_map:
                s["transactions"] = []
                accounts_map[acc_num] = s

        default_acc = next(iter(accounts_map.keys())) if accounts_map else metadata.get("primaryAccountNumber")
        for tx in all_txs:
            if not isinstance(tx, dict):
                continue
            a_key = tx.get("accountNumber") or default_acc
            if a_key in accounts_map:
                accounts_map[a_key]["transactions"].append(tx)

        doc = {
            **metadata,
            "accountNumber": metadata.get("primaryAccountNumber"),
            "accounts": list(accounts_map.values()),
            "transactions": all_txs,
            "beginningBalance": round(sum((_parse_num(a.get("beginningBalance")) or 0.0) for a in accounts_map.values()), 2),
            "endingBalance": round(sum((_parse_num(a.get("endingBalance")) or 0.0) for a in accounts_map.values()), 2),
            "totalDeposits": round(sum((_parse_num(a.get("totalDeposits")) or 0.0) for a in accounts_map.values()), 2),
            "totalWithdrawals": round(sum((_parse_num(a.get("totalWithdrawals")) or 0.0) for a in accounts_map.values()), 2),
        }
        return self._tag_transaction_ids(doc)

    def _tag_transaction_ids(self, data: dict) -> dict:
        txs = data.get("transactions", []) or []
        clean_txs = []
        for idx, tx in enumerate(txs, start=1):
            if not isinstance(tx, dict):
                continue
            amt = _parse_num(tx.get("amount")) or 0.0
            desc = str(tx.get("description") or "").strip()
            raw_type = str(tx.get("type") or "").strip().lower()

            tx["trID"] = f"TR{idx:05d}"
            tx["amount"] = amt
            if raw_type == "returned_item" or "[RETURNED UNPAID]" in desc.upper() or "(REJECTED)" in desc.upper():
                tx["type"] = "returned_item"
            else:
                tx["type"] = raw_type if raw_type in ("credit", "debit") else ("credit" if amt > 0 else "debit")

            tx["balance"] = _parse_num(tx.get("balance"))
            clean_txs.append(tx)

        data["transactions"] = clean_txs
        return data

    # ------------------------------------------------------------------------
    # MATHEMATICAL RECONCILIATION AUDIT (NULL-SAFE)
    # ------------------------------------------------------------------------
    def _audit_reconciliation(self, data: dict) -> dict:
        beg = _parse_num(data.get("beginningBalance"))
        end = _parse_num(data.get("endingBalance"))
        sum_dep = _parse_num(data.get("totalDeposits"))
        sum_wd = _parse_num(data.get("totalWithdrawals"))

        txs = data.get("transactions", []) or []
        credit_sum = 0.0
        debit_sum = 0.0

        for tx in txs:
            if not isinstance(tx, dict):
                continue
            amt = _parse_num(tx.get("amount")) or 0.0
            desc = str(tx.get("description") or "").upper()
            tx_type = tx.get("type")

            if "[RETURNED UNPAID]" in desc or tx_type == "returned_item":
                continue

            if tx_type == "credit" or amt > 0:
                credit_sum += abs(amt)
            elif tx_type == "debit" or amt < 0:
                debit_sum += abs(amt)

        credit_sum = round(credit_sum, 2)
        debit_sum = round(debit_sum, 2)

        dep_diff = round(credit_sum - sum_dep, 2) if sum_dep is not None else None
        wd_diff = round(debit_sum - sum_wd, 2) if sum_wd is not None else None

        has_all_summary = all(v is not None for v in (beg, end, sum_dep, sum_wd))
        math_diff = round(beg + sum_dep - sum_wd - end, 2) if has_all_summary else None
        math_check = (abs(math_diff) <= RECONCILIATION_TOLERANCE) if math_diff is not None else False

        # Netted offset check (Credit voucher adjustment resolver)
        is_netted_offset = (
            dep_diff is not None and wd_diff is not None
            and abs(round(dep_diff - wd_diff, 2)) <= RECONCILIATION_TOLERANCE
            and abs(dep_diff) > RECONCILIATION_TOLERANCE
        )

        dep_pass = (abs(dep_diff) <= RECONCILIATION_TOLERANCE) if dep_diff is not None else True
        wd_pass = (abs(wd_diff) <= RECONCILIATION_TOLERANCE) if wd_diff is not None else True

        # Check partial statement coverage (<= 70% threshold rule)
        coverage = _check_partial_statement_coverage(data.get("startdate"), data.get("enddate"), txs)

        # Document must have transactions or explicit summary figures to pass
        has_content = len(txs) > 0 or has_all_summary
        passed = has_content and (dep_pass or is_netted_offset) and (wd_pass or is_netted_offset) and (math_check or not has_all_summary) and not coverage.get("is_partial", False)

        warning_reasons = []
        if not has_content:
            warning_reasons.append("No transactions or summary totals could be extracted from this document.")
        if coverage.get("is_partial"):
            warning_reasons.append(coverage["reason"])
        if has_all_summary and not math_check:
            warning_reasons.append(f"Summary formula mismatch (Beg {_money(beg)} + Dep {_money(sum_dep)} - WD {_money(sum_wd)} != End {_money(end)})")
        if not dep_pass and not is_netted_offset:
            warning_reasons.append(f"Deposits mismatch: Extracted {_money(credit_sum)} vs Summary {_money(sum_dep)} (diff: {_money(dep_diff)})")
        if not wd_pass and not is_netted_offset:
            warning_reasons.append(f"Withdrawals mismatch: Extracted {_money(debit_sum)} vs Summary {_money(sum_wd)} (diff: {_money(wd_diff)})")

        if is_netted_offset:
            warning_reasons.append(f"Auto-resolved netted credit voucher adjustment offset: +{_money(dep_diff)}")

        return {
            "passed": passed,
            "credit_sum": credit_sum,
            "debit_sum": debit_sum,
            "deposit_diff": dep_diff,
            "withdrawal_diff": wd_diff,
            "is_netted_offset": is_netted_offset,
            "is_partial_statement": coverage.get("is_partial", False),
            "coverage_pct": coverage.get("pct", 100.0),
            "coverage_reason": coverage.get("reason"),
            "warningReason": " | ".join(warning_reasons) if warning_reasons else None,
            "checks": [
                {
                    "name": "Statement Duration Coverage Check",
                    "passed": not coverage.get("is_partial", False),
                    "diff": None,
                    "detail": coverage["reason"] if coverage.get("is_partial") else f"Coverage: {coverage.get('pct', 100.0)}% of statement period ({data.get('startdate')} to {data.get('enddate')})"
                },
                {
                    "name": "Summary Balance Math Check",
                    "passed": math_check if has_all_summary else has_content,
                    "diff": math_diff,
                    "detail": f"Calculated: Beg {_money(beg)} + Dep {_money(sum_dep)} - WD {_money(sum_wd)} vs End {_money(end)}" if has_all_summary else ("Summary balances not printed on single page" if has_content else "No summary data found")
                },
                {
                    "name": "Deposits Reconciliation",
                    "passed": dep_pass or is_netted_offset,
                    "diff": dep_diff,
                    "detail": f"Extracted {_money(credit_sum)} vs summary {_money(sum_dep)}" + (f" (Netted offset: +{_money(dep_diff)})" if is_netted_offset else "")
                },
                {
                    "name": "Withdrawals Reconciliation",
                    "passed": wd_pass or is_netted_offset,
                    "diff": wd_diff,
                    "detail": f"Extracted {_money(debit_sum)} vs summary {_money(sum_wd)}" + (f" (Netted offset: +{_money(wd_diff)})" if is_netted_offset else "")
                },
            ]
        }

    # ------------------------------------------------------------------------
    # FINANCIAL POSITION & MCA UNDERWRITING PASS
    # ------------------------------------------------------------------------
    def _run_underwriting(self, data: dict) -> dict:
        txns = data.get("transactions", []) or []
        if not txns:
            return {"analyzed_transactions": [], "mca_count": 0, "loan_count": 0, "returned_count": 0}

        lines = ["trID,postingDate,type,amount,description"]
        for tx in txns:
            if isinstance(tx, dict):
                lines.append(f"{tx.get('trID')},{tx.get('postingDate')},{tx.get('type')},{tx.get('amount')},{str(tx.get('description') or '').replace(',', ' ')}")
        csv_table = "\n".join(lines)

        user_msg = (
            f"STATEMENT SUMMARY:\n"
            f"Bank: {data.get('bankName')} | Account: {data.get('accountNumber')}\n"
            f"Period: {data.get('startdate')} to {data.get('enddate')}\n\n"
            f"TRANSACTIONS TABLE:\n{csv_table}"
        )

        res, u = self.gateway.call_structured(UNDERWRITING_SYSTEM_PROMPT, [{"type": "input_text", "text": user_msg}], MCA_ANALYSIS_SCHEMA, "mca_underwriting")
        self._track_usage(u)

        if isinstance(res, list) and len(res) > 0 and isinstance(res[0], dict):
            res = res[0]
        elif not isinstance(res, dict):
            res = {}

        analyzed = res.get("analyzed_transactions", []) or []
        mca_items = [t for t in analyzed if isinstance(t, dict) and t.get("isMca")]
        cc_items = [t for t in analyzed if isinstance(t, dict) and t.get("isCreditCard")]
        loan_items = [t for t in analyzed if isinstance(t, dict) and t.get("isBusinessLoan")]
        bnpl_items = [t for t in analyzed if isinstance(t, dict) and t.get("isBnpl")]
        returned_items = [t for t in analyzed if isinstance(t, dict) and t.get("isReturnedItem")]

        return {
            "analyzed_transactions": analyzed,
            "mca_count": len(mca_items),
            "cc_count": len(cc_items),
            "loan_count": len(loan_items),
            "bnpl_count": len(bnpl_items),
            "returned_count": len(returned_items),
        }
