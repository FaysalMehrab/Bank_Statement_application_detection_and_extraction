#!/usr/bin/env python3
"""
Bank Statement Extraction Engine — Version 3 (Production Single-Prompt Aligned)
Model: GPT-5.6 Terra (reasoning: none)
"""

import base64
import io
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime

import pdfplumber
from dotenv import load_dotenv
from openai import OpenAI
from pdf2image import convert_from_path

try:
    from pypdf import PdfReader, PdfWriter
except ImportError:
    try:
        from PyPDF2 import PdfReader, PdfWriter
    except ImportError:
        PdfReader = None
        PdfWriter = None

load_dotenv()

# ============================================================================
# CONFIGURATION
# ============================================================================

PDF_FILE_PATH = (
    sys.argv[1] if len(sys.argv) > 1 else
    "MCA_Probs_and_issues/new_prob_1_10_26/Bela 1 Travel/Bank of America, N.A._x6448_06-01-2026_to_06-30-2026.pdf"
)

OUTPUT_ROOT = "New_mca_positions/MCA_prob/outputs"
RECONCILIATION_TOLERANCE = 0.01

IMAGE_DPI = 300
IMAGE_DETAIL = "high"

MODEL_CONFIG = {
    "name": "GPT-5.6 Terra",
    "model": "gpt-5.6-terra",
    "reasoning_effort": "high",
    "input_price": 2.00,
    "cached_input_price": 0.20,
    "output_price": 12.00,
}

# ============================================================================
# SINGLE CONSOLIDATED PRODUCTION PROMPT
# ============================================================================

