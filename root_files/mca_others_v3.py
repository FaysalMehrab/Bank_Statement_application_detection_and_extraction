#!/usr/bin/env python3
"""
Comprehensive Financial Position Extractor — Full Production Suite
Model: GPT-5.6 Terra (reasoning: none)
Supported Categories: MCA, MCA Payment Returns, Returned Items (NSF), 
                     Credit Cards, Business Loans, and BNPL
"""

import json
import os
import sys
from datetime import datetime
from collections import defaultdict

from openai import OpenAI
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# LLM configuration
# ---------------------------------------------------------------------------
LLM_MODEL = "gpt-5.6-terra"
INPUT_TOKEN_PRICE = 2.00   # $ per 1M input tokens
OUTPUT_TOKEN_PRICE = 12.00  # $ per 1M output tokens

# ---------------------------------------------------------------------------
# System Prompt (Complete Underwriting Suite + Returned Items Invariant)
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are a senior Merchant Cash Advance (MCA), Revenue-Based Financing (RBF), and Small Business Lending underwriter specializing in bank statement analysis, transaction categorization, and financing detection.
    Your task is to analyze the ENTIRE bank statement as a single dataset and identify every active or newly funded Merchant Cash Advance, Revenue-Based Financing, Working Capital Loan, Factoring agreement, Business Loan, Business Line Draw, or similar financing position.
    Never classify transactions independently. Build financing relationships by linking funding deposits and repayment transactions.

    ### Transaction Category Detection Rules:
    1. FOR CREDITS (Inflows):
    - If description contains 'transfer' AND any of [intuit, stripe, cashapp, paypal, venmo], return 'Revenue'.
    - Else if description contains 'transfer', return 'Transfer In'.
    - Else if description contains 'zelle', return 'Zelle In'.
    - Else if description contains 'ach', return 'ACH In'.
    - Else if description contains 'wire', return 'Wire In'.
    - DEFAULT: return 'Revenue'.

    2. FOR DEBITS (Outflows):
    - If description contains 'transfer', return 'Transfer Out'.
    - Else if description contains 'zelle', return 'Zelle Out'.
    - Else if description contains 'ach', return 'ACH Out'.
    - Else if description contains 'wire', return 'Wire Out'.
    - DEFAULT: return 'Expense'.

    3. FOR RETURNED / UNPAID ITEMS:
    - If transaction type in input is 'returned_item', OR description contains '[RETURNED UNPAID]', 'RETURNED UNPAID', or indicates an uncollected/bounced check or deposit from an unpaid schedule:
      * ALWAYS return trCategory = 'Returned Item'.
      * ALWAYS set isReturnedItem = true.
      * ALWAYS set isMca = false, isCreditCard = false, isBusinessLoan = false, isBnpl = false.
      * Set lenderName = null, creditorName = null, loanLenderName = null, bnplProvider = null.
      * Do NOT classify as 'Expense' (it was not a cleared outgoing expenditure, do NOT deduct it).
      * Do NOT classify as 'Revenue' (it bounced and never settled).
      * It MUST NOT be deducted or subtracted from the merchant's business cash flow or expenses.

    ========================
    MCA DETECTION
    ========================
    Use a multi-pass review.

    PASS 1 — Funding:
    Review incoming credits as potential financing proceeds (ACH credits, wires, RTP, transfers, instant payments, deposits).
    Do not require keywords such as FUNDING, CAPITAL, LOAN, or ADVANCE if an identifiable financing entity or company name is present.
    CRITICAL: A credit can ONLY be marked as MCA Funding (isMca=true) if the transaction description contains an identifiable lender/funder name, financing brand, or loan/advance reference.
    Generic unbranded credits (e.g., "Wire Transfer Credit", "Online Transfer", "Deposit", "Counter Deposit") containing NO lender or company name MUST be treated as regular business inflows (trCategory = "Wire In" or "Transfer In", isMca = false, lenderName = null). NEVER classify an unbranded generic wire or transfer as MCA funding.

    PASS 2 — Repayments:
    Review every outgoing transaction as a potential repayment.
    Repayments may occur via ACH, CCD, PPD, WEB, debit card, recurring card payment, processor withholding, wire, check, internal transfer, or any business payment method.
    Do not require recurring payments. A financing position may contain only one repayment.

    PASS 3 — Link Transactions:
    Connect funding and repayments using:
    - normalized lender names
    - descriptor similarity
    - ACH Company IDs
    - recurring identifiers
    - financing terminology
    Descriptor similarity and matching lender identity are strictly required.
    CRITICAL: NEVER link a generic unbranded credit (such as "Wire Transfer Credit") to an outgoing MCA repayment simply because dates are nearby. If an MCA lender (e.g., "Stage Advance") only has repayments on the statement and no branded funding deposit appears with that lender's name, output ONLY the repayment(s) for that lender. Do not attach unbranded wires as funding.

    PASS 4 — Recover Missed Positions:
    Perform a second full review specifically searching for:
    - single funding deposits
    - single repayments
    - lenders appearing only once
    - funding near statement end
    - repayments near statement beginning
    - descriptor variations
    - card, wire, check, or processor repayments
    - unmatched business credits or debits that reasonably represent financing
    Never require both funding and repayment to exist within the statement. Financing relationships may span multiple statements.

    Evidence may consist of:
    - one funding only
    - one repayment only
    - one funding + one repayment
    - daily, weekly, monthly, or irregular repayments
    When sufficient evidence exists, classify the transaction as MCA even if only one related transaction appears.

    ========================
    LENDER NORMALIZATION
    ========================
    Normalize lender names by removing IDs, trace numbers, LLC, INC, CORP, COMPANY, GROUP, DES:, WEB, CCD, PPD, and extra spaces while preserving the lender identity.
    Treat abbreviated or similar descriptors as the same lender whenever supported by evidence.
    Examples:
        ExpansionCap → Expansion Capital
        BeckhamCapitl → Beckham Capital
        Banana Funding G → Banana Funding Group
        Pivot Funding Group LLC, Pivot Fund, Pivot Fund-Coll → Pivot Funding Group
        Giggle Finance, GiggleFinance → Giggle Finance
    Never require exact descriptor matches.

    ========================
    MCA PAYMENT RETURN DETECTION
    ========================
    Apply this check BEFORE using any transaction as PASS 1 funding evidence or PASS 2 repayment evidence.

    A debit and a nearby credit represent a RETURNED/REVERSED financing attempt (not real funding, and not a completed repayment) when:
    - Both share the same or a matching normalized lender/company descriptor, AND
    - The credit amount matches the debit amount exactly, or within a small tolerance (e.g. a few dollars, to allow for a return or NSF fee netted into the reversal), AND
    - The credit posts on the same date as the debit, or within the following 1-3 business days.

    Treat any of the following as strong confirming evidence of a return, even without an exact amount/date match:
    'ACH RETURN', 'ACH RETURN CHARGE', 'RETURN CHARGE', 'RETURN FEE', 'UNAUTHORIZED RETURN', 'UNABLE TO PROCESS', 'INSUFFICIENT FUNDS', 'NSF', 'REVERSAL', 'REVERSED', 'RECALL', 'CHARGEBACK', or a standard ACH return reason code (R01, R02, R03, R04, R09, R10, R29, etc.) appearing on the credit line, an adjacent fee line, or an adjacent line item tied to the same descriptor.

    When a debit/credit pair matches this pattern:
    - Do not count the debit as PASS 2 repayment evidence, and do not count the credit as PASS 1 funding evidence, for that lender.
    - Classify BOTH the original debit and the reversing credit as trCategory = "MCA Payment Return", with isMca = false, and isReturnedItem = false.
    - A near-zero-amount fee line explicitly labeled as a return charge (e.g. "ACH Return Charge .00") tied to the same lender descriptor should also be classified as trCategory = "MCA Payment Return", isMca = false, and isReturnedItem = false.
    - If this reversal pair is the ONLY evidence found for that lender anywhere in the statement, do not create a financing position for that lender from it alone — a failed attempt is not financing evidence.
    - If the same lender has OTHER debits or credits elsewhere in the statement that are not part of a return pair, those continue to be evaluated normally under PASS 1-4 and LENDER NORMALIZATION above, and can still establish or support a financing position for that lender.
    - A transaction classified as MCA Payment Return or Returned Item must never be evaluated under CREDIT CARD PAYMENT DETECTION, BUSINESS LOAN DETECTION, or BNPL DETECTION below.

    ========================
    CREDIT CARD PAYMENT DETECTION
    ========================
    Only evaluate a transaction for Credit Card classification after confirming it does not qualify as MCA, MCA Payment Return, or Returned Item.

    Look for outgoing bank withdrawals/debits that pay down a credit or charge card balance. Do not count normal merchant debit/credit card purchases made at a store.

    Classify a transaction as a credit card payment when the description contains:
    - 'CRCARDPMT'
    - 'CARD PAYMENT' (likely credit/charge card)
    - 'CC REPAY' / 'CC REPAYME'
    - 'EPAYMENT ACH PMT'
    - 'AUTOPAY' with a known card issuer name (e.g., Amex, Chase, Citi, Discover, Capital One, Barclays)

    Known card issuers, servicers, and private-label card processors — also classify a payment as a credit card payment when one of these names appears together with any payment-style wording ('PAY', 'PYMT', 'PMT', 'PAYMENT'), even without one of the exact keywords above:
    Amex, Chase, Citi, Discover, Capital One, Barclays, Comenity (Comenity Bank / Comenity Pay), Credit First NA / CFNA, Synchrony (Synchrony Bank / CareCredit), Bread Financial, First National Bank of Omaha, GE Capital Retail Bank.

    Broader behavioral matching: Only apply indicators like 'DIRECTPAY', 'DIRECT PAY', 'MOBILE PMT', 'ONLINE PMT', 'ONLINE PAYMENT', 'WEB PAYMENT', or 'WEB PMT' when they appear alongside one of the known issuer/servicer names above — never on their own, since these words also commonly appear on utility, insurance, and vendor bill payments.

    A card payment can be to a personal or store-branded card even when paid from a business account and the description shows an individual's name (e.g., 'Ind Name:') as the recipient — classify it as a credit card payment regardless of whether the named recipient is a person or a business.

    Exclude: Debit card purchases (containing 'VISA DDA PUR', 'DBCRD PUR'), ATM cash withdrawals, internal transfers, business/overdraft fees, and subscriptions.

    ========================
    BUSINESS LOAN DETECTION (separate from MCA)
    ========================
    Only evaluate a transaction for Business Loan classification after confirming it does not qualify as MCA, MCA Payment Return, or Returned Item.

    Identify traditional business term loans or line of credit transactions. Keep these strictly separate from MCA candidate transactions.

    Do not rely strictly on hardcoded lists; evaluate transaction behaviors dynamically. A business loan has two states:
    - Funding coming in: Large incoming deposit from a lender (classify as 'business_loan_funding_candidate').
    - Payments going out: Monthly or semi-monthly fixed-amount ACH/wire/check repayments to the lender (classify as 'business_loan_repayment').

    Dictionary of known business lenders: 'TD Bank', 'Chase', 'Wells Fargo', 'Bank of America', 'Capital One Business', 'OnDeck', 'Funding Circle', 'Kabbage', 'BlueVine', 'Lendio', 'SmartBiz', 'SBFS', 'Small Business Financial Solutions', 'Basepoint'.

    If a transaction matches a lender-dictionary name above but contains none of the loan keywords below, still classify it as a business loan — do not discard it for lack of keyword evidence. A lender-dictionary match is sufficient on its own.

    Business loan keywords: 'BUSINESS LOAN', 'SMALL BUSINESS LOAN', 'TERM LOAN', 'SBA', 'SBA LOAN', 'EQUIPMENT FINANCE', 'EQUIPMENT LOAN', 'LOAN PAYMENT', 'LOAN PMT', 'LENDING', 'FINANCIAL SOLUTIONS', 'BANK LOAN', 'COMMERCIAL LOAN', 'LINE OF CREDIT', 'LOC PAYMENT'.

    Link outbound repayments back to a previous loan funding deposit from the same or related lender if both exist on the statement, even when the names do not match exactly.

    A loan payment can be made to a personal name (e.g., 'Ind Name:') even when paid from a business account — classify it as a business loan if the payer/lender company name matches the dictionary, regardless of the named recipient.

    ========================
    BNPL DETECTION
    ========================
    Only evaluate a transaction for BNPL classification after confirming it is not already classified as MCA, MCA Payment Return, Returned Item, Credit Card, or Business Loan under the sections above.

    Identify short-term installment payments representing Buy Now, Pay Later financing products. Keep them separate from credit cards and regular merchant debit card purchases.

    Apply only to outgoing transactions (amounts < 0 / debits).

    Target keywords:
    - High-confidence: 'AFFIRM', 'AFFIRM.COM', 'AFFIRM.COM PAYMENTS', 'AFFIRM * PAY', 'WWW.AFFIRM.COM', 'KLARNA', 'AFTERPAY', 'SEZZLE', 'ZIP', 'QUADPAY', 'PAYPAL PAYIN4', 'PYPL PAYIN4', 'PAY IN 4', 'PAYPAL *PYPL PAYIN4'.
    - Medium-confidence: 'SPLIT PAY', 'SP+AFF', 'INSTALLMENT PAYMENT', 'PAY LATER'.

    Provider name normalization — always collapse variant keywords down to ONE consistent bnplProvider name per brand.

    Exclude: Standard merchant debit card purchases, internal checking transfers, software subscriptions, and merchant processing incoming deposits.

    ========================
    FINAL REVIEW
    ========================
    Before producing the output:
    - review every credit for funding
    - review every debit for repayment
    - re-check every debit/credit pair flagged as MCA against the MCA PAYMENT RETURN DETECTION rules above before finalizing isMca
    - ensure Returned Items have isReturnedItem=true, are marked as 'Returned Item', and are NOT deducted from cash flow
    - ensure all regular cleared transactions have isReturnedItem=false
    - re-evaluate unmatched transactions
    - ensure every financing position has been identified
    Prefer identifying a financing relationship over leaving a supported transaction unmatched.

    ========================
    OUTPUT
    ========================
    For every transaction return:
    - trID
    - postingDate
    - trCategory
    - amount
    - isMca
    - lenderName
    - isCreditCard
    - creditorName
    - isBusinessLoan
    - loanLenderName
    - isBnpl
    - bnplProvider
    - isReturnedItem

    Flag Rules:
    - Set isMca=true for every transaction belonging to a financing position, including both funding deposits and repayments.
    - Set isCreditCard=true only when Credit Card Payment Detection rules are met, and provide creditorName.
    - Set isBusinessLoan=true only when Business Loan Detection rules are met, and provide loanLenderName.
    - Set isBnpl=true only when BNPL Detection rules are met, and provide bnplProvider.
    - Set isReturnedItem=true strictly for uncollected, bounced, or returned unpaid items (trCategory="Returned Item"). For all normal cleared transactions, set isReturnedItem=false.
    - A returned/reversed financing attempt must be output with trCategory="MCA Payment Return", isMca=false, isReturnedItem=false.
    Analyze the entire statement before making any decision."""

# ---------------------------------------------------------------------------
# JSON schema for structured output — flat per-transaction format
# ---------------------------------------------------------------------------
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "analyzed_transactions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "trID": {
                        "type": "string",
                        "description": "Transaction ID from input"
                    },
                    "trCategory": {
                        "type": "string",
                        "description": "Classified transaction category based on description and amount"
                    },
                    "isMca": {
                        "type": "boolean",
                        "description": "True if transaction is identified as MCA payment, else false"
                    },
                    "lenderName": {
                        "type": ["string", "null"],
                        "description": "Normalized lender name if identified as MCA, else null"
                    },
                    "isCreditCard": {
                        "type": "boolean",
                        "description": "True if transaction is identified as credit card payment, else false"
                    },
                    "creditorName": {
                        "type": ["string", "null"],
                        "description": "Normalized creditor name if identified as credit card payment, else null"
                    },
                    "isBusinessLoan": {
                        "type": "boolean",
                        "description": "True if transaction is identified as business loan, else false"
                    },
                    "loanLenderName": {
                        "type": ["string", "null"],
                        "description": "Normalized lender name if identified as business loan, else null"
                    },
                    "isBnpl": {
                        "type": "boolean",
                        "description": "True if transaction is identified as BNPL payment, else false"
                    },
                    "bnplProvider": {
                        "type": ["string", "null"],
                        "description": "Normalized BNPL provider name if identified as BNPL, else null"
                    },
                    "isReturnedItem": {
                        "type": "boolean",
                        "description": "True strictly for uncollected, bounced, or returned unpaid items, else false"
                    }
                },
                "required": [
                    "trID", 
                    "trCategory", 
                    "isMca", 
                    "lenderName", 
                    "isCreditCard", 
                    "creditorName", 
                    "isBusinessLoan", 
                    "loanLenderName", 
                    "isBnpl", 
                    "bnplProvider",
                    "isReturnedItem"
                ],
                "additionalProperties": False
            }
        }
    },
    "required": ["analyzed_transactions"],
    "additionalProperties": False
}


def load_extraction(path):
    """Load the extraction JSON and return the full data dict."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_user_message(data):
    """
    Build the user message containing statement metadata and every transaction.
    Transactions are formatted as a compact CSV table to minimize tokens.
    """
    meta = (
        f"BANK STATEMENT METADATA:\n"
        f"  Bank:            {data.get('bankName', 'N/A')}\n"
        f"  Account holder:  {data.get('accountHolder', 'N/A')}\n"
        f"  Account #:       {data.get('accountNumber', 'N/A')}\n"
        f"  Statement period: {data.get('startdate', '?')} to {data.get('enddate', '?')}\n"
        f"  Beginning balance: ${data.get('beginningBalance', 0):,.2f}\n"
        f"  Ending balance:    ${data.get('endingBalance', 0):,.2f}\n"
        f"  Total deposits:    ${data.get('totalDeposits', 0):,.2f}\n"
        f"  Total withdrawals: ${data.get('totalWithdrawals', 0):,.2f}\n"
        f"  # credits: {data.get('numberOfCreditTransactions', 0)}   "
        f"# debits: {data.get('numberOfDebitsTransactions', 0)}\n"
    )

    txns = data.get("transactions", [])
    lines = ["trID,postingDate,type,amount,description"]
    for tx in txns:
        lines.append(
            f"{tx['trID']},{tx['postingDate']},{tx['type']},{tx['amount']},"
            f"{tx['description']}"
        )
    txn_table = "\n".join(lines)

    return (
        f"{meta}\n"
        f"COMPLETE TRANSACTION LIST ({len(txns)} transactions):\n"
        f"{txn_table}\n\n"
        "Using the system prompt rules, perform the matching passes and return an "
        "analyzed_transactions array covering every transaction in the statement. "
        "Set isMca=true if it is an MCA payment/deposit (otherwise false), "
        "isCreditCard=true if it is an outgoing credit card repayment (otherwise false), "
        "isBusinessLoan=true if it is business loan funding or repayment (otherwise false), "
        "isBnpl=true if it is a BNPL installment payment (otherwise false), "
        "and isReturnedItem=true strictly for uncollected or bounced items (otherwise false). "
        "Provide corresponding normalized names or null for empty fields. "
        "Always set trCategory to the classified category."
    )


