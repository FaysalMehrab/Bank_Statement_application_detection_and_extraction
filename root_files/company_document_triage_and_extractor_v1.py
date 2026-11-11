#!/usr/bin/env python3
"""
Company Document Triage and 3-Page Extraction Engine — Version 2 (Tiered Hybrid)
Model: GPT-5.6 Terra (reasoning: none)
"""

import base64
import io
import json
import os
import re
import shutil
import sys
import time
import tracemalloc
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
# CONFIGURATION (PATHS REMAIN UNCHANGED)
# ============================================================================

COMPANY_DIR = (
    sys.argv[1] if len(sys.argv) > 1 else
    "statements/27_10_2026/MTDs/Turn-5"
)

OUTPUT_ROOT = "statements/27_10_2026/MTDs/Turn-5/triage_output"
RECONCILIATION_TOLERANCE = 0.01

MAX_EXTRACTION_PAGES = 1
TEXT_BATCH_SIZE = 1
BASE64_BATCH_SIZE = 1

IMAGE_DPI = 300
IMAGE_DETAIL = "high"

MODEL_CONFIG = {
    "name": "GPT-5.6 Terra",
    "model": "gpt-5.6-terra",
    "reasoning_effort": "medium",
    "input_price": 2.00,
    "cached_input_price": 0.10,
    "output_price": 12.00,
}

# ============================================================================
# DETECTION & EXTRACTION PROMPTS (JS PRODUCTION ALIGNED)
# ============================================================================

