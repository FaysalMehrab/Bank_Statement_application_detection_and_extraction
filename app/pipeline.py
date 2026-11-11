"""
Unified Pipeline Orchestrator:
Zero-Shot Triage -> Topology -> Isolated Engines (V3 / V4) -> Audit -> MCA Analysis
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
    SINGLE_ACCOUNT_SYSTEM_PROMPT,
    MULTI_ACCOUNT_SYSTEM_PROMPT,
    UNDERWRITING_SYSTEM_PROMPT,
)
from app.schemas import (
    CLASSIFICATION_SCHEMA,
    FREEFORM_ENTITY_SCHEMA,
    TOPOLOGY_SCHEMA,
    V3_SINGLE_DOCUMENT_SCHEMA,
    V4_METADATA_SCHEMA,
    V4_BATCH_SCHEMA,
    MCA_ANALYSIS_SCHEMA,
)


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
        # STAGE 1: TRIAGE & CLASSIFICATION
        # --------------------------------------------------------------------
        self.emit("Triaging document type...", 10)
        triage_content = []

        if is_pdf:
            text_p1 = ""
            with pdfplumber.open(file_path) as pdf:
                total_pages = len(pdf.pages)
                if total_pages > 0:
                    text_p1 = pdf.pages[0].extract_text(layout=True) or ""

            if len(text_p1.strip()) >= 150:
                triage_content.append({"type": "input_text", "text": f"Page 1 Layout Text:\n\n{text_p1}"})
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
        category = triage_res.get("category", "other_document")

        # --------------------------------------------------------------------
        # ROUTE A: APPLICATION FORM
        # --------------------------------------------------------------------
        if category == "application_form":
            self.emit("Funding Application identified. Form data extracted.", 100)
            return {
                "documentType": "application_form",
                "status": "success",
                "extractedData": triage_res,
                "usage": self.usage_acc,
                "elapsed": round(time.time() - t0, 2),
            }

        # --------------------------------------------------------------------
        # ROUTE B: IDENTITY, VOIDED CHECK, OR OFFICIAL ENTITY DOCUMENT
        # --------------------------------------------------------------------
        if category in ("driving_licence", "voided_check") or (category == "other_document" and not is_pdf):
            self.emit("Extracting structured summary for entity document...", 50)
            entity_res, u_ent = self.gateway.call_structured(FREEFORM_SUMMARY_PROMPT, triage_content, FREEFORM_ENTITY_SCHEMA, "entity_summary")
            self._track_usage(u_ent)
            return {
                "documentType": category,
                "status": "success",
                "extractedData": entity_res,
                "usage": self.usage_acc,
                "elapsed": round(time.time() - t0, 2),
            }

        # --------------------------------------------------------------------
        # ROUTE C: OTHER DOCUMENT (NO USEFUL INFORMATION)
        # --------------------------------------------------------------------
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
        # ROUTE D: BANK STATEMENT -> STAGE 2: TOPOLOGY DETECTION
        # --------------------------------------------------------------------
        self.emit("Bank Statement confirmed. Detecting statement topology...", 25)
        pages_text = []
        with pdfplumber.open(file_path) as pdf:
            total_pages = len(pdf.pages)
            for idx, p in enumerate(pdf.pages, start=1):
                pages_text.append((idx, p.extract_text(layout=True) or ""))

        header_text = "\n\n".join(f"=== PAGE {p[0]} ===\n{p[1]}" for p in pages_text[:min(2, total_pages)])
        topo_content = [{"type": "input_text", "text": f"Statement header pages:\n\n{header_text}"}]
        topo_res, u_topo = self.gateway.call_structured(TOPOLOGY_SYSTEM_PROMPT, topo_content, TOPOLOGY_SCHEMA, "topology_schema")
        self._track_usage(u_topo)

        is_consolidated = bool(topo_res.get("isConsolidated"))

        # --------------------------------------------------------------------
        # STAGE 3: SEPARATED EXTRACTION ENGINE (SINGLE vs. MULTI)
        # --------------------------------------------------------------------
        if is_consolidated:
            self.emit("Consolidated Multi-Account Statement detected. Running V4 Engine...", 40)
            stmt_data = self._extract_multi_account(file_path, pages_text, total_pages)
        else:
            self.emit("Single-Account Statement confirmed. Running V3 Engine...", 40)
            stmt_data = self._extract_single_account(file_path, pages_text, total_pages)

        # --------------------------------------------------------------------
        # STAGE 4: MATHEMATICAL RECONCILIATION AUDIT
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
    # SINGLE ACCOUNT V3 ENGINE
    # ------------------------------------------------------------------------
    def _extract_single_account(self, file_path: str, pages_text: list, total_pages: int) -> dict:
        full_text = "\n\n".join(f"=== PAGE {p[0]} ===\n{p[1]}" for p in pages_text)

        # Tier 1: Text Layout
        if len(full_text.strip()) > 300:
            content = [{"type": "input_text", "text": f"Complete statement text across all pages:\n\n{full_text}"}]
            doc, u = self.gateway.call_structured(SINGLE_ACCOUNT_SYSTEM_PROMPT, content, V3_SINGLE_DOCUMENT_SCHEMA, "v3_single_document")
            self._track_usage(u)
            doc = self._tag_transaction_ids(doc)
            return doc

        # Tier 2: Vision Fallback
        imgs = convert_from_path(file_path, dpi=IMAGE_DPI)
        content = [{"type": "input_image", "image_url": _pil_to_base64_data_uri(img)} for img in imgs]
        content.insert(0, {"type": "input_text", "text": "Extract all summary metadata and transactions from statement images:"})
        doc, u = self.gateway.call_structured(SINGLE_ACCOUNT_SYSTEM_PROMPT, content, V3_SINGLE_DOCUMENT_SCHEMA, "v3_single_document")
        self._track_usage(u)
        doc = self._tag_transaction_ids(doc)
        return doc

    # ------------------------------------------------------------------------
    # MULTI-ACCOUNT V4 ENGINE (Parallel Micro-batches)
    # ------------------------------------------------------------------------
    def _extract_multi_account(self, file_path: str, pages_text: list, total_pages: int) -> dict:
        header_text = "\n\n".join(f"=== PAGE {p[0]} ===\n{p[1]}" for p in pages_text[:min(2, total_pages)])
        meta_content = [{"type": "input_text", "text": f"TASK: DOCUMENT_METADATA\n\nStatement header pages:\n\n{header_text}"}]
        metadata, u_meta = self.gateway.call_structured(MULTI_ACCOUNT_SYSTEM_PROMPT, meta_content, V4_METADATA_SCHEMA, "v4_metadata")
        self._track_usage(u_meta)

        batch_size = 3
        batches = [pages_text[i:i + batch_size] for i in range(0, total_pages, batch_size)]

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

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(_process_batch, b) for b in batches]
            for f in as_completed(futures):
                res, u = f.result()
                self._track_usage(u)
                all_summaries.extend(res.get("accountSummaries", []))
                all_txs.extend(res.get("transactions", []))

        # Reconcile accounts map
        accounts_map = {}
        for s in all_summaries:
            acc_num = s.get("accountNumber")
            if acc_num and acc_num not in accounts_map:
                s["transactions"] = []
                accounts_map[acc_num] = s

        default_acc = next(iter(accounts_map.keys())) if accounts_map else metadata.get("primaryAccountNumber")
        for tx in all_txs:
            a_key = tx.get("accountNumber") or default_acc
            if a_key in accounts_map:
                accounts_map[a_key]["transactions"].append(tx)

        doc = {
            **metadata,
            "accountNumber": metadata.get("primaryAccountNumber"),
            "accounts": list(accounts_map.values()),
            "transactions": all_txs,
            "beginningBalance": sum(a.get("beginningBalance", 0) for a in accounts_map.values()),
            "endingBalance": sum(a.get("endingBalance", 0) for a in accounts_map.values()),
            "totalDeposits": sum(a.get("totalDeposits", 0) for a in accounts_map.values()),
            "totalWithdrawals": sum(a.get("totalWithdrawals", 0) for a in accounts_map.values()),
        }
        return self._tag_transaction_ids(doc)

    def _tag_transaction_ids(self, data: dict) -> dict:
        txs = data.get("transactions", []) or []
        for idx, tx in enumerate(txs, start=1):
            tx["trID"] = f"TR{idx:05d}"
            tx["amount"] = _parse_num(tx.get("amount"))
            tx["balance"] = _parse_num(tx.get("balance"))
        data["transactions"] = txs
        return data

    # ------------------------------------------------------------------------
    # AUDIT RECONCILIATION & OFFSET RESOLVER
    # ------------------------------------------------------------------------
    def _audit_reconciliation(self, data: dict) -> dict:
        beg = _parse_num(data.get("beginningBalance"))
        end = _parse_num(data.get("endingBalance"))
        sum_dep = _parse_num(data.get("totalDeposits"))
        sum_wd = _parse_num(data.get("totalWithdrawals"))

        txs = data.get("transactions", [])
        credit_sum = round(sum(tx["amount"] for tx in txs if tx.get("type") == "credit" and "[RETURNED UNPAID]" not in (tx.get("description") or "").upper()), 2)
        debit_sum = round(sum(abs(tx["amount"]) for tx in txs if tx.get("type") == "debit" and "[RETURNED UNPAID]" not in (tx.get("description") or "").upper()), 2)

        dep_diff = round(credit_sum - sum_dep, 2) if sum_dep is not None else None
        wd_diff = round(debit_sum - sum_wd, 2) if sum_wd is not None else None

        # Netted offset check
        is_netted_offset = (
            dep_diff is not None and wd_diff is not None
            and abs(round(dep_diff - wd_diff, 2)) <= RECONCILIATION_TOLERANCE
            and abs(dep_diff) > RECONCILIATION_TOLERANCE
        )

        dep_pass = (abs(dep_diff) <= RECONCILIATION_TOLERANCE) if dep_diff is not None else False
        wd_pass = (abs(wd_diff) <= RECONCILIATION_TOLERANCE) if wd_diff is not None else False
        math_check = (abs(round(beg + sum_dep - sum_wd - end, 2)) <= RECONCILIATION_TOLERANCE) if all(v is not None for v in (beg, end, sum_dep, sum_wd)) else False

        passed = (dep_pass or is_netted_offset) and (wd_pass or is_netted_offset) and math_check

        warning_reasons = []
        if not math_check:
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
                {"name": "Summary Balance Math Check", "passed": math_check, "diff": round(beg + sum_dep - sum_wd - end, 2) if math_check is not None else None},
                {"name": "Deposits Reconciliation", "passed": dep_pass or is_netted_offset, "diff": dep_diff},
                {"name": "Withdrawals Reconciliation", "passed": wd_pass or is_netted_offset, "diff": wd_diff},
            ]
        }

    # ------------------------------------------------------------------------
    # FINANCIAL POSITION & MCA PASS
    # ------------------------------------------------------------------------
    def _run_underwriting(self, data: dict) -> dict:
        txns = data.get("transactions", [])
        if not txns:
            return {"analyzed_transactions": [], "summary": {}}

        lines = ["trID,postingDate,type,amount,description"]
        for tx in txns:
            lines.append(f"{tx['trID']},{tx['postingDate']},{tx['type']},{tx['amount']},{tx['description']}")
        csv_table = "\n".join(lines)

        user_msg = (
            f"STATEMENT SUMMARY:\n"
            f"Bank: {data.get('bankName')} | Account: {data.get('accountNumber')}\n"
            f"Period: {data.get('startdate')} to {data.get('enddate')}\n\n"
            f"TRANSACTIONS TABLE:\n{csv_table}"
        )

        res, u = self.gateway.call_structured(UNDERWRITING_SYSTEM_PROMPT, [{"type": "input_text", "text": user_msg}], MCA_ANALYSIS_SCHEMA, "mca_underwriting")
        self._track_usage(u)

        analyzed = res.get("analyzed_transactions", [])
        mca_items = [t for t in analyzed if t.get("isMca")]
        cc_items = [t for t in analyzed if t.get("isCreditCard")]
        loan_items = [t for t in analyzed if t.get("isBusinessLoan")]
        bnpl_items = [t for t in analyzed if t.get("isBnpl")]
        returned_items = [t for t in analyzed if t.get("isReturnedItem")]

        return {
            "analyzed_transactions": analyzed,
            "mca_count": len(mca_items),
            "cc_count": len(cc_items),
            "loan_count": len(loan_items),
            "bnpl_count": len(bnpl_items),
            "returned_count": len(returned_items),
        }


def _money(val):
    return "N/A" if val is None else f"${float(val):,.2f}"