def _print_request_cost(response):
    usage = response.usage

    input_tokens = usage.input_tokens
    output_tokens = usage.output_tokens

    input_cost = input_tokens / 1_000_000 * INPUT_TOKEN_PRICE
    output_cost = output_tokens / 1_000_000 * OUTPUT_TOKEN_PRICE
    total_cost = input_cost + output_cost

    print(f"Input tokens:       {input_tokens:,}")
    print(f"Output tokens:      {output_tokens:,}")
    print(f"GPT-5.6 Terra cost: ${total_cost:.6f}")


def call_llm(client, user_message):
    print("\n" + "=" * 60)
    print("Calling LLM for financial position extraction ...")
    print("=" * 60)

    try:
        response = client.responses.create(
            model=LLM_MODEL,
            instructions=SYSTEM_PROMPT,
            input=user_message,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "transaction_analysis",
                    "strict": True,
                    "schema": OUTPUT_SCHEMA,
                }
            },
        )

        _print_request_cost(response)
        result = json.loads(response.output_text)
        return result

    except json.JSONDecodeError as e:
        print(f"Error: could not parse LLM JSON response — {e}")
        return None
    except Exception as e:
        print(f"Error during LLM call — {e}")
        return None


def _format_date(raw_date):
    if not raw_date:
        return "?"
    try:
        dt = datetime.strptime(raw_date, "%Y-%m-%d")
        return f"{dt.month}/{dt.day}/{dt.year}"
    except ValueError:
        return raw_date


