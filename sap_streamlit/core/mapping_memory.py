"""
Mapping Memory — persistent local storage for user-corrected field mappings.
Saves to data/mapping_memory.json and reuses on next run.
"""
import json
from pathlib import Path
from typing import Dict, Optional

_MEMORY_FILE = Path(__file__).parent.parent / "data" / "mapping_memory.json"


def _load() -> dict:
    if _MEMORY_FILE.exists():
        try:
            return json.loads(_MEMORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save(data: dict):
    _MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    _MEMORY_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_remembered_mapping(ltmc_col: str, sap_object: str = "") -> Optional[str]:
    """Return previously saved mapping for an LTMC column."""
    mem = _load()
    key = f"{sap_object}|{ltmc_col}".upper()
    if key in mem:
        return mem[key]["postload_col"]
    # Try without object prefix
    key2 = f"|{ltmc_col}".upper()
    if key2 in mem:
        return mem[key2]["postload_col"]
    return None


def save_mapping(ltmc_col: str, postload_col: str, sap_object: str = "",
                 source: str = "user"):
    """Persist a confirmed/corrected mapping."""
    mem  = _load()
    key  = f"{sap_object}|{ltmc_col}".upper()
    mem[key] = {
        "ltmc_col":    ltmc_col.upper(),
        "postload_col": postload_col,
        "sap_object":  sap_object,
        "source":      source,
    }
    _save(mem)


def save_bulk(mappings: Dict[str, str], sap_object: str = "", source: str = "user"):
    """Save multiple mappings at once."""
    for ltmc_col, postload_col in mappings.items():
        if postload_col:
            save_mapping(ltmc_col, postload_col, sap_object, source)


def get_all_memories() -> list:
    """Return all saved mappings as a list."""
    mem = _load()
    return [
        {**v, "key": k}
        for k, v in mem.items()
    ]


def clear_memory(sap_object: str = None):
    mem = _load()
    if sap_object:
        keys = [k for k in mem if k.startswith(f"{sap_object}|".upper())]
        for k in keys:
            del mem[k]
    else:
        mem = {}
    _save(mem)