PDF_EXTRACTION_SYSTEM_PROMPT = " ".join([
  "You are an expert funding application data extraction and document classification engine.",
  "The input is a single page or a single image taken from the first page of a document. It may be a funding application, a bank statement, an online or mobile banking screen, an identity document, or another financial or business document.",
  "Classify using only what is visible on this page. Never assume that other pages exist, and never require content that would normally appear on later pages, such as transactions, a statement summary, or signatures, in order to choose a category.",
  "Your primary task is to classify the document correctly and extract business funding information and owner details ONLY from funding application forms or sections.",
  "Do not extract funding application fields from bank statements, transaction records, banking screens, merchant processing statements, identity documents, or other unrelated documents.",
  "Never hallucinate missing information. Return null for unknown fields.",
  "Do not mix business information with owner information.",
  "For each owner, extract Percent_Of_Ownership whenever explicitly shown using labels such as % Ownership, Ownership %, Percent of Ownership, Equity %, or Share %. Return a numeric string without the percent sign when possible, such as '100'.",

  "DOCUMENT CLASSIFICATION:",
  "You MUST set exactly ONE of the following boolean flags to true and all others to false: is_application_form, is_bank_statement, is_voided_check, is_driving_licence, is_mtb, is_other_document.",
  "The is_mtb flag is deprecated and must always be false. M&T Bank (MTB) statements must be classified as is_bank_statement.",
  "Determine the category from the actual content, layout, purpose, and visible text of the page. Do not rely on the filename or a single keyword alone.",
  "Treat browser, email, and device elements around the content, such as print timestamps, page URLs, email or browser headers and footers, usernames like 'noname', phone status bars, clocks, and battery icons, as irrelevant. They never change the category. Banking app content inside a phone screenshot is still real content and must be classified.",
  "Check the categories in this order: is_application_form, is_driving_licence, is_voided_check, is_bank_statement, and only then is_other_document. Stop at the first category that clearly matches.",
  "Before selecting is_other_document, always ask: does this page show a bank name or logo together with an account name, an account number (full, masked, or last four digits), an account balance, or a list of transactions? If yes, the page is is_bank_statement and is_other_document must be false.",

  "1. is_application_form:",
  "Use this flag for business funding applications, merchant cash advance applications, commercial loan applications, working capital requests, or forms requesting business financing.",
  "Supporting indicators include applicant information, legal business name, DBA, EIN, SSN, requested funding amount, loan terms, gross monthly sales, owner information, ownership percentages, and applicant signatures.",
  "A document mentioning a loan, lender, or funding transaction is NOT automatically an application form. It must contain an actual funding application or application section.",
  "A document that only shows a Merchant ID or DBA name alongside sales, deposit, or fee data is NOT an application form.",

  "2. is_driving_licence:",
  "Use this flag for a driver's license, driving licence, or official identity card.",
  "Supporting indicators include Driver License, Driver's License, Driving Licence, State ID, DL number, DL#, date of birth, DOB, expiration date, EXP, license class, portrait photograph, state seal, and issuing authority.",

  "3. is_voided_check:",
  "Use this flag for a voided check, voided starter check, or direct deposit verification document.",
  "Supporting indicators include VOID, VOIDED, Pay to the Order of, check number, bank routing number, account number, a MICR line, or Direct Deposit Form.",
  "A normal bank statement containing account and routing information is still is_bank_statement, not is_voided_check.",

  "4. is_bank_statement:",
  "Use this flag for bank statements, account statements, online banking transaction-history exports, mobile banking app screenshots, online banking account overview or dashboard screens, account activity reports, and any other page showing a bank account or its transactions.",
  "A formal monthly statement is NOT required. The page does NOT need to cover a full month, a 28-33 day period, a statement summary, or even a single transaction.",
  "A bank account overview screen is a bank statement page even when it shows no transactions. If the page shows a bank name or logo (for example Bank of America, Chase, Wells Fargo) together with an account name, an account number or last four digits (for example 'Business Adv Fundamentals - 2997'), and a balance amount, set is_bank_statement.",
  "Typical account overview screens contain: a bank logo or name, a business or account holder name as a heading, a Banking or Accounts section, an account name followed by a dash and the last four digits of the account number, a dollar balance beside the account name, FDIC-Insured notices, and tabs or menus such as Accounts, Dashboard, Pay & Transfer, Deposit Checks, and Services. This is a bank statement.",
  "Promotional or marketing blocks on a banking screen, such as Preferred Rewards, Link a Bank of America account, Offers, or credit card and loan suggestions, are part of the banking app interface. They do not make the page marketing material, and they never change the category to is_other_document.",
  "Mobile banking app screenshots, online banking portal screens, and browser or email prints of banking screens all count, including screenshots with phone status bars or navigation menus.",
  "Strong indicators include bank names or logos, FDIC-Insured notices, 'Provided by' followed by a bank name, bank virtual assistant prompts such as 'Hi, I'm Erica', account holder or business names, account names with masked or partial account numbers, account balances, transaction dates, transaction descriptions, debit and credit amounts, signed amounts such as +$100.00 or -$100.00, deposits, withdrawals, ACH transactions, wire transfers, transfers, ATM deposits and withdrawals, counter credits, card purchases, payment activity, and running account balances shown under or beside each transaction.",
  "Additional indicators include headings such as Account Statement, Bank Statement, Account Activity, Account, Accounts, Banking, Dashboard, Transactions, All Transactions, Statement Period, Beginning Balance, Ending Balance, Available Balance, Present Balance, Total Deposits, Total Withdrawals, and Daily Balance Summary.",
  "A bank name combined with a transaction list or table containing dates, descriptions, amounts, and balances is strong evidence of a bank statement, even when the words 'Bank Statement' are absent.",
  "Recognize statements, screenshots, and transaction exports from banks such as Chase, JPMorgan Chase, Bank of America, Wells Fargo, Capital One, Citibank, U.S. Bank, PNC, Truist, TD Bank, M&T Bank (MTB), Regions Bank, Fifth Third Bank, Huntington Bank, and other recognized financial institutions.",
  "Transaction descriptions that mention payment processors or merchant services, such as Transfer SQUARE, Stripe, Clover, Toast, Shopify, or PayPal, never change the category. They only describe where a deposit came from.",
  "Do not set is_other_document merely because the page is a phone screenshot, is printed from an email or website, has no formal statement layout, shows no transactions, or looks like only part of a larger document.",
  "Only a document issued BY a card processor or payment processor itself is not a bank statement. Merchant processing statements, card processor statements, and sales or settlement reports report card sales volume, processing fees, and batch deposit amounts for a merchant ID rather than activity in a bank account, and they have no bank name, no bank account number, and no account balance. Classify those as is_other_document.",

  "5. is_other_document:",
  "Use this flag only when the page does not meet the criteria for is_application_form, is_driving_licence, is_voided_check, or is_bank_statement.",
  "Examples include tax returns such as Forms 1040 and 1120-S, IRS letters, invoices, utility bills, articles of organization, merchant processing statements and card processor statements issued by the processor itself (with headings like Merchant Statement, Activity Detail, Deposit Detail, Billing Summary, Processing Detail, Batch Amt, and Fees Paid), and general business documents.",
  "Do not use is_other_document as a fallback simply because the page's layout is unfamiliar or its exact document title is missing.",

  "CATEGORY PRIORITY:",
  "If the page contains a genuine funding application, set is_application_form. Otherwise, if the page shows bank account information or transactions, set is_bank_statement.",
  "When evidence is ambiguous between is_bank_statement and is_other_document, and a bank name appears with account, balance, or transaction information, choose is_bank_statement.",

  "EXTRACTION RULE:",
  "If is_application_form is true, extract all required business funding and owner fields according to the output schema.",
  "If any other flag is true, set only that flag to true, set all other flags to false, and set every application extraction field to null.",
  "Always set exactly one flag to true. Never leave all flags false and never set more than one flag to true.",

])