def _format_amount(raw_amount):
    try:
        return f"${float(raw_amount):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def _print_group_section(title, grouped_txns, original_by_id, flag_label):
    divider = "─" * 60
    print(f"\n{divider}\n  {title} ({len(grouped_txns)} detected):\n{divider}")

    for company_name, txns in sorted(grouped_txns.items()):
        print(f"  • {company_name} ({len(txns)} transaction(s)):")

        company_total = 0.0
        for tx in txns:
            original = original_by_id.get(tx["trID"], {})
            description = (original.get("description") or "").strip()
            date_str    = _format_date(original.get("postingDate"))
            amount      = original.get("amount", 0) or 0
            company_total += float(amount)

            print(
                f"    {tx['trID']}  |  {tx.get('trCategory', '')}  |  {flag_label}"
                f"  |  {description}  |  {date_str}  |  {_format_amount(amount)}"
            )

        print(f"\n    Total: {_format_amount(company_total)}")
        print(divider)


def print_report(result, data):
    analyzed = result.get("analyzed_transactions", [])
    original_by_id = {tx["trID"]: tx for tx in data.get("transactions", [])}

    mca_txns      = [tx for tx in analyzed if tx.get("isMca")]
    cc_txns       = [tx for tx in analyzed if tx.get("isCreditCard")]
    loan_txns     = [tx for tx in analyzed if tx.get("isBusinessLoan")]
    bnpl_txns     = [tx for tx in analyzed if tx.get("isBnpl")]
    returned_txns = [tx for tx in analyzed if tx.get("isReturnedItem")]

    by_lender = defaultdict(list)
    for tx in mca_txns:
        by_lender[tx.get("lenderName") or "Unknown"].append(tx)

    by_creditor = defaultdict(list)
    for tx in cc_txns:
        by_creditor[tx.get("creditorName") or "Unknown"].append(tx)

    by_loan_lender = defaultdict(list)
    for tx in loan_txns:
        by_loan_lender[tx.get("loanLenderName") or "Unknown"].append(tx)

    by_provider = defaultdict(list)
    for tx in bnpl_txns:
        by_provider[tx.get("bnplProvider") or "Unknown"].append(tx)

    print("\n" + "=" * 70)
    print("FINANCIAL EXTRACTION REPORT")
    print("=" * 70)
    print(f"  Total transactions analyzed: {len(analyzed)}")
    print(f"  MCA transactions:            {len(mca_txns)}")
    print(f"  Unique MCA lenders:          {len(by_lender)}")
    print(f"  Credit Card repayments:      {len(cc_txns)}")
    print(f"  Unique CC creditors:         {len(by_creditor)}")
    print(f"  Business Loan transactions:  {len(loan_txns)}")
    print(f"  Unique Loan lenders:         {len(by_loan_lender)}")
    print(f"  BNPL transactions:           {len(bnpl_txns)}")
    print(f"  Unique BNPL providers:       {len(by_provider)}")
    print(f"  Returned / Unpaid items:     {len(returned_txns)}")

    if mca_txns:
        _print_group_section("MCA LENDERS", by_lender, original_by_id, "isMca=True")

    if cc_txns:
        _print_group_section("CREDIT CARD DEBT PAYMENTS", by_creditor, original_by_id, "isCreditCard=True")

    if loan_txns:
        _print_group_section("BUSINESS TERM LOANS", by_loan_lender, original_by_id, "isBusinessLoan=True")

    if bnpl_txns:
        _print_group_section("BUY NOW, PAY LATER FINANCING", by_provider, original_by_id, "isBnpl=True")

    if returned_txns:
        divider = "─" * 60
        print(f"\n{divider}\n  RETURNED / UNPAID ITEMS (NSF) ({len(returned_txns)} detected):\n{divider}")
        for tx in returned_txns:
            original = original_by_id.get(tx["trID"], {})
            description = (original.get("description") or "").strip()
            date_str    = _format_date(original.get("postingDate"))
            amount      = original.get("amount", 0) or 0
            print(
                f"    {tx['trID']}  |  {tx.get('trCategory', '')}  |  isReturnedItem=True"
                f"  |  {description}  |  {date_str}  |  {_format_amount(amount)}"
            )
        print(divider)

    print("\n" + "=" * 70)


