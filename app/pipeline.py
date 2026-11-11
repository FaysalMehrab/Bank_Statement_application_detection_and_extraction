"""
Unified Pipeline Orchestrator:
Micro-Batched Execution (Tier 1 Text / Tier 2 Vision) -> Audit -> MCA Analysis
Strictly adheres to the proven micro-batched architecture from the research scripts.
Fully hardened against NoneType math and unhandled list responses.
"""

import base64
import io
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import pdfplumber
from pdf2image import convert_from_path

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


def _parse_num(val):
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        cleaned = val.replace("$", "").replace(",", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _money(val):
    return "N/A" if val is None else f"${float(val):,.2f}"


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

        if is_pdf:
            text_p1 = ""
            try:
                with pdfplumber.open(file_path) as pdf:
                    if len(pdf.pages) > 0:
                        text_p1 = (pdf.pages[0].extract_text(layout=True) or "").strip()
                        if not text_p1:
                            text_p1 = (pdf.pages[0].extract_text() or "").strip()
            except Exception:
                text_p1 = ""

            if len(text_p1) >= 40:
                triage_content.append({"type": "input_text", "text": f"Page 1 Text:\n\n{text_p1}"})
            else:
                imgs = convert_from_path(file_path, dpi=150, first_page=1, last_page=1)
                triage_content.append({"type": "input_image", "image_url": _pil_to_base64_data_uri(imgs[0])})
        else:
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
            triage_res = {"category": "other_document"}

        category = triage_res.get("category", "other_document")

        # ROUTE A: APPLICATION FORM
        if category == "application_form":
            self.emit("Funding Application identified. Form data extracted.", 100)
            return {
                "documentType": "application_form",
                "status": "success",
                "extractedData": triage_res,
                "usage": self.usage_acc,
                "elapsed": round(time.time() - t0, 2),
            }

        # ROUTE B: IDENTITY, VOIDED CHECK, OR OFFICIAL ENTITY DOCUMENT
        if category in ("driving_licence", "voided_check") or (category == "other_document" and not is_pdf):
            self.emit("Extracting structured summary for entity document...", 50)
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
            self.emit("No actionable financial information detected.", 100)
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
        pages_text = []
        with pdfplumber.open(file_path) as pdf:
            total_pages = len(pdf.pages)
            for idx, p in enumerate(pdf.pages, start=1):
                p_text = p.extract_text(layout=True) or p.extract_text() or ""
                pages_text.append((idx, p_text))

        header_text = "\n\n".join(f"=== PAGE {p[0]} ===\n{p[1]}" for p in pages_text[:min(2, total_pages)])
        topo_content = [{"type": "input_text", "text": f"Statement header pages:\n\n{header_text}"}]
        topo_res, u_topo = self.gateway.call_structured(TOPOLOGY_SYSTEM_PROMPT, topo_content, TOPOLOGY_SCHEMA, "topology_schema")
        self._track_usage(u_topo)

        # Defensive normalization: ensure topo_res is dict
        if isinstance(topo_res, list) and len(topo_res) > 0 and isinstance(topo_res[0], dict):
            topo_res = topo_res[0]
        elif not isinstance(topo_res, dict):
            topo_res = {"isConsolidated": False, "evidence": "Single account default"}

        is_consolidated = bool(topo_res.get("isConsolidated", False))

        # --------------------------------------------------------------------
        # STAGE 3: MICRO-BATCHED STATEMENT EXTRACTION
        # --------------------------------------------------------------------
        if is_consolidated:
            self.emit("Consolidated Multi-Account Statement detected. Running V4 Engine...", 35)
            stmt_data = self._extract_multi_account_batched(file_path, pages_text, total_pages)
        else:
            self.emit("Single-Account Statement confirmed. Running V3 Engine...", 35)
            stmt_data = self._extract_single_account_batched(file_path, pages_text, total_pages)

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
            "documentType": "bank_statement",
            "isConsolidated": is_consolidated,
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
    def _extract_single_account_batched(self, file_path: str, pages_text: list, total_pages: int) -> dict:
        # Step A: Extract statement summary metadata from header pages 1-2 (~150 tokens)
        self.emit("Extracting statement summary and balance metadata...", 40)
        header_text = "\n\n".join(f"=== PAGE {p[0]} ===\n{p[1]}" for p in pages_text[:min(2, total_pages)])
        header_content = [{"type": "input_text", "text": f"Statement header:\n\n{header_text}"}]
        summary_data, u_sum = self.gateway.call_structured(SUMMARY_INSTRUCTIONS, header_content, SUMMARY_SCHEMA, "statement_summary")
        self._track_usage(u_sum)

        if isinstance(summary_data, list) and len(summary_data) > 0 and isinstance(summary_data[0], dict):
            summary_data = summary_data[0]
        elif not isinstance(summary_data, dict):
            summary_data = {}

        # Step B: Micro-batch transaction pages (1-2 pages per batch, ~400-800 tokens each)
        batches = [pages_text[i:i + TEXT_BATCH_SIZE] for i in range(0, total_pages, TEXT_BATCH_SIZE)]
        all_txs = []

        for b_idx, batch_slice in enumerate(batches, start=1):
            pct = 40 + int((b_idx / len(batches)) * 30)
            self.emit(f"Extracting transactions (Pages {batch_slice[0][0]}-{batch_slice[-1][0]} of {total_pages})...", pct)

            b_text = "\n\n".join(f"=== PAGE {p[0]} ===\n{p[1]}" for p in batch_slice)
            prompt = (
                f"Period: {summary_data.get('startdate')} to {summary_data.get('enddate')}\n"
                f"Text from Pages {batch_slice[0][0]}-{batch_slice[-1][0]} of {total_pages}:\n\n{b_text}"
            )
            b_res, u_b = self.gateway.call_structured(BATCH_INSTRUCTIONS, [{"type": "input_text", "text": prompt}], BATCH_TRANSACTIONS_SCHEMA, "batch_transactions")
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
            "extractionScope": f"All {total_pages} pages extracted",
            "transactions": all_txs,
        }
        return self._tag_transaction_ids(doc)

    # ------------------------------------------------------------------------
    # MICRO-BATCHED MULTI-ACCOUNT ENGINE (Parallel Batches from V4)
    # ------------------------------------------------------------------------
    def _extract_multi_account_batched(self, file_path: str, pages_text: list, total_pages: int) -> dict:
        self.emit("Extracting multi-account document metadata...", 40)
        header_text = "\n\n".join(f"=== PAGE {p[0]} ===\n{p[1]}" for p in pages_text[:min(2, total_pages)])
        meta_content = [{"type": "input_text", "text": f"TASK: DOCUMENT_METADATA\n\nStatement header pages:\n\n{header_text}"}]
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
            b_text = "\n\n".join(f"=== PAGE {p[0]} ===\n{p[1]}" for p in b_slice)
            prompt = (
                f"TASK: BATCH_EXTRACTION\n"
                f"Period: {metadata.get('startdate')} to {metadata.get('enddate')}\n"
                f"Primary Account: {metadata.get('primaryAccountNumber')}\n"
                f"Pages {b_slice[0][0]}-{b_slice[-1][0]} of {total_pages}:\n\n{b_text}"
            )
            res, u = self.gateway.call_structured(MULTI_ACCOUNT_SYSTEM_PROMPT, [{"type": "input_text", "text": prompt}], V4_BATCH_SCHEMA, "v4_batch")
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
                    logger.warning(f"Batch thread extraction warning: {batch_err}")

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

        passed = (dep_pass or is_netted_offset) and (wd_pass or is_netted_offset) and (math_check or not has_all_summary)

        warning_reasons = []
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
            "warningReason": " | ".join(warning_reasons) if warning_reasons else None,
            "checks": [
                {
                    "name": "Summary Balance Math Check",
                    "passed": math_check if has_all_summary else True,
                    "diff": math_diff,
                    "detail": f"Calculated: Beg {_money(beg)} + Dep {_money(sum_dep)} - WD {_money(sum_wd)} vs End {_money(end)}" if has_all_summary else "Summary balances not printed on single page (Multi-Account statement)"
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
    # FINANCIAL POSITION & MCA UNDERWRITING PASS (mca_others_v3.py)
    # ------------------------------------------------------------------------
    def _run_underwriting(self, data: dict) -> dict:
        txns = data.get("transactions", []) or []
        if not txns:
            return {"analyzed_transactions": [], "summary": {}}

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
