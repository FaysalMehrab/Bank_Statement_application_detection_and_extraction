#!/usr/bin/env python3
"""
Statement Topology Detector (single vs. multi-account)

Debug-only script: asks the LLM whether bank statement PDFs are SINGLE-account
statements or CONSOLIDATED (multi-account) statements, and shows WHY.
Runs over one or many PDFs and prints a summary table at the end.

Usage:
    python detect_topology.py                                  # runs the DEFAULT_PDFS list below
    python detect_topology.py a.pdf b.pdf c.pdf                # specific files
    python detect_topology.py --prompt current                 # your current prompt only
    python detect_topology.py --prompt revised                 # revised prompt only
    python detect_topology.py --vision                         # send page images instead of text
    python detect_topology.py --max-pages 3                    # only look at the first 3 pages
    python detect_topology.py --summary-only                   # skip per-file details, show table only
    python detect_topology.py --json results.json              # save all raw results to a JSON file
"""

import argparse
import base64
import io
import json
import os
import re
import sys

import pdfplumber
from dotenv import load_dotenv
from openai import OpenAI
from pdf2image import convert_from_path

load_dotenv()

# ============================================================================
# CONFIGURATION
# ============================================================================

MODEL = "gpt-5.6-terra"
REASONING = {"effort": "high"}
IMAGE_DPI = 150
IMAGE_DETAIL = "high"

# Used when no PDF paths are passed on the command line.
DEFAULT_PDFS = [
    # Single account examples:
    "statements/26_10_2026/63355157-1189-4768-b5dc-3147b5f13a10_d53c0018-b357-497d-8e28-78d2068ee120_GetDocument__28__NOV2025__1_._111646 (1).pdf",
    "MCA_Probs_and_issues/MCA_Warnings/Needham Bank_x5737_09-01-2026_to_09-30-2026.pdf",
    "MCA_Probs_and_issues/MCA_Warnings/Wells Fargo Bank, N.A._x1976_04-01-2026_to_04-30-2026.pdf",
    "MCA_Probs_and_issues/MCA_Warnings/Wells Fargo Bank, N.A._x1976_06-01-2026_to_06-30-2026.pdf",
    "MCA_Probs_and_issues/MCA_Warnings/Wells Fargo Bank, N.A._x1976_08-01-2026_to_08-31-2026.pdf",
    "New_mca_positions/ Cory T. Takata DBA Cory T. Takata/pdf_Statemnts/First Hawaiian Bank_x0364_03-31-2026_to_04-30-2026.pdf",
    "New_mca_positions/New Heights Farm I LLC/pdf_Statemnts/x3799_07-01-2026_to_07-27-2026.pdf",
    "New_mca_positions/Tony’s Gym Inc/pdf_Statemnts/ab&t_x1949_05-01-2026_to_05-31-2026.pdf",
    


    # Multi acount examples (consolidated statements):
    "statements/20_10_2026/US Eagle Federal Credit Union_x0001_08-01-2026_to_08-31-2026.pdf",
    "statements/20_10_2026/US Eagle Federal Credit Union_x0001_06-01-2026_to_06-30-2026.pdf",
    "statements/20_10_2026/US Eagle Federal Credit Union_x0001_07-01-2026_to_07-31-2026.pdf",
    "statements/20_10_2026/US Eagle Federal Credit Union_x0001_09-01-2026_to_09-30-2026.pdf",
    "statements/12_10_2026/statements/Truist Bank_x3718_08-01-2026_to_08-31-2026.pdf",
    "statements/12_10_2026/statements/Truist Bank_x3718_09-01-2026_to_09-30-2026.pdf",
    "statements/12_10_2026/statements/Truist_x3718_05-29-2026_to_06-30-2026.pdf",
    "statements/12_10_2026/statements/Truist_x3718_07-01-2026_to_07-31-2026.pdf",


]

# ============================================================================
# PROMPTS
# ============================================================================