BANK_STATEMENT_SYSTEM_PROMPT = """You are a financial document extraction engine specialized in bank statements.
Your task is to extract the complete statement summary metadata and ALL individual transaction line items across all provided pages.

PART 1: STATEMENT SUMMARY & METADATA RULES
Extract the high-level statement summary and balance metadata from the document:
- Bank Name: Name of the bank issuing the statement
- Account Holder: Name of the account holder
- Account Holder Address, City, State, Zip: Address details, null if missing
- Account Number: Bank account number
- Statement Period: Start and end dates in YYYY-MM-DD format
- Beginning Balance: Balance at start of statement period
- Ending Balance: Balance at end of statement period
- Average Daily Balance / Ledger Balance: Extract if present, null if absent
- Total Deposits: Total deposits amount from summary
- Total Withdrawals: Total withdrawals amount from summary (see Fee Invariant below)
- Number of Credit / Debit Transactions: Counts from summary, null if absent
- Service Charges: Total service charges / fees applied, null if absent

SUMMARY TOTAL WITHDRAWALS & FEES RECONCILIATION INVARIANT:
- Total Withdrawals must reflect all total debit outflows deducted from the account during the period.
- If the statement summary separates 'Total Withdrawals' and 'Total Fees / Service Charges' as distinct line items (e.g., Withdrawals: $16,445.18 and Fees: $190.00), extract 'totalWithdrawals' as the sum of both (Withdrawals + Fees = $16,635.18), so that total withdrawals reconciles with all debits posted to the account.
- Still extract the isolated fee amount into 'serviceCharges' ($190.00).
- If the statement already includes fees within total withdrawals, extract the printed total directly without adding anything.

PART 2: TRANSACTION LINE ITEMS RULES
1. Extract EVERY individual transaction line item across all pages without omission, deduplication, or truncation.
2. Amounts: Positive numbers for credits/deposits, negative numbers for debits/withdrawals/fees/checks.
3. If a running balance or ending daily balance is explicitly printed on the row, extract it into 'balance'; otherwise use null.

POLARITY & DAY-LEVEL MATH INVARIANT:
- Statements often have two amount columns ('Deposits/Credits' and 'Withdrawals/Debits'). Check column alignment carefully!
- Semantic words: 'Purchase', 'Payment', 'ACH Debit', 'Zelle to', 'Check' are DEBITS. 'Online Transfer From', 'Zelle From', 'Deposit' are CREDITS.
- ENDING DAILY BALANCE NOTE: When printed, daily ending balance appears only once on the last transaction of the day and reflects the net result of all transactions for that day. Verify: Previous Day Balance + Day Deposits - Day Withdrawals = Ending Daily Balance.

RETURNED / REJECTED / UNPAID ITEMS INVARIANT:
- Any transaction that did not clear or was returned/unpaid—whether listed in a separate schedule (e.g., 'Items returned unpaid', 'Returned checks', 'NSF notices') OR printed inline within the main transaction tables marked with words like '(Rejected)', 'Rejected', 'Returned', 'Unpaid', 'NSF', or 'Bounced'—MUST have its description prefixed with '[RETURNED UNPAID] '.
- Example: 'External Withdrawal Fundbox INC... (Rejected)' -> '[RETURNED UNPAID] External Withdrawal Fundbox INC... (Rejected)'.
- If the returned/rejected item appears in the deposit table as an offsetting entry, preserve it as a credit (+ amount) with the '[RETURNED UNPAID] ' prefix. If it appears in the withdrawal table, preserve it as a debit (- amount) with the '[RETURNED UNPAID] ' prefix.
- This prefix is strictly required so that downstream reconciliation engines isolate non-clearing entries from cleared totals.
- FEES CHARGED FOR A RETURNED ITEM ARE NOT RETURNED ITEMS: A fee or charge that the bank assessed because an item was returned, rejected, unpaid, uncollected, or paid into overdraft (e.g., 'Insufficient Funds Charge', 'Uncollected Funds Charge', 'Returned Item Fee', 'NSF Fee', 'Overdraft Fee') is a normal posted debit that counts toward fees and total withdrawals. Do NOT prefix it with '[RETURNED UNPAID] ', even when its description contains words such as 'Returned', 'Unpaid', 'Paid', or 'NSF'. Prefix only the item that failed to clear (the payment, check, ACH debit, or deposit itself), never the fee charged for it.
- REJECTED ITEM THAT REVERSES AN EARLIER ENTRY (PAIRING): Some statements print a rejected/returned item as an offsetting entry that reverses an earlier posted entry (for example, a '(Rejected)' line with a positive amount in the deposits section whose amount and counterparty/reference ID match an earlier debit printed in the withdrawals section, or the opposite direction). The original entry and its offsetting entry net to zero and are excluded from the statement's printed summary totals together. When both halves are printed:
  * Prefix the offsetting entry (as required above) AND ALSO prefix the matching original entry with '[RETURNED UNPAID] ', keeping the original entry's own sign and amount.
  * Match strictly one-to-one: same amount, same counterparty and reference/ID, opposite direction, on the same date or the nearest preceding date. Each rejection matches exactly one original entry.
  * Any other entry with the same amount and counterparty (for example, a later re-presentment that was paid) is a normal cleared transaction and must NOT be prefixed.
  * Check against the printed summary totals: the untagged credits should add up to Total Deposits, and the untagged debits (including fees and checks) should add up to Total Withdrawals. If the printed totals clearly INCLUDE the original entry, tag only the offsetting entry as before.
  * If no matching original entry is printed, tag only the rejected entry itself, as before.

BOUNDARY & GROUPING RULES:
- Same-day groupings: If multiple transactions occur on the same day and the date is only printed on the first row, propagate that date to the following transactions in the same block.
- Multi-line descriptions: Concatenate indented sub-lines using ' - '. Strip formatting artifacts like bullets or leading quotes.
- Exclude statement totals, opening/closing balance headers, and daily balance summary tables from the transaction array.
"""

# ============================================================================
# UNIFIED DOCUMENT SCHEMA
# ============================================================================

