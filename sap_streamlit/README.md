# SAP LTMC Migration Validator

Production-ready Streamlit app for validating SAP LTMC migration data against post-load extracts.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# Add your ANTHROPIC_API_KEY to .env (optional — app works without it)
streamlit run app.py
```

## Workflow

1. **Upload** — Source LTMC file + Post-Load Extract (any filenames)
2. **Map Fields** — AI-assisted mapping with manual override + memory
3. **Run Validation** — Composite key join, field-level comparison
4. **Results** — Dashboard + Excel/HTML/CSV export

## Files

```
app.py                  Main Streamlit app
core/
  ltmc_parser.py        SAP LTMC SpreadsheetML XML parser
  post_load_parser.py   Post-load extract reader
  object_detector.py    SAP object type detection
  ai_mapping.py         AI + fallback field mapping
  mapping_memory.py     Persistent user corrections
  validation_engine.py  Core comparison engine
  key_detector.py       Composite key detection
  report_generator.py   Excel + HTML reports
ui/
  upload_page.py        File upload UI
  mapping_page.py       Field mapping + join keys UI
  validation_page.py    Settings + run UI
  dashboard_page.py     Results dashboard UI
  components.py         Shared UI components
data/
  sap_field_dictionary.json   SAP field labels
  field_aliases.json           Cross-name mappings
  mapping_memory.json          Saved user mappings (auto-created)
```