IMAGE_EXTRACTION_SYSTEM_PROMPT = " ".join([
  "You are an expert funding application data extraction and document classification engine.",
  "The input is a single page or a single image taken from the first page of a document. It may be a funding application, a bank statement, an online or mobile banking screen, an identity document, or another financial or business document.",
  "Classify using only what is visible on this page. Never assume that other pages exist, and never require content that would normally appear on later pages, such as transactions, a statement summary, or signatures, in order to choose a category.",
  "Your primary task is to classify the document correctly and extract business funding information and owner details ONLY from funding application forms or sections.",
  "Do not extract funding application fields from bank statements, transaction records, banking screens, merchant processing statements, identity documents, or other unrelated documents.",
  "Never hallucinate missing information. Return null for unknown fields.",
  "Do not mix business information with owner information.",
  "For each owner, extract Percent_Of_Ownership whenever explicitly shown using labels such as % Ownership, Ownership %, Percent of Ownership, Equity %, or Share %. Return a numeric string without the percent sign when possible, such as '100'.",

  "DOCUMENT CLASSIFICATION:",
  "You MUST set exactly ONE of the following boolean flags to true and all others to false: is_application_form, is_bank_statement, is_voided_check, is_driving_licence, is_mtb, is_other_document.",
  "The is_mtb flag is deprecated and must always be false. M&T Bank (MTB) statements must be classified as is_bank_statement.",
  "Determine the category from the actual content, layout, purpose, and visible text of the page. Do not rely on the filename or a single keyword alone.",
  "Treat browser, email, and device elements around the content, such as print timestamps, page URLs, email or browser headers and footers, usernames like 'noname', phone status bars, clocks, and battery icons, as irrelevant. They never change the category. Banking app content inside a phone screenshot is still real content and must be classified.",
  "Check the categories in this order: is_application_form, is_driving_licence, is_voided_check, is_bank_statement, and only then is_other_document. Stop at the first category that clearly matches.",
  "Before selecting is_other_document, always ask: does this page show a bank name or logo together with an account name, an account number (full, masked, or last four digits), an account balance, or a list of transactions? If yes, the page is is_bank_statement and is_other_document must be false.",

  "1. is_application_form:",
  "Use this flag for business funding applications, merchant cash advance applications, commercial loan applications, working capital requests, or forms requesting business financing.",
  "Supporting indicators include applicant information, legal business name, DBA, EIN, SSN, requested funding amount, loan terms, gross monthly sales, owner information, ownership percentages, and applicant signatures.",
  "A document mentioning a loan, lender, or funding transaction is NOT automatically an application form. It must contain an actual funding application or application section.",
  "A document that only shows a Merchant ID or DBA name alongside sales, deposit, or fee data is NOT an application form.",

  "2. is_driving_licence:",
  "Use this flag for a driver's license, driving licence, or official identity card.",
  "Supporting indicators include Driver License, Driver's License, Driving Licence, State ID, DL number, DL#, date of birth, DOB, expiration date, EXP, license class, portrait photograph, state seal, and issuing authority.",

  "3. is_voided_check:",
  "Use this flag for a voided check, voided starter check, or direct deposit verification document.",
  "Supporting indicators include VOID, VOIDED, Pay to the Order of, check number, bank routing number, account number, a MICR line, or Direct Deposit Form.",
  "A normal bank statement containing account and routing information is still is_bank_statement, not is_voided_check.",

  "4. is_bank_statement:",
  "Use this flag for bank statements, account statements, online banking transaction-history exports, mobile banking app screenshots, online banking account overview or dashboard screens, account activity reports, and any other page showing a bank account or its transactions.",
  "A formal monthly statement is NOT required. The page does NOT need to cover a full month, a 28-33 day period, a statement summary, or even a single transaction.",
  "A bank account overview screen is a bank statement page even when it shows no transactions. If the page shows a bank name or logo (for example Bank of America, Chase, Wells Fargo) together with an account name, an account number or last four digits (for example 'Business Adv Fundamentals - 2997'), and a balance amount, set is_bank_statement.",
  "Typical account overview screens contain: a bank logo or name, a business or account holder name as a heading, a Banking or Accounts section, an account name followed by a dash and the last four digits of the account number, a dollar balance beside the account name, FDIC-Insured notices, and tabs or menus such as Accounts, Dashboard, Pay & Transfer, Deposit Checks, and Services. This is a bank statement.",
  "Promotional or marketing blocks on a banking screen, such as Preferred Rewards, Link a Bank of America account, Offers, or credit card and loan suggestions, are part of the banking app interface. They do not make the page marketing material, and they never change the category to is_other_document.",
  "Mobile banking app screenshots, online banking portal screens, and browser or email prints of banking screens all count, including screenshots with phone status bars or navigation menus.",
  "Strong indicators include bank names or logos, FDIC-Insured notices, 'Provided by' followed by a bank name, bank virtual assistant prompts such as 'Hi, I'm Erica', account holder or business names, account names with masked or partial account numbers, account balances, transaction dates, transaction descriptions, debit and credit amounts, signed amounts such as +$100.00 or -$100.00, deposits, withdrawals, ACH transactions, wire transfers, transfers, ATM deposits and withdrawals, counter credits, card purchases, payment activity, and running account balances shown under or beside each transaction.",
  "Additional indicators include headings such as Account Statement, Bank Statement, Account Activity, Account, Accounts, Banking, Dashboard, Transactions, All Transactions, Statement Period, Beginning Balance, Ending Balance, Available Balance, Present Balance, Total Deposits, Total Withdrawals, and Daily Balance Summary.",
  "A bank name combined with a transaction list or table containing dates, descriptions, amounts, and balances is strong evidence of a bank statement, even when the words 'Bank Statement' are absent.",
  "Recognize statements, screenshots, and transaction exports from banks such as Chase, JPMorgan Chase, Bank of America, Wells Fargo, Capital One, Citibank, U.S. Bank, PNC, Truist, TD Bank, M&T Bank (MTB), Regions Bank, Fifth Third Bank, Huntington Bank, and other recognized financial institutions.",
  "Transaction descriptions that mention payment processors or merchant services, such as Transfer SQUARE, Stripe, Clover, Toast, Shopify, or PayPal, never change the category. They only describe where a deposit came from.",
  "Do not set is_other_document merely because the page is a phone screenshot, is printed from an email or website, has no formal statement layout, shows no transactions, or looks like only part of a larger document.",
  "Only a document issued BY a card processor or payment processor itself is not a bank statement. Merchant processing statements, card processor statements, and sales or settlement reports report card sales volume, processing fees, and batch deposit amounts for a merchant ID rather than activity in a bank account, and they have no bank name, no bank account number, and no account balance. Classify those as is_other_document.",

  "5. is_other_document:",
  "Use this flag only when the page does not meet the criteria for is_application_form, is_driving_licence, is_voided_check, or is_bank_statement.",
  "Examples include tax returns such as Forms 1040 and 1120-S, IRS letters, invoices, utility bills, articles of organization, merchant processing statements and card processor statements issued by the processor itself (with headings like Merchant Statement, Activity Detail, Deposit Detail, Billing Summary, Processing Detail, Batch Amt, and Fees Paid), and general business documents.",
  "Do not use is_other_document as a fallback simply because the page's layout is unfamiliar or its exact document title is missing.",

  "CATEGORY PRIORITY:",
  "If the page contains a genuine funding application, set is_application_form. Otherwise, if the page shows bank account information or transactions, set is_bank_statement.",
  "When evidence is ambiguous between is_bank_statement and is_other_document, and a bank name appears with account, balance, or transaction information, choose is_bank_statement.",

  "EXTRACTION RULE:",
  "If is_application_form is true, extract all required business funding and owner fields according to the output schema.",
  "If any other flag is true, set only that flag to true, set all other flags to false, and set every application extraction field to null.",
  "Always set exactly one flag to true. Never leave all flags false and never set more than one flag to true.",

])