CURRENT_PROMPT = """DOCUMENT TOPOLOGY (SINGLE VS MULTI-ACCOUNT):
- Check if this document covers a SINGLE bank account or is a CONSOLIDATED statement covering multiple accounts:
  * isConsolidated: Set to true ONLY if the document explicitly covers multiple distinct accounts (e.g., 'Consolidated Statement', 'Summary of your accounts' with separate accounts). Otherwise, set false.
  * IF SINGLE ACCOUNT (isConsolidated == false):
    Extract beginningBalance, endingBalance, totalDeposits, and totalWithdrawals directly from Page 1's summary box.
    MANDATORY NORMALIZATION RULE: If the statement summary lists 'Total Withdrawals' and a separate 'Total Fees' or 'Service Charges' line on different rows, sum them together into Total Withdrawals so that: Beginning Balance + Total Deposits - Total Withdrawals = Ending Balance.
  * IF MULTI-ACCOUNT (isConsolidated == true):
    Set beginningBalance, endingBalance, totalDeposits, and totalWithdrawals to null (they will be captured directly from each account section).
"""

REVISED_PROMPT = """DOCUMENT TOPOLOGY (SINGLE VS MULTI-ACCOUNT):
- Decide whether this document covers a SINGLE bank account or is a CONSOLIDATED statement covering multiple distinct accounts.

  * HOW TO DECIDE: Collect every account number printed in the page headers and section headings (masked numbers count, e.g. XXXXXX3817). Set isConsolidated = true ONLY if two or more DIFFERENT account numbers appear (e.g. 'Checking ...1234' and 'Savings ...5678', or a 'Consolidated Statement' / 'Summary of your accounts' listing separate account numbers). Otherwise set isConsolidated = false.

  * EXCEPTION (separate full accounts under one account/member number): Sub-accounts that share one account/member number are normally ONE account (see NOT CONSOLIDATED below). Set isConsolidated = true anyway ONLY when TWO OR MORE sub-accounts EACH have ALL of the following: (1) their own section heading, (2) their own beginning and ending balance, (3) their own printed period totals for BOTH deposits and withdrawals (e.g. 'N Total Deposits for $X' and 'N Total Withdrawals for $Y'), and (4) real activity (non-zero deposits or withdrawals) with their own transaction listing.

  * NOT CONSOLIDATED (these do NOT count as multiple accounts):
    - Multiple rows in a summary table that all sit under ONE account/member number. Credit unions often list sub-accounts ('Shares', share IDs such as 0000 / 0010 / 0020, 'Suffixes', 'Sub-accounts') under a single member number. Treat these as ONE account, unless the EXCEPTION above applies.
    - A secondary sub-account with no transactions and a flat balance (e.g. a $5.00 membership savings share).
    - Repeated page headers showing the same account number.
    - Multiple sections for the same account (e.g. 'Continued' pages).
    - Sub-accounts under one account/member number that show only a previous/new balance and a transaction list, without their own printed deposit and withdrawal period totals (the EXCEPTION requires those totals).
    - A loan or credit section (installment loan, line of credit, mortgage), including a closed loan, listed under the same account/member number as the deposit account(s).
    - Account-like references or masked name fragments that appear only inside transaction descriptions (e.g. 'Transfer To Share 71', 'XXXXXXXXXX').

  * IF SINGLE ACCOUNT (isConsolidated == false):
    - If the summary box has one row, extract beginningBalance, endingBalance, totalDeposits, and totalWithdrawals directly from it.
    - If the summary box lists several sub-account rows under the same account number, use the row for the sub-account that contains the transaction listing (the one with the transactions and running balances). Ignore inactive sub-accounts (zero deposits and zero withdrawals) for these totals.
    - If there is no summary box and several sub-accounts have transaction listings, use the sub-account that prints period totals for deposits and withdrawals (typically the checking / share draft account). Take its previous (beginning) balance and new (ending) balance, and ignore the other sub-accounts for these four values.
    - MANDATORY NORMALIZATION RULE: If the statement summary lists 'Total Withdrawals' and a separate 'Total Fees' or 'Service Charges' line on different rows, sum them together into Total Withdrawals so that: Beginning Balance + Total Deposits - Total Withdrawals = Ending Balance. The same applies when withdrawals are split across separately printed totals (e.g. 'Withdrawals and Other Charges' plus 'Checks/Drafts Cleared'): sum all of them into Total Withdrawals.

  * IF MULTI-ACCOUNT (isConsolidated == true):
    Set beginningBalance, endingBalance, totalDeposits, and totalWithdrawals to null (they will be captured directly from each account section).

  * EXAMPLE (single account): A summary table listing '0000 BUSINESS REGULAR SAVINGS 5.00 / 0.00 / 0.00 / 5.00' and '0010 BUSINESS ECOCHECKING 2,744.81 / -19,137.19 / 17,389.69 / 997.31', with the same account number XXXXXX3817 on every page, is ONE account (isConsolidated = false). Use the 0010 row: beginningBalance 2,744.81, totalWithdrawals 19,137.19, totalDeposits 17,389.69, endingBalance 997.31.

  * EXAMPLE (consolidated under one member number): One member number is printed on every page and the statement lists 'Share A' and 'Checking B'. Each has its own heading, its own beginning and ending balance, its own printed 'N Total Deposits' and 'N Total Withdrawals', and its own transaction table, and both have non-zero activity. isConsolidated = true and all four totals are null.

  * EXAMPLE (single account, several shares): One account number is printed on every page. The statement has a savings share, a tax share, a checking/share-draft share and a closed loan, each shown as a section with only a previous balance, transactions, and a new balance. Only the checking share also prints period totals for deposits and withdrawals (plus cleared drafts/checks). isConsolidated = false. Take beginning/ending balance from the checking share, totalDeposits from its printed deposit total, and totalWithdrawals as the sum of all its printed withdrawal-side totals.
    """