def build_output(data, result):
    return {
        "bankName":              data.get("bankName"),
        "accountHolder":         data.get("accountHolder"),
        "accountNumber":         data.get("accountNumber"),
        "statementPeriod": {
            "start": data.get("startdate"),
            "end":   data.get("enddate"),
        },
        "totalTransactions":     len(data.get("transactions", [])),
        "extractedAt":           datetime.now().isoformat(),
        "model":                 LLM_MODEL,
        "analyzed_transactions": result.get("analyzed_transactions", []),
    }


def main():
    load_dotenv()

    if len(sys.argv) >= 2:
        input_path = sys.argv[1]
    else:
        input_path = (
           "statements/Universal_check_for_validation/output/gpt_5_6_terra/NorthEast Community Bank_x5409_07-01-2026_to_07-31-2026_gpt_5_6_terra_v3_20261013_162524.json"
        )

    if not os.path.exists(input_path):
        print(f"Error: file '{input_path}' not found.")
        sys.exit(1)

    if not os.getenv("OPENAI_API_KEY"):
        print("Error: OPENAI_API_KEY not set.")
        sys.exit(1)

    print(f"Loading: {input_path}")
    data = load_extraction(input_path)
    txns = data.get("transactions", [])
    if not txns:
        print("No transactions found.")
        sys.exit(1)
    print(f"  {len(txns)} transactions loaded "
          f"({data.get('startdate', '?')} → {data.get('enddate', '?')})")

    user_message = build_user_message(data)
    client = OpenAI()
    result = call_llm(client, user_message)

    if not result:
        print("\n❌ LLM extraction failed.")
        sys.exit(1)

    print_report(result, data)

    if len(sys.argv) >= 3:
        output_path = sys.argv[2]
    else:
        os.makedirs("ot_output", exist_ok=True)
        base = os.path.splitext(os.path.basename(input_path))[0]
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join("New_mca_positions/Output/o", f"{base}_output_{ts}.json")

    output = build_output(data, result)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, default=str, ensure_ascii=False)
    print(f"\nSaved to: {output_path}")

    analyzed = result.get("analyzed_transactions", [])
    mca_count = sum(1 for tx in analyzed if tx.get("isMca"))
    print(f"🎉 Done: {len(analyzed)} transactions analyzed, "
          f"{mca_count} identified as MCA.")


if __name__ == "__main__":
    main()