# Prompts for Bank Statement Extraction Engine
SUMMARY_INSTRUCTIONS = """You are a financial document extraction engine specialized in bank statements.
Extract the high-level statement summary and balance metadata from the header page(s).

KEY INFORMATION TO EXTRACT:
- Bank Name: Name of the bank issuing the statement
- Account Holder: Name of the account holder
- Account Holder Address: Address of the account holder, null if missing
- Account Holder City: City of the account holder, null if missing
- Account Holder State: State of the account holder, null if missing
- Account Holder Zip: Zip code of the account holder, null if missing
- Account Number: Bank account number
- Statement Period: Start and end dates in YYYY-MM-DD format
- Average Daily Balance: Average daily balance as per summary, null if absent
- Average Ledger Balance: Average ledger balance, null if absent
- Beginning Balance: Balance at start of statement period
- Ending Balance: Balance at end of statement period
- Total Deposits: Total deposits amount from summary
- Total Withdrawals: Total withdrawals amount from summary
- Number of Credit Transactions: Count of credit/deposits from summary, null if absent
- Number of Debit Transactions: Count of debit/withdrawals from summary, null if absent
- Service Charges: Total service charges applied, null if absent
"""

BATCH_INSTRUCTIONS = """You are a financial document extraction engine specialized in bank statements.
Extract all transaction line items from the provided pages.

CRITICAL REQUIREMENTS:
1. Extract EVERY individual transaction line item visible on the provided page(s) without omission or truncation.
2. Amounts: Positive numbers for credits/deposits, negative numbers for debits/withdrawals/fees/checks.
3. If a running balance or ending daily balance is explicitly printed on the row, extract it into 'balance'; otherwise use null.

POLARITY & DAY-LEVEL MATH INVARIANT (CREDIT VS DEBIT):
- Statements often have two amount columns ('Deposits/Credits' and 'Withdrawals/Debits'). Look at the column header!
- Compare amount alignment against rows above and below: if an amount aligns under Withdrawals/Debits, it is a DEBIT.
- Check semantic words: 'Purchase', 'Payment', 'ACH Debit', 'Zelle to', 'Check' are DEBITS. 'Online Transfer From', 'Zelle From', 'Deposit' are CREDITS.
- ENDING DAILY BALANCE NOTE: When a bank prints 'Ending daily balance', that balance is ONLY printed once on the LAST transaction of the day. It reflects the net result of ALL transactions for that day, NOT just that single row! 
  Verify the day's math: Previous Day Balance + Day Deposits - Day Withdrawals = Ending Daily Balance.
  Never mark an outgoing payment as a credit simply because the day's final balance went up due to other deposits.

RETURNED / UNPAID ITEMS INVARIANT:
- Informational disclosure tables such as 'Items returned unpaid', 'Returned checks', or 'NSF notices' list transactions that did NOT clear and are EXCLUDED from the bank's total deposit and withdrawal figures.
- If extracting an item from an unpaid/returned disclosure schedule, you MUST prefix the description with '[RETURNED UNPAID] '.
- If the returned item was an attempted deposit, preserve it as a credit (+ amount) with '[RETURNED UNPAID] ' prefix. Do NOT make it a debit.

BOUNDARY & GROUPING RULES:
- SAME-DAY GROUPINGS: In statements (e.g. Bank of America, Chase), when multiple transactions occur on the same day, the date is often printed only on the first row of that group. If a row lacks an explicit date, infer the date from the preceding transaction row in that same date block.
- Multi-line descriptions: Concatenate primary lines and indented detail lines using ' - '. Strip statement formatting artifacts like leading single quotes (') or bullets.
- Exclude statement totals, opening/closing balance headers, and daily balance summary tables.
"""