PROMPTS = {"current": CURRENT_PROMPT, "revised": REVISED_PROMPT}

# Appended to BOTH prompts so the model explains itself. The prompt text above
# is left untouched, so the isConsolidated decision is still driven by it.
DEBUG_SUFFIX = """
DEBUG OUTPUT (diagnostic only):
- evidence: In 1-3 short sentences, state exactly which text or table in the document led to your isConsolidated decision.
- accountNumbersFound: List every distinct account number / member number / share ID you saw in the document, exactly as printed (masked values included).
"""

INSTRUCTIONS_HEADER = (
    "You are an expert financial document analysis engine specialized in bank statements. "
    "Analyze the provided statement and answer the topology question below.\n\n"
)

# ============================================================================
# SCHEMA
# isConsolidated is first on purpose: it mirrors a production schema where the
# decision comes before any explanation, so the test reflects real behavior.
# ============================================================================

TOPOLOGY_SCHEMA = {
    "type": "json_schema",
    "name": "topology_schema",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "isConsolidated": {"type": "boolean"},
            "evidence": {"type": "string"},
            "accountNumbersFound": {"type": "array", "items": {"type": "string"}},
            "beginningBalance": {"type": ["number", "null"]},
            "endingBalance": {"type": ["number", "null"]},
            "totalDeposits": {"type": ["number", "null"]},
            "totalWithdrawals": {"type": ["number", "null"]},
        },
        "required": [
            "isConsolidated", "evidence", "accountNumbersFound",
            "beginningBalance", "endingBalance", "totalDeposits", "totalWithdrawals",
        ],
        "additionalProperties": False,
    },
}

# ============================================================================
# HELPERS
# ============================================================================

def _money(val):
    return "null" if val is None else f"${float(val):,.2f}"


def _normalize_acc(raw):
    return re.sub(r"[^A-Za-z0-9]", "", str(raw or "")).upper()


