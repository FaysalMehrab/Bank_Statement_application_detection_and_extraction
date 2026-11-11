"""
Strict JSON Schemas for Triage, Topology, Statements (V3 & V4), MCA, and Freeform Summaries.
"""

# Stage 1: Classification & Application Form Schema
CLASSIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {
            "type": "string",
            "enum": [
                "application_form",
                "driving_licence",
                "bank_statement",
                "voided_check",
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
}

# Freeform Entity Extraction (for Licenses, Voided Checks, Tax Forms, etc.)
FREEFORM_ENTITY_SCHEMA = {
    "type": "object",
    "properties": {
        "documentType": {"type": "string"},
        "entityName": {"type": ["string", "null"]},
        "entityAddress": {"type": ["string", "null"]},
        "identifierNumber": {"type": ["string", "null"], "description": "DL#, Check#, EIN, SSN, or Account Number"},
        "routingNumber": {"type": ["string", "null"]},
        "issueOrEffectiveDate": {"type": ["string", "null"]},
        "expirationDate": {"type": ["string", "null"]},
        "summary": {"type": "string", "description": "Structured executive summary of all visible information on the document"},
        "extractedFields": {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "description": "Any key-value details extracted from the document",
        },
    },
    "required": ["documentType", "entityName", "entityAddress", "identifierNumber", "routingNumber", "issueOrEffectiveDate", "expirationDate", "summary", "extractedFields"],
    "additionalProperties": False,
}

# Stage 2: Statement Topology Schema
TOPOLOGY_SCHEMA = {
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
}

# Stage 3A: V3 Single-Account Document Schema
V3_SINGLE_DOCUMENT_SCHEMA = {
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
}

# Stage 3B: V4 Multi-Account Schemas
V4_METADATA_SCHEMA = {
    "type": "object",
    "properties": {
        "bankName": {"type": "string"},
        "accountHolder": {"type": "string"},
        "accountHolderAddress": {"type": ["string", "null"]},
        "accountHolderCity": {"type": ["string", "null"]},
        "accountHolderState": {"type": ["string", "null"]},
        "accountHolderZip": {"type": ["string", "null"]},
        "primaryAccountNumber": {"type": "string"},
        "startdate": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
        "enddate": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
        "avgDailyBalance": {"type": ["number", "null"]},
        "avgLedgerBalance": {"type": ["number", "null"]},
    },
    "required": [
        "bankName", "accountHolder", "accountHolderAddress", "accountHolderCity",
        "accountHolderState", "accountHolderZip", "primaryAccountNumber",
        "startdate", "enddate", "avgDailyBalance", "avgLedgerBalance",
    ],
    "additionalProperties": False,
}

V4_BATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "accountSummaries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "accountNumber": {"type": "string"},
                    "accountName": {"type": "string"},
                    "beginningBalance": {"type": "number"},
                    "endingBalance": {"type": "number"},
                    "totalDeposits": {"type": "number"},
                    "totalWithdrawals": {"type": "number"},
                    "serviceCharges": {"type": ["number", "null"]},
                    "numberOfCreditTransactions": {"type": ["integer", "null"]},
                    "numberOfDebitsTransactions": {"type": ["integer", "null"]},
                },
                "required": [
                    "accountNumber", "accountName", "beginningBalance",
                    "endingBalance", "totalDeposits", "totalWithdrawals",
                    "serviceCharges", "numberOfCreditTransactions",
                    "numberOfDebitsTransactions",
                ],
                "additionalProperties": False,
            },
        },
        "transactions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "accountNumber": {"type": "string"},
                    "postingDate": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
                    "description": {"type": "string", "minLength": 1},
                    "amount": {"type": "number"},
                    "type": {"type": "string", "enum": ["credit", "debit", "returned_item"]},
                    "balance": {"type": ["number", "null"]},
                },
                "required": ["accountNumber", "postingDate", "description", "amount", "type", "balance"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["accountSummaries", "transactions"],
    "additionalProperties": False,
}

# Stage 4: Financial Position & MCA Schema (mca_others_v3)
MCA_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "analyzed_transactions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "trID": {"type": "string"},
                    "trCategory": {"type": "string"},
                    "isMca": {"type": "boolean"},
                    "lenderName": {"type": ["string", "null"]},
                    "isCreditCard": {"type": "boolean"},
                    "creditorName": {"type": ["string", "null"]},
                    "isBusinessLoan": {"type": "boolean"},
                    "loanLenderName": {"type": ["string", "null"]},
                    "isBnpl": {"type": "boolean"},
                    "bnplProvider": {"type": ["string", "null"]},
                    "isReturnedItem": {"type": "boolean"},
                },
                "required": [
                    "trID", "trCategory", "isMca", "lenderName",
                    "isCreditCard", "creditorName", "isBusinessLoan",
                    "loanLenderName", "isBnpl", "bnplProvider", "isReturnedItem"
                ],
                "additionalProperties": False,
            }
        }
    },
    "required": ["analyzed_transactions"],
    "additionalProperties": False,
}