FULL_INSTRUCTIONS = """You are a financial document extraction engine specialized in bank statements.
CRITICAL REQUIREMENTS:
1. Extract ALL transactions across ALL pages without omission, deduplication, or truncation.
2. Extract exact amounts: Positive for credits/deposits, negative for debits/withdrawals.
3. If a running balance or ending daily balance exists on the row, extract it into 'balance'; otherwise use null.
4. Verify day-level math on daily ending balances: Previous Day Balance + Day Deposits - Day Withdrawals = Ending Daily Balance.
5. If extracting items from an 'Items returned unpaid' or NSF schedule that did not clear the posted balance, prefix the description with '[RETURNED UNPAID] '.
6. Infer dates for same-day groups if only printed on the first line.
7. Concatenate indented detail lines with ' - '.
8. Exclude beginning/ending balances, statement totals, and daily balance summary tables from transactions.
"""

# ============================================================================
# SCHEMAS
# ============================================================================

CLASSIFICATION_SCHEMA = {
    "type": "json_schema",
    "name": "document_classification_and_extraction_schema",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": [
                    "application_form",
                    "driving_licence",
                    "bank_statement",
                    "voide_statement",
                    "other_document",
                ],
                "description": "Detected category of the document",
            },
            "business_information": {
                "type": ["object", "null"],
                "description": "Business information, null if category is not application_form",
                "properties": {
                    "legal_business_name": {"type": ["string", "null"]},
                    "dba": {"type": ["string", "null"]},
                    "ein": {"type": ["string", "null"]},
                    "address": {"type": ["string", "null"]},
                    "city": {"type": ["string", "null"]},
                    "state": {"type": ["string", "null"]},
                    "zip": {"type": ["string", "null"]},
                    "phone": {"type": ["string", "null"]},
                    "monthly_sales": {"type": ["number", "null"]},
                },
                "required": [
                    "legal_business_name", "dba", "ein", "address",
                    "city", "state", "zip", "phone", "monthly_sales"
                ],
                "additionalProperties": False,
            },
            "funding_request": {
                "type": ["object", "null"],
                "description": "Funding request details, null if category is not application_form",
                "properties": {
                    "requested_amount": {"type": ["number", "null"]},
                    "use_of_funds": {"type": ["string", "null"]},
                },
                "required": ["requested_amount", "use_of_funds"],
                "additionalProperties": False,
            },
            "owners": {
                "type": ["array", "null"],
                "description": "Owner details, null if category is not application_form",
                "items": {
                    "type": "object",
                    "properties": {
                        "owner_name": {"type": ["string", "null"]},
                        "ssn": {"type": ["string", "null"]},
                        "percent_of_ownership": {"type": ["string", "null"]},
                        "title": {"type": ["string", "null"]},
                        "address": {"type": ["string", "null"]},
                        "phone": {"type": ["string", "null"]},
                        "dob": {"type": ["string", "null"]},
                    },
                    "required": [
                        "owner_name", "ssn", "percent_of_ownership",
                        "title", "address", "phone", "dob"
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["category", "business_information", "funding_request", "owners"],
        "additionalProperties": False,
    },
}

SUMMARY_SCHEMA = {
    "type": "json_schema",
    "name": "statement_summary_schema",
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
        },
        "required": [
            "bankName", "accountHolder", "accountHolderAddress", "accountHolderCity",
            "accountHolderState", "accountHolderZip", "accountNumber", "startdate", "enddate",
            "beginningBalance", "endingBalance", "avgDailyBalance", "avgLedgerBalance",
            "totalDeposits", "totalWithdrawals", "numberOfCreditTransactions",
            "numberOfDebitsTransactions", "serviceCharges",
        ],
        "additionalProperties": False,
    },
}

BATCH_TRANSACTIONS_SCHEMA = {
    "type": "json_schema",
    "name": "transactions_batch_schema",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
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
            }
        },
        "required": ["transactions"],
        "additionalProperties": False,
    },
}

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
# PIPELINE CLASS
# ============================================================================