def _distinct_accounts(result):
    found = result.get("accountNumbersFound") or []
    return sorted({_normalize_acc(a) for a in found if _normalize_acc(a)})


def _verdict(is_consolidated):
    return "MULTI" if is_consolidated else "SINGLE"


def _shorten(text, width):
    return text if len(text) <= width else text[: width - 3] + "..."


def _pil_to_data_uri(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("utf-8")


def load_pages_text(pdf_path, max_pages):
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for idx, p in enumerate(pdf.pages, start=1):
            if max_pages and idx > max_pages:
                break
            pages.append((idx, p.extract_text(layout=True) or ""))
    return pages


def build_user_content(pdf_path, max_pages, use_vision):
    """Returns (content, description) for the Responses API user message."""
    if use_vision:
        with pdfplumber.open(pdf_path) as pdf:
            total = len(pdf.pages)
        last = min(max_pages, total) if max_pages else total
        images = convert_from_path(pdf_path, dpi=IMAGE_DPI, first_page=1, last_page=last)
        content = [{"type": "input_text", "text": f"Statement pages 1-{last} of {total}:"}]
        content += [
            {"type": "input_image", "image_url": _pil_to_data_uri(img), "detail": IMAGE_DETAIL}
            for img in images
        ]
        return content, f"vision, pages 1-{last} of {total} @ {IMAGE_DPI} DPI"

    pages = load_pages_text(pdf_path, max_pages)
    text = "\n\n".join(f"=== PAGE {n} ===\n{t}" for n, t in pages)
    if len(text.strip()) < 200:
        print("WARNING: very little text extracted. This may be a scanned PDF; try --vision.\n")
    content = [{"type": "input_text", "text": f"Statement text:\n\n{text}"}]
    return content, f"text, {len(pages)} page(s)"


# ============================================================================
# DETECTION
# ============================================================================

def run_detection(client, prompt_text, user_content):
    resp = client.responses.create(
        model=MODEL,
        reasoning=REASONING,
        instructions=INSTRUCTIONS_HEADER + prompt_text + DEBUG_SUFFIX,
        input=[{"role": "user", "content": user_content}],
        text={"format": TOPOLOGY_SCHEMA},
    )
    return json.loads(resp.output_text)


def print_result(name, result):
    found = result.get("accountNumbersFound") or []
    distinct = _distinct_accounts(result)
    llm_says_multi = bool(result.get("isConsolidated"))
    code_says_multi = len(distinct) > 1

    print("-" * 78)
    print(f"PROMPT: {name.upper()}")
    print("-" * 78)
    print(f"  LLM verdict:            {'MULTI-ACCOUNT (consolidated)' if llm_says_multi else 'SINGLE ACCOUNT'}")
    print(f"  isConsolidated:         {result.get('isConsolidated')}")
    print(f"  Account numbers found:  {found}")
    print(f"  Distinct (normalized):  {distinct}  ({len(distinct)})")
    print(f"  Distinct-count verdict: {'MULTI-ACCOUNT' if code_says_multi else 'SINGLE ACCOUNT'}")
    print(f"  Evidence:               {result.get('evidence')}")
    print(f"  Beginning balance:      {_money(result.get('beginningBalance'))}")
    print(f"  Total deposits:         {_money(result.get('totalDeposits'))}")
    print(f"  Total withdrawals:      {_money(result.get('totalWithdrawals'))}")
    print(f"  Ending balance:         {_money(result.get('endingBalance'))}")
    if llm_says_multi != code_says_multi:
        print("  >> MISMATCH: the LLM flag disagrees with the distinct account-number count.")
    print()


def process_file(client, pdf_path, prompt_names, args):
    """Runs the selected prompts on one PDF. Returns a result dict for the summary."""
    user_content, desc = build_user_content(pdf_path, args.max_pages, args.vision)

    if not args.summary_only:
        print("=" * 78)
        print(f"File:  {os.path.basename(pdf_path)}")
        print(f"Model: {MODEL} | Input: {desc}")
        print("=" * 78)

    entry = {"path": pdf_path, "input": desc, "results": {}}
    for name in prompt_names:
        entry["results"][name] = run_detection(client, PROMPTS[name], user_content)
        if not args.summary_only:
            print_result(name, entry["results"][name])
    return entry


# ============================================================================
# SUMMARY TABLE
# ============================================================================

def print_summary(entries, prompt_names):
    name_w = 46
    cols = [("FILE", name_w)] + [(n.upper(), 8) for n in prompt_names] + [("ACCTS", 6), ("FLAGS", 30)]
    header = " ".join(f"{label:<{width}}" for label, width in cols)

    print("=" * len(header))
    print("SUMMARY (SINGLE vs MULTI per prompt)")
    print("=" * len(header))
    print(header)
    print("-" * len(header))

    for e in entries:
        label = _shorten(os.path.basename(e["path"]), name_w)
        if "error" in e:
            row = [f"{label:<{name_w}}"] + [f"{'-':<8}" for _ in prompt_names]
            row += [f"{'-':<6}", f"{'ERROR: ' + _shorten(e['error'], 22):<30}"]
            print(" ".join(row))
            continue

        flags = []
        verdicts = []
        for n in prompt_names:
            r = e["results"][n]
            verdicts.append(bool(r.get("isConsolidated")))
            if bool(r.get("isConsolidated")) != (len(_distinct_accounts(r)) > 1):
                flags.append(f"{n}: flag vs count mismatch")
        if len(set(verdicts)) > 1:
            flags.append("prompts disagree")

        # Distinct account count from the last prompt run (revised when running both).
        last = e["results"][prompt_names[-1]]
        n_accts = len(_distinct_accounts(last))

        row = [f"{label:<{name_w}}"]
        row += [f"{_verdict(bool(e['results'][n].get('isConsolidated'))):<8}" for n in prompt_names]
        row += [f"{n_accts:<6}", f"{'; '.join(flags) if flags else '-':<30}"]
        print(" ".join(row))

    print("=" * len(header))


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Detect single vs. multi-account bank statements.")
    parser.add_argument("pdfs", nargs="*", help="Statement PDF paths (default: DEFAULT_PDFS in this file)")
    parser.add_argument("--prompt", choices=["current", "revised", "both"], default="both",
                        help="Which topology prompt to test (default: both)")
    parser.add_argument("--vision", action="store_true", help="Send page images instead of extracted text")
    parser.add_argument("--max-pages", type=int, default=0, help="Only send the first N pages (0 = all)")
    parser.add_argument("--summary-only", action="store_true", help="Skip per-file details, print only the summary table")
    parser.add_argument("--json", metavar="FILE", help="Save all raw results to this JSON file")
    args = parser.parse_args()

    if not os.getenv("OPENAI_API_KEY"):
        print("Error: OPENAI_API_KEY is not set.")
        sys.exit(1)

    pdf_paths = args.pdfs or DEFAULT_PDFS
    prompt_names = ["current", "revised"] if args.prompt == "both" else [args.prompt]

    print("=" * 78)
    print("STATEMENT TOPOLOGY DETECTOR")
    print(f"Files: {len(pdf_paths)} | Prompts: {', '.join(prompt_names)} | Model: {MODEL}")
    print("=" * 78)

    client = OpenAI()
    entries = []
    for path in pdf_paths:
        if not os.path.exists(path):
            print(f"\n[SKIP] Not found: {path}")
            entries.append({"path": path, "error": "file not found"})
            continue
        try:
            entries.append(process_file(client, path, prompt_names, args))
        except Exception as exc:  # keep going so one bad file doesn't stop the batch
            print(f"\n[ERROR] {os.path.basename(path)}: {exc}")
            entries.append({"path": path, "error": str(exc)})

    print()
    print_summary(entries, prompt_names)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)
        print(f"\nSaved raw results: {args.json}")


if __name__ == "__main__":
    main()