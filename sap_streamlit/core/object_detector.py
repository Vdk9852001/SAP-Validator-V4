"""
SAP Object Detector
Detects the SAP migration object type from LTMC headers and post-load headers.
"""
from __future__ import annotations
import re
from typing import Optional

# Signature fields that strongly identify an object
OBJECT_SIGNATURES = {
    "Business Partner / Customer": {
        "required_any": ["KUNNR","BU_PARTNER","PARTNER","CUSTOMER","BP_NUMBER"],
        "supporting":   ["KTOKD","NAME1","NAMORG1","LAND1","CITY1","VKORG","KNVV","KNA1"],
        "description":  "Customer / Business Partner master data",
    },
    "Vendor / Supplier": {
        "required_any": ["LIFNR","VENDOR","SUPPLIER"],
        "supporting":   ["KTOKK","NAME1","LAND1","EKORG","LFA1","LFM1"],
        "description":  "Vendor / Supplier master data",
    },
    "Material Master": {
        "required_any": ["MATNR","MATERIAL","PRODUCT"],
        "supporting":   ["MTART","MATKL","MEINS","MAKTX","WERKS","MARC","MARA"],
        "description":  "Material master data",
    },
    "Purchasing Info Record": {
        "required_any": ["INFNR","INFO_RECORD"],
        "supporting":   ["LIFNR","MATNR","EKORG","WERKS","NETPR"],
        "description":  "Purchasing info records",
    },
    "Condition Records": {
        "required_any": ["KSCHL","CONDITION_TYPE"],
        "supporting":   ["KNUMH","MATNR","KUNNR","VKORG","VTWEG","KBETR","DATAB","DATBI"],
        "description":  "Pricing / condition records",
    },
    "Purchase Orders": {
        "required_any": ["EBELN","PO_NUMBER","PURCHASE_ORDER"],
        "supporting":   ["EBELP","MATNR","LIFNR","EKORG","WERKS","MENGE"],
        "description":  "Purchase orders",
    },
    "Sales Orders": {
        "required_any": ["VBELN","SO_NUMBER","SALES_ORDER"],
        "supporting":   ["POSNR","MATNR","KUNNR","VKORG","VTWEG","MENGE"],
        "description":  "Sales orders",
    },
    "GL Account": {
        "required_any": ["SAKNR","GL_ACCOUNT","ACCOUNT"],
        "supporting":   ["BUKRS","KTOKS","WAERS","XBILK"],
        "description":  "General ledger accounts",
    },
    "Cost Center": {
        "required_any": ["KOSTL","COST_CENTER"],
        "supporting":   ["KOKRS","BUKRS","DATAB","DATBI","KTEXT","VERAK"],
        "description":  "Cost centres",
    },
    "Profit Center": {
        "required_any": ["PRCTR","PROFIT_CENTER"],
        "supporting":   ["KOKRS","BUKRS","DATAB","DATBI","KTEXT"],
        "description":  "Profit centres",
    },
    "Work Center": {
        "required_any": ["ARBPL","WORK_CENTER"],
        "supporting":   ["WERKS","VERWE","KTEXT","VERAN","CANUM","KAPAR"],
        "description":  "Work centres / resources",
    },
    "Asset Master": {
        "required_any": ["ANLN1","ASSET"],
        "supporting":   ["ANLN2","BUKRS","ANLKL","AKTIV","TXT50"],
        "description":  "Fixed asset master data",
    },
    "Bank Master": {
        "required_any": ["BANKL","BANK_KEY"],
        "supporting":   ["BANKS","BANKA","SWIFT"],
        "description":  "Bank master data",
    },
}


def detect_object(ltmc_cols: list, postload_cols: list = None) -> dict:
    """
    Detect SAP object from column headers.
    Returns {object_name, confidence, description, matched_fields}
    """
    all_cols = set()
    for c in (ltmc_cols or []):
        all_cols.add(c.upper().strip())
    for c in (postload_cols or []):
        all_cols.add(c.upper().strip())
        # Also try stripping descriptions like "Customer Number (KUNNR)"
        m = re.search(r'\(([A-Z0-9_]+)\)', c)
        if m:
            all_cols.add(m.group(1).upper())

    best_name  = "Unknown"
    best_score = 0
    best_meta  = {}

    for obj_name, sig in OBJECT_SIGNATURES.items():
        required_hits = [f for f in sig["required_any"] if f in all_cols]
        support_hits  = [f for f in sig["supporting"]    if f in all_cols]

        if not required_hits:
            continue

        score = len(required_hits) * 10 + len(support_hits)
        if score > best_score:
            best_score = score
            best_name  = obj_name
            best_meta  = {
                "description":    sig["description"],
                "required_found": required_hits,
                "support_found":  support_hits,
            }

    conf = "High" if best_score >= 15 else "Medium" if best_score >= 10 else "Low"
    return {
        "object":      best_name,
        "confidence":  conf,
        "score":       best_score,
        "description": best_meta.get("description", ""),
        "matched_fields": best_meta.get("required_found", []) + best_meta.get("support_found", []),
    }