class CompanyDocumentPipeline:
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
            "cost": in_cost + cached_cost + out_cost,
        }

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

    def _encode_image(self, image_path):
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def is_rich_text_pdf(self, path):
        try:
            with pdfplumber.open(path) as pdf:
                num_pages = len(pdf.pages)
                if num_pages == 0:
                    return False
                text = "".join((p.extract_text() or "") + "\n" for p in pdf.pages[:min(MAX_EXTRACTION_PAGES, num_pages)]).strip()

            if len(text) < 250 or (len(text) / min(MAX_EXTRACTION_PAGES, num_pages)) < 150:
                return False
            if len(re.findall(r"\b\d{1,3}(?:,\d{3})*\.\d{2}\b", text)) < 4:
                return False
            return True
        except Exception:
            return False

    def classify_document(self, file_path):
        ext = os.path.splitext(file_path)[1].lower()
        is_pdf = ext == ".pdf"

        if is_pdf:
            system_prompt = PDF_EXTRACTION_SYSTEM_PROMPT
            has_text = False
            first_pages_text = ""
            try:
                with pdfplumber.open(file_path) as pdf:
                    num_pages = len(pdf.pages)
                    pages_to_check = [pdf.pages[i] for i in range(min(1, num_pages))]
                    first_pages_text = "\n\n".join(p.extract_text(layout=True) or "" for p in pages_to_check).strip()
                    has_text = len(first_pages_text) >= 150
            except Exception:
                has_text = False

            if has_text:
                content = [{"type": "input_text", "text": f"Document content (Pages 1-2):\n\n{first_pages_text}"}]
            else:
                data_uri = self._slice_pdf_to_base64(file_path, 0, 1)
                content = [{"type": "input_file", "filename": os.path.basename(file_path), "file_data": data_uri}]
        else:
            system_prompt = IMAGE_EXTRACTION_SYSTEM_PROMPT
            b64_img = self._encode_image(file_path)
            mime = "image/png" if ext == ".png" else "image/jpeg"
            content = [{"type": "input_image", "image_url": f"data:{mime};base64,{b64_img}"}]

        resp = self.client.responses.create(
            model=MODEL_CONFIG["model"],
            instructions=system_prompt,
            input=[{"role": "user", "content": content}],
            text={"format": CLASSIFICATION_SCHEMA},
        )
        usage_info = self._calc_cost(resp.usage)
        return json.loads(resp.output_text), usage_info

    # ------------------------------------------------------------------------
    # 3-PAGE BANK STATEMENT EXTRACTION ENGINE
    # ------------------------------------------------------------------------

    def _extract_3pages_tier1_text(self, file_path):
        pages = []
        with pdfplumber.open(file_path) as pdf:
            total_pages = len(pdf.pages)
            limit = min(MAX_EXTRACTION_PAGES, total_pages)
            for i in range(limit):
                pages.append((i + 1, pdf.pages[i].extract_text(layout=True) or ""))

        usage_acc = {
            "input_tokens": 0, "cached_input_tokens": 0, "uncached_input_tokens": 0,
            "output_tokens": 0, "cost": 0.0,
        }

        header_text = "\n\n".join(f"=== PAGE {p[0]} ===\n{p[1]}" for p in pages[:min(2, len(pages))])
        resp_sum = self.client.responses.create(
            model=MODEL_CONFIG["model"],
            instructions=SUMMARY_INSTRUCTIONS,
            input=[{"role": "user", "content": f"Statement header:\n\n{header_text}"}],
            text={"format": SUMMARY_SCHEMA},
        )
        u_sum = self._calc_cost(resp_sum.usage)
        for k in usage_acc:
            usage_acc[k] += u_sum[k]
        summary_data = json.loads(resp_sum.output_text)

        all_txs = []
        for i in range(0, len(pages), TEXT_BATCH_SIZE):
            batch = pages[i:i + TEXT_BATCH_SIZE]
            batch_text = "\n\n".join(f"=== PAGE {p[0]} ===\n{p[1]}" for p in batch)
            prompt = (
                f"Period: {summary_data.get('startdate')} to {summary_data.get('enddate')}\n"
                f"Text from Pages {batch[0][0]}-{batch[-1][0]} of {total_pages}:\n\n{batch_text}"
            )
            resp_b = self.client.responses.create(
                model=MODEL_CONFIG["model"],
                instructions=BATCH_INSTRUCTIONS,
                input=[{"role": "user", "content": prompt}],
                text={"format": BATCH_TRANSACTIONS_SCHEMA},
            )
            u_b = self._calc_cost(resp_b.usage)
            for k in usage_acc:
                usage_acc[k] += u_b[k]

            b_txs = json.loads(resp_b.output_text).get("transactions", [])
            all_txs.extend(b_txs)

        doc = {
            **summary_data,
            "extractionScope": f"First {len(pages)} pages of {total_pages}",
            "transactions": all_txs,
        }
        return doc, usage_acc, total_pages

    def _extract_3pages_tier2_base64(self, file_path):
        with open(file_path, "rb") as f:
            total_pages = len(PdfReader(f).pages)
        limit = min(MAX_EXTRACTION_PAGES, total_pages)

        usage_acc = {
            "input_tokens": 0, "cached_input_tokens": 0, "uncached_input_tokens": 0,
            "output_tokens": 0, "cost": 0.0,
        }

        chunk_header_b64 = self._slice_pdf_to_base64(file_path, 0, min(1, limit))
        resp_sum = self.client.responses.create(
            model=MODEL_CONFIG["model"],
            instructions=SUMMARY_INSTRUCTIONS,
            input=[{"role": "user", "content": [{"type": "input_file", "filename": "header.pdf", "file_data": chunk_header_b64}]}],
            text={"format": SUMMARY_SCHEMA},
        )
        u_sum = self._calc_cost(resp_sum.usage)
        for k in usage_acc:
            usage_acc[k] += u_sum[k]
        summary_data = json.loads(resp_sum.output_text)

        chunk_all_b64 = self._slice_pdf_to_base64(file_path, 0, limit)
        resp_tx = self.client.responses.create(
            model=MODEL_CONFIG["model"],
            instructions=BATCH_INSTRUCTIONS,
            input=[{"role": "user", "content": [{"type": "input_file", "filename": "pages_1_to_3.pdf", "file_data": chunk_all_b64}]}],
            text={"format": BATCH_TRANSACTIONS_SCHEMA},
        )
        u_tx = self._calc_cost(resp_tx.usage)
        for k in usage_acc:
            usage_acc[k] += u_tx[k]
        tx_data = json.loads(resp_tx.output_text).get("transactions", [])

        doc = {
            **summary_data,
            "extractionScope": f"First {limit} pages of {total_pages}",
            "transactions": tx_data,
        }
        return doc, usage_acc, total_pages

    def _extract_3pages_tier3_images(self, file_path):
        images = convert_from_path(file_path, dpi=IMAGE_DPI, first_page=1, last_page=MAX_EXTRACTION_PAGES)
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
            instructions=FULL_INSTRUCTIONS,
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

        u = self._calc_cost(resp.usage)
        doc = json.loads(resp.output_text)
        doc["extractionScope"] = f"First {len(images)} pages"
        return doc, u, len(images)

    def extract_bank_statement_3_pages(self, file_path):
        is_digital = self.is_rich_text_pdf(file_path)

        if is_digital:
            try:
                raw_doc, usage, total_p = self._extract_3pages_tier1_text(file_path)
                txs = raw_doc.get("transactions", [])
                if len(txs) > 0:
                    processed = self._post_process(raw_doc)
                    return processed, usage, "Tier 1 (Batched Text)"
            except Exception:
                pass

        if PdfReader is not None:
            try:
                raw_doc, usage, total_p = self._extract_3pages_tier2_base64(file_path)
                txs = raw_doc.get("transactions", [])
                if len(txs) > 0 or not is_digital:
                    processed = self._post_process(raw_doc)
                    return processed, usage, "Tier 2 (Batched Base64 Vision)"
            except Exception:
                pass

        raw_doc, usage, total_p = self._extract_3pages_tier3_images(file_path)
        processed = self._post_process(raw_doc)
        return processed, usage, "Tier 3 (High-Res Images)"

    def _post_process(self, data):
        txs = data.get("transactions", []) or []
        valid_txs = []
        for tx in txs:
            if not isinstance(tx, dict):
                continue
            amt = _parse_num(tx.get("amount"))
            dt = tx.get("postingDate")
            desc = tx.get("description")
            if not dt or not desc or amt is None:
                continue
            tx["amount"] = amt
            tx["type"] = tx.get("type") or ("credit" if amt > 0 else "debit")
            tx["balance"] = _parse_num(tx.get("balance"))
            valid_txs.append(tx)

        for idx, tx in enumerate(valid_txs, start=1):
            tx["trID"] = f"TR{idx:05d}"

        data["transactions"] = valid_txs
        return data

    def audit_3pages(self, doc):
        txs = doc.get("transactions", []) or []
        credit_sum = round(sum(tx["amount"] for tx in txs if tx.get("type") == "credit" and "[RETURNED UNPAID]" not in (tx.get("description") or "").upper()), 2)
        debit_sum = round(sum(abs(tx["amount"]) for tx in txs if tx.get("type") == "debit" and "[RETURNED UNPAID]" not in (tx.get("description") or "").upper()), 2)
        returned_count = sum(1 for tx in txs if "[RETURNED UNPAID]" in (tx.get("description") or "").upper())

        seen_fingerprints = {}
        leaks = []
        for tx in txs:
            parts = (tx.get("description") or "").split(" - ")
            if len(parts) > 1:
                detail = " - ".join(parts[1:]).strip()
                if len(detail) > 15:
                    is_card = bool(re.search(r"X{4,}|\b\d{4}\s*X+|XXXX|card\s*\d{4}", detail, re.IGNORECASE))
                    if not is_card:
                        if detail in seen_fingerprints:
                            leaks.append(f"Subline across {seen_fingerprints[detail]} and {tx.get('trID')}")
                        else:
                            seen_fingerprints[detail] = tx.get("trID")

        return {
            "credit_sum_3pages": credit_sum,
            "debit_sum_3pages": debit_sum,
            "returned_unpaid_items": returned_count,
            "subline_leaks": len(leaks),
        }

    # ------------------------------------------------------------------------
    # MAIN PIPELINE WORKFLOW
    # ------------------------------------------------------------------------

    def process_company_folder(self, company_dir):
        company_name = os.path.basename(os.path.normpath(company_dir))
        print("=" * 78)
        print(f"PROCESSING COMPANY: {company_name}")
        print(f"Source Folder:     {company_dir}")
        print(f"Extraction Scope:  First {MAX_EXTRACTION_PAGES} pages of Bank Statements (R&D mode)")
        print("=" * 78)

        bank_stmt_dir = os.path.join(OUTPUT_ROOT, company_name, "Bank Statement")
        app_dir = os.path.join(OUTPUT_ROOT, company_name, "applications")
        other_dir = os.path.join(OUTPUT_ROOT, company_name, "Other Documents")

        for d in [bank_stmt_dir, app_dir, other_dir]:
            os.makedirs(d, exist_ok=True)

        valid_exts = {".pdf", ".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp"}
        files = [
            f for f in os.listdir(company_dir)
            if os.path.isfile(os.path.join(company_dir, f)) and os.path.splitext(f)[1].lower() in valid_exts
        ]

        if not files:
            print("No supported documents found in directory.")
            return

        print(f"Found {len(files)} document(s) to triage and process.\n")

        grand_totals = {
            "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0,
            "cost": 0.0, "cpu_time": 0.0, "wall_time": 0.0,
        }

        for idx, fn in enumerate(sorted(files), start=1):
            fp = os.path.join(company_dir, fn)
            print(f"[{idx}/{len(files)}] Triaging: {fn}")

            tracemalloc.start()
            t_cpu_start = time.process_time()
            t_wall_start = time.perf_counter()

            file_tokens_in = 0
            file_tokens_cached = 0
            file_tokens_out = 0
            file_cost = 0.0

            try:
                result, u_class = self.classify_document(fp)
                category = result.get("category", "other_document")

                file_tokens_in += u_class["input_tokens"]
                file_tokens_cached += u_class["cached_input_tokens"]
                file_tokens_out += u_class["output_tokens"]
                file_cost += u_class["cost"]

                print(f"    Detected Category: [{category}]")

                base_fn, ext = os.path.splitext(fn)

                if category == "bank_statement":
                    extracted_doc, u_ext, engine_tier = self.extract_bank_statement_3_pages(fp)
                    file_tokens_in += u_ext["input_tokens"]
                    file_tokens_cached += u_ext["cached_input_tokens"]
                    file_tokens_out += u_ext["output_tokens"]
                    file_cost += u_ext["cost"]

                    audit = self.audit_3pages(extracted_doc)
                    save_path = os.path.join(bank_stmt_dir, f"{base_fn}_extracted_3pages.json")
                    with open(save_path, "w", encoding="utf-8") as f:
                        json.dump(extracted_doc, f, indent=2, ensure_ascii=False)

                    print(f"    Engine Used:       {engine_tier}")
                    print(f"    Action:            Extracted first 3 pages ({len(extracted_doc.get('transactions', []))} txns)")
                    print(f"    1-Page Audit:      Deposits: {_money(audit['credit_sum_3pages'])} | Withdrawals: {_money(audit['debit_sum_3pages'])}")
                    if audit["returned_unpaid_items"]:
                        print(f"    Flagged NSF:       {audit['returned_unpaid_items']} returned/unpaid item(s)")
                    print(f"    Saved:             {save_path}")

                elif category == "application_form":
                    dest_fp = os.path.join(app_dir, fn)
                    shutil.copy2(fp, dest_fp)
                    app_json_path = os.path.join(app_dir, f"{base_fn}_extracted_application.json")
                    with open(app_json_path, "w", encoding="utf-8") as f:
                        json.dump(result, f, indent=2, ensure_ascii=False)

                    print(f"    Action:            Copied form to applications/ & saved extracted application JSON")
                    print(f"    Saved File:        {dest_fp}")
                    print(f"    Saved Data:        {app_json_path}")

                else:
                    # driving_licence, voide_statement, mtb, or other_document
                    suffix = category.upper()
                    new_fn = f"{base_fn}_{suffix}{ext}"
                    dest_fp = os.path.join(other_dir, new_fn)
                    shutil.copy2(fp, dest_fp)
                    print(f"    Action:            Copied & renamed [{category}] to Other Documents/")
                    print(f"    Saved:             {dest_fp}")

            except Exception as e:
                print(f"    Error processing {fn}: {e}")

            finally:
                t_wall_elapsed = time.perf_counter() - t_wall_start
                t_cpu_elapsed = time.process_time() - t_cpu_start
                _, peak_mem_bytes = tracemalloc.get_traced_memory()
                tracemalloc.stop()

                peak_ram_mb = peak_mem_bytes / (1024 * 1024)

                grand_totals["input_tokens"] += file_tokens_in
                grand_totals["cached_input_tokens"] += file_tokens_cached
                grand_totals["output_tokens"] += file_tokens_out
                grand_totals["cost"] += file_cost
                grand_totals["cpu_time"] += t_cpu_elapsed
                grand_totals["wall_time"] += t_wall_elapsed

                print("    Performance & Cost:")
                print(f"      • Tokens:      Input: {file_tokens_in:,} (Cached: {file_tokens_cached:,}) | Output: {file_tokens_out:,}")
                print(f"      • API Cost:    ${file_cost:.6f}")
                print(f"      • Hardware:    Wall Time: {t_wall_elapsed:.2f}s | CPU Time: {t_cpu_elapsed:.2f}s | Peak RAM: {peak_ram_mb:.2f} MB\n")

        print("=" * 78)
        print(f"COMPLETED TRIAGE & EXTRACTION FOR: {company_name}")
        print(f"Outputs located in: {os.path.join(OUTPUT_ROOT, company_name)}/")
        print("-" * 78)
        print("TOTAL PIPELINE CONSUMPTION:")
        print(f"  • Total Input Tokens:   {grand_totals['input_tokens']:,} (Cached: {grand_totals['cached_input_tokens']:,})")
        print(f"  • Total Output Tokens:  {grand_totals['output_tokens']:,}")
        print(f"  • Total API Cost:       ${grand_totals['cost']:.4f}")
        print(f"  • Total Wall Time:      {grand_totals['wall_time']:.2f}s")
        print(f"  • Total CPU Time:       {grand_totals['cpu_time']:.2f}s")
        print("=" * 78)


# ============================================================================
# MAIN
# ============================================================================

def main():
    if not os.path.exists(COMPANY_DIR):
        print(f"Error: Company directory '{COMPANY_DIR}' does not exist.")
        sys.exit(1)

    if not os.getenv("OPENAI_API_KEY"):
        print("Error: OPENAI_API_KEY is not set.")
        sys.exit(1)

    pipeline = CompanyDocumentPipeline()
    pipeline.process_company_folder(COMPANY_DIR)


if __name__ == "__main__":
    main()