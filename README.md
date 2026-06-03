# Genpact SAP Migration Validator — V4

## Quick Start
```
pip install -r requirements.txt
python dashboard/app.py
# Open http://localhost:5000
```

## Folder Structure
```
sap_validator_v4/
  config/
    field_labels.json     <- SAP field friendly names (KUNNR = Customer Number)
    field_aliases.json    <- Cross-name mappings (NAME1 -> NAMORG1)
  core/
    field_labels.py       <- Label resolution engine
    field_mapper.py       <- Alias + fuzzy mapping engine
    object_config.py      <- SAP object configs (join keys, key fields)
    validator.py          <- Core validation engine (vectorized)
    reporter.py           <- Excel report generator
  dashboard/
    app.py                <- Flask server
    templates/
      dashboard.html      <- UI
  data/
    source/               <- DROP SOURCE FILES HERE
    target/               <- DROP TARGET FILES HERE
  reports/                <- Excel reports saved here
```

## How Field Mapping Works
Source files use SAP 4.7 names, target files use S/4HANA names.
The mapper resolves them automatically:

| Priority | Method         | Example                    |
|----------|---------------|----------------------------|
| 1        | Exact match   | MATNR -> MATNR             |
| 2        | Object alias  | NAME1 -> NAMORG1 (CUSTOMER)|
| 3        | Global alias  | LAND1 -> COUNTRY           |
| 4        | Fuzzy match   | similarity >= 90%           |

## Adding New SAP Objects
Edit `config/field_aliases.json` — no code changes needed:
```json
"PURCHASING": {
  "LIFNR": ["VENDOR_ID", "SUPPLIER"],
  "EBELN": ["PO_NUMBER", "ORDER_ID"]
}
```
Drop files named `PURCHASING.csv` in source/target and the tool auto-maps.

## Field Labels
`KUNNR` shows as **Customer Number**, `NAME1` shows as **Name 1 / Customer Name**,
cross-mapped fields show as **Customer Name  NAME1 → NAMORG1**.

To override labels: upload a CSV via Settings with format:
```
FIELD_NAME,YOUR_LABEL
KUNNR,Customer ID
```