FULL_DOCUMENT_SCHEMA = {
    "type": "json_schema",
    "name": "document_schema",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "bankName": {"type": "string"},
            "accountHolder": {"type": "string"},
            "accountHolderAddress": {"type": ["string", "null"]},
            "accountHolderCity": {"type": ["string", "null"]},
            "accountHolderState": {"type": ["string", "null"]},
            "accountHolderZip": {"type": ["string", "null"]},
            "accountNumber": {"type": "string"},
            "startdate": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
            "enddate": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
            "beginningBalance": {"type": "number"},
            "endingBalance": {"type": "number"},
            "avgDailyBalance": {"type": ["number", "null"]},
            "avgLedgerBalance": {"type": ["number", "null"]},
            "totalDeposits": {"type": "number"},
            "totalWithdrawals": {"type": "number"},
            "numberOfCreditTransactions": {"type": ["integer", "null"]},
            "numberOfDebitsTransactions": {"type": ["integer", "null"]},
            "serviceCharges": {"type": ["number", "null"]},
            "transactions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "postingDate": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
                        "description": {"type": "string", "minLength": 1},
                        "amount": {"type": "number"},
                        "type": {"type": "string", "enum": ["credit", "debit"]},
                        "balance": {"type": ["number", "null"]},
                    },
                    "required": ["postingDate", "description", "amount", "type", "balance"],
                    "additionalProperties": False,
                },
            },
        },
        "required": [
            "bankName", "accountHolder", "accountHolderAddress", "accountHolderCity",
            "accountHolderState", "accountHolderZip", "accountNumber", "startdate", "enddate",
            "beginningBalance", "endingBalance", "avgDailyBalance", "avgLedgerBalance",
            "totalDeposits", "totalWithdrawals", "numberOfDebitsTransactions",
            "numberOfCreditTransactions", "serviceCharges", "transactions",
        ],
        "additionalProperties": False,
    },
}

# ============================================================================
# HELPERS
# ============================================================================

def _money(val):
    if val is None:
        return "N/A"
    return f"${float(val):,.2f}"

def _diff(actual, expected):
    if actual is None or expected is None:
        return None
    return round(float(actual) - float(expected), 2)

def _passes_diff(diff):
    return diff is not None and abs(diff) <= RECONCILIATION_TOLERANCE

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

# ============================================================================
# EXTRACTION ENGINE (OPTION 2: SINGLE CONSOLIDATED CALL)
# ============================================================================

class TransactionExtractorV3:
    def __init__(self):
        self.client = OpenAI()

    def _calc_cost(self, usage):
        in_tok = getattr(usage, "input_tokens", 0) or 0
        out_tok = getattr(usage, "output_tokens", 0) or 0
        details = getattr(usage, "input_tokens_details", None)
        cached_tok = getattr(details, "cached_tokens", 0) or 0
        uncached_tok = max(in_tok - cached_tok, 0)

        in_cost = (uncached_tok / 1_000_000 * MODEL_CONFIG["input_price"])
        cached_cost = (cached_tok / 1_000_000 * MODEL_CONFIG["cached_input_price"])
        out_cost = (out_tok / 1_000_000 * MODEL_CONFIG["output_price"])

        return {
            "input_tokens": in_tok,
            "cached_input_tokens": cached_tok,
            "uncached_input_tokens": uncached_tok,
            "output_tokens": out_tok,
            "reasoning_tokens": 0,
            "input_cost": in_cost + cached_cost,
            "output_cost": out_cost,
            "total_cost": in_cost + cached_cost + out_cost,
        }

    def is_rich_text_pdf(self, path):
        try:
            with pdfplumber.open(path) as pdf:
                num_pages = len(pdf.pages)
                if num_pages == 0:
                    return False
                text = "".join((p.extract_text() or "") + "\n" for p in pdf.pages).strip()

            if len(text) < 250 or (len(text) / num_pages) < 150:
                return False
            if len(re.findall(r"\b\d{1,3}(?:,\d{3})*\.\d{2}\b", text)) < 4:
                return False
            return True
        except Exception:
            return False

    def _slice_pdf_to_base64(self, pdf_path, start_0idx, end_0idx):
        if PdfReader is None:
            return None
        with open(pdf_path, "rb") as f:
            reader = PdfReader(f)
            writer = PdfWriter()
            total = len(reader.pages)
            for p in range(start_0idx, min(end_0idx, total)):
                writer.add_page(reader.pages[p])
            buf = io.BytesIO()
            writer.write(buf)
            buf.seek(0)
            return f"data:application/pdf;base64,{base64.b64encode(buf.read()).decode('utf-8')}"

    # ------------------------------------------------------------------------
    # TIER 1: SINGLE-CALL BATCHED LAYOUT TEXT MODE
    # ------------------------------------------------------------------------
    def _extract_tier1_text(self, pdf_path):
        started_at = time.perf_counter()
        pages_text = []
        with pdfplumber.open(pdf_path) as pdf:
            for idx, p in enumerate(pdf.pages, start=1):
                p_text = p.extract_text(layout=True) or ""
                pages_text.append(f"=== PAGE {idx} ===\n{p_text}")

        full_doc_text = "\n\n".join(pages_text)

        resp = self.client.responses.create(
            model=MODEL_CONFIG["model"],
            instructions=BANK_STATEMENT_SYSTEM_PROMPT,
            input=[{"role": "user", "content": f"Document content across all pages:\n\n{full_doc_text}"}],
            text={"format": FULL_DOCUMENT_SCHEMA},
        )

        usage = self._calc_cost(resp.usage)
        usage["elapsed_seconds"] = round(time.perf_counter() - started_at, 2)
        doc = json.loads(resp.output_text)
        return doc, usage

    # ------------------------------------------------------------------------
    # TIER 2: SINGLE-CALL BASE64 MULTIMODAL VISION MODE
    # ------------------------------------------------------------------------
    def _extract_tier2_base64(self, pdf_path):
        started_at = time.perf_counter()
        with open(pdf_path, "rb") as f:
            total_pages = len(PdfReader(f).pages)

        full_b64 = self._slice_pdf_to_base64(pdf_path, 0, total_pages)

        resp = self.client.responses.create(
            model=MODEL_CONFIG["model"],
            instructions=BANK_STATEMENT_SYSTEM_PROMPT,
            input=[{"role": "user", "content": [{"type": "input_file", "filename": "statement.pdf", "file_data": full_b64}]}],
            text={"format": FULL_DOCUMENT_SCHEMA},
        )

        usage = self._calc_cost(resp.usage)
        usage["elapsed_seconds"] = round(time.perf_counter() - started_at, 2)
        doc = json.loads(resp.output_text)
        return doc, usage

    # ------------------------------------------------------------------------
    # TIER 3: SINGLE-CALL HIGH-RES IMAGE VISION FALLBACK
    # ------------------------------------------------------------------------
    def _extract_tier3_images(self, pdf_path):
        started_at = time.perf_counter()
        images = convert_from_path(pdf_path, dpi=IMAGE_DPI)
        temp_dir = f"temp_{uuid.uuid4().hex[:8]}"
        os.makedirs(temp_dir, exist_ok=True)
        paths = []
        for idx, img in enumerate(images, start=1):
            p = os.path.join(temp_dir, f"page_{idx}.png")
            img.save(p, "PNG")
            paths.append(p)

        content = []
        for p in paths:
            with open(p, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
            content.append({"type": "input_image", "image_url": f"data:image/png;base64,{b64}", "detail": IMAGE_DETAIL})

        resp = self.client.responses.create(
            model=MODEL_CONFIG["model"],
            instructions=BANK_STATEMENT_SYSTEM_PROMPT,
            input=[{"role": "user", "content": content}],
            text={"format": FULL_DOCUMENT_SCHEMA},
        )

        for p in paths:
            try:
                os.remove(p)
            except OSError:
                pass
        try:
            os.rmdir(temp_dir)
        except OSError:
            pass

        usage = self._calc_cost(resp.usage)
        usage["elapsed_seconds"] = round(time.perf_counter() - started_at, 2)
        doc = json.loads(resp.output_text)
        return doc, usage

    # ------------------------------------------------------------------------
    # ZERO-TRUST ROUTER
    # ------------------------------------------------------------------------
    def extract_from_pdf(self, pdf_path):
        is_digital = self.is_rich_text_pdf(pdf_path)

        # --------------------------------------------------------------------
        # TIER 1: Digital text extraction via pdfplumber
        # --------------------------------------------------------------------
        if is_digital:
            print("PDF Type: Digital TruePDF -> Routing to Tier 1: Layout Text Mode (Single Call)...")
            raw_data, usage = self._extract_tier1_text(pdf_path)
            processed_data, _ = self._process_extraction_response(raw_data)
            validation = self._validate_extraction(processed_data)

            # ALWAYS print Tier 1 verification report (even on failure)
            self.print_report(processed_data, validation, usage, tier_name="TIER 1 (LAYOUT TEXT)")

            if validation["passed"]:
                print("Gate Result: [PASS] Deposits and withdrawals reconciled within tolerance. Tier 1 accepted.")
                return processed_data, usage, validation

            print("\nGate Result: [FAIL] Discrepancy detected in Tier 1. Escalating to Tier 2: Base64 Vision...\n")

        # --------------------------------------------------------------------
        # TIER 2: Multimodal vision via Base64
        # --------------------------------------------------------------------
        if PdfReader is not None:
            print("Routing to Tier 2: Base64 Multimodal Vision Mode (Single Call)...")
            raw_data, usage = self._extract_tier2_base64(pdf_path)
            processed_data, _ = self._process_extraction_response(raw_data)
            validation = self._validate_extraction(processed_data)

            # ALWAYS print Tier 2 verification report
            self.print_report(processed_data, validation, usage, tier_name="TIER 2 (BASE64 VISION)")

            if validation["passed"] or not is_digital:
                if validation["passed"]:
                    print("Gate Result: [PASS] Deposits and withdrawals reconciled within tolerance. Tier 2 accepted.")
                return processed_data, usage, validation

            print("\nGate Result: [FAIL] Discrepancy detected in Tier 2. Escalating to Tier 3: High-Res Vision Fallback...\n")

        # --------------------------------------------------------------------
        # TIER 3: High-Res Vision Fallback (pdf2image)
        # --------------------------------------------------------------------
        print("Routing to Tier 3: High-Res Vision Fallback (Single Call)...")
        raw_data, usage = self._extract_tier3_images(pdf_path)
        processed_data, _ = self._process_extraction_response(raw_data)
        validation = self._validate_extraction(processed_data)
        self.print_report(processed_data, validation, usage, tier_name="TIER 3 (HIGH-RES IMAGES)")
        return processed_data, usage, validation

    # ------------------------------------------------------------------------
    # RESPONSE PROCESSING & AUDIT
    # ------------------------------------------------------------------------
    def _process_extraction_response(self, data):
        txs = data.get("transactions", []) or []
        valid_txs = []
        malformed = 0

        for tx in txs:
            if not isinstance(tx, dict):
                malformed += 1
                continue

            dt = tx.get("postingDate")
            desc = tx.get("description")
            amt = _parse_num(tx.get("amount"))

            if not dt or not desc or amt is None:
                malformed += 1
                continue

            tx["amount"] = amt
            tx["type"] = tx.get("type") or ("credit" if amt > 0 else "debit")
            tx["balance"] = _parse_num(tx.get("balance"))
            valid_txs.append(tx)

        for idx, tx in enumerate(valid_txs, start=1):
            tx["trID"] = f"TR{idx:05d}"

        data["transactions"] = valid_txs
        return data, malformed

    def _validate_extraction(self, data):
        sum_dep = _parse_num(data.get("totalDeposits"))
        sum_wd = _parse_num(data.get("totalWithdrawals"))

        txs = data.get("transactions", []) or []
        credit_sum = 0.0
        debit_sum = 0.0
        credit_count = 0
        debit_count = 0

        for tx in txs:
            amt = _parse_num(tx.get("amount"))
            tx_type = tx.get("type")
            desc = (tx.get("description") or "").strip()

            # Skip rejected/unpaid items from cleared reconciliation totals
            if "[RETURNED UNPAID]" in desc.upper():
                continue

            if amt is not None:
                if tx_type == "credit":
                    credit_count += 1
                    credit_sum += abs(amt)
                elif tx_type == "debit":
                    debit_count += 1
                    debit_sum += abs(amt)

        credit_sum = round(credit_sum, 2)
        debit_sum = round(debit_sum, 2)

        dep_diff = _diff(credit_sum, sum_dep)
        w_diff = _diff(debit_sum, sum_wd)

        dep_passed = _passes_diff(dep_diff)
        w_passed = _passes_diff(w_diff)

        checks = [
            {
                "name": "Deposits vs credit transactions",
                "passed": dep_passed,
                "difference": dep_diff,
                "detail": f"Extracted {_money(credit_sum)} vs summary {_money(sum_dep)}",
            },
            {
                "name": "Withdrawals vs debit transactions",
                "passed": w_passed,
                "difference": w_diff,
                "detail": f"Extracted {_money(debit_sum)} vs summary {_money(sum_wd)}",
            },
        ]

        overall_passed = dep_passed and w_passed

        return {
            "passed": overall_passed,
            "checks": checks,
            "transaction_count": len(txs),
            "credit_count": credit_count,
            "debit_count": debit_count,
            "credit_sum": credit_sum,
            "debit_sum": debit_sum,
            "summary_deposits": sum_dep,
            "summary_withdrawals": sum_wd,
        }

    def print_report(self, data, validation, usage, tier_name="TIER 1 (LAYOUT TEXT)"):
        print("\n" + "=" * 78)
        print(f"EXTRACTED STATEMENT SUMMARY — {tier_name}")
        print("=" * 78)
        print(f"  Bank:                {data.get('bankName')}")
        print(f"  Account Holder:      {data.get('accountHolder')}")
        print(f"  Holder Address:      {data.get('accountHolderAddress') or 'N/A'}")

        city = data.get("accountHolderCity") or ""
        state = data.get("accountHolderState") or ""
        zip_code = data.get("accountHolderZip") or ""
        loc = f"{city}, {state} {zip_code}".strip()
        print(f"  City/State/Zip:      {loc if loc and loc != ',' else 'N/A'}")

        print(f"  Account Number:      {data.get('accountNumber')}")
        print(f"  Period:              {data.get('startdate')} to {data.get('enddate')}")
        print(f"  Beginning Balance:   {_money(data.get('beginningBalance'))}")
        print(f"  Ending Balance:      {_money(data.get('endingBalance'))}")
        print(f"  Avg Daily Balance:   {_money(data.get('avgDailyBalance'))}")
        print(f"  Total Deposits:      {_money(data.get('totalDeposits'))}")
        print(f"  Total Withdrawals:   {_money(data.get('totalWithdrawals'))}")
        print(f"  Extracted Tx Count:  {validation['transaction_count']}")

        print("\nTRANSACTION COUNTS:")
        print(f"  • Credit Transactions Count:  {validation['credit_count']}")
        print(f"  • Debit Transactions Count:   {validation['debit_count']}")

        print("\nRECONCILIATION AUDIT:")
        print("-" * 78)
        for c in validation["checks"]:
            status = "PASS" if c["passed"] else "FAIL"
            suffix = f" | diff: {_money(c['difference'])}" if c["difference"] is not None else ""
            print(f"  [{status}] {c['name']}: {c['detail']}{suffix}")

        overall = "PASS" if validation["passed"] else "FAIL"
        print("-" * 78)
        print(f"  OVERALL TIER STATUS: {overall}")
        print("-" * 78)
        print("TOKEN USAGE & API COST:")
        print(f"  Input Tokens:        {usage['input_tokens']:,} (Cached: {usage['cached_input_tokens']:,})")
        print(f"  Output Tokens:       {usage['output_tokens']:,}")
        print(f"  Total Cost:          ${usage['total_cost']:.6f}")
        print(f"  Elapsed Time:        {usage.get('elapsed_seconds', 0.0)}s")
        print("=" * 78 + "\n")


# ============================================================================
# MAIN
# ============================================================================

def main():
    if not os.path.exists(PDF_FILE_PATH):
        print(f"Error: PDF '{PDF_FILE_PATH}' not found.")
        sys.exit(1)

    if not os.getenv("OPENAI_API_KEY"):
        print("Error: OPENAI_API_KEY is not set.")
        sys.exit(1)

    print("=" * 78)
    print("BANK STATEMENT EXTRACTION ENGINE — V3 (ALIGNED ZERO-TRUST HYBRID)")
    print(f"Target: {os.path.basename(PDF_FILE_PATH)}")
    print(f"Model:  {MODEL_CONFIG['name']} ({MODEL_CONFIG['model']})")
    print("=" * 78)

    extractor = TransactionExtractorV3()
    extracted_data, usage, validation = extractor.extract_from_pdf(PDF_FILE_PATH)

    safe_name = MODEL_CONFIG["name"].lower().replace(".", "_").replace(" ", "_").replace("-", "_")
    out_dir = os.path.join(OUTPUT_ROOT, safe_name)
    os.makedirs(out_dir, exist_ok=True)
    base_fn = os.path.splitext(os.path.basename(PDF_FILE_PATH))[0]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(out_dir, f"{base_fn}_{safe_name}_v3_{ts}.json")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(extracted_data, f, indent=2, ensure_ascii=False)

    print(f"Saved Final JSON: {out_path}")


if __name__ == "__main__":
    main()