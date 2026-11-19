"""Offline audit of the existing SQLite product snapshot (no network calls)."""
from collections import defaultdict
import re
import unicodedata
from .normalizer import normalize_name


def _key(s):
    return re.sub(r"[^A-Z0-9]", "", unicodedata.normalize("NFKC", str(s or "")).upper())


def audit_rows(rows):
    """rows: iterable dicts with olsera_product_id, name, sku, barcode, etc."""
    products = list(rows)
    findings = []
    index = defaultdict(list)
    sku_index = defaultdict(list)
    barcode_index = defaultdict(list)
    for p in products:
        pid = int(p["olsera_product_id"])
        name = str(p.get("name") or "")
        suggested, flags = normalize_name(name)
        for flag in flags:
            findings.append({"product_id": pid, "kind": flag, "severity": "REVIEW" if flag == "ABBREVIATION_AMBIGUOUS" else "LOW",
                             "old_name": name, "suggested_name": suggested, "related_ids": "", "reason": "Singkatan ambigu; perlu peninjauan" if flag == "ABBREVIATION_AMBIGUOUS" else "Huruf besar, spasi, atau satuan tidak konsisten"})
        if not name.strip():
            findings.append({"product_id":pid,"kind":"EMPTY_NAME","severity":"HIGH","old_name":name,"suggested_name":"","related_ids":"","reason":"Nama kosong"})
        normalized = _key(suggested)
        if normalized:
            index[normalized].append(pid)
        for field, table in (("sku", sku_index),("barcode",barcode_index)):
            val = str(p.get(field) or "").strip()
            if val:
                table[val].append(pid)  # Identifiers are exact; do not normalize or drop leading zeros.
    for group, kind, level in ((index,"POSSIBLE_DUPLICATE_NAME","REVIEW"),(sku_index,"DUPLICATE_SKU","HIGH"),(barcode_index,"DUPLICATE_BARCODE","HIGH")):
        for ids in group.values():
            if len(ids) < 2:
                continue
            ids = sorted(set(ids))
            if len(ids) < 2:
                continue
            # Review candidates only; no automatic merging/deletion.
            for pid in ids:
                findings.append({"product_id":pid,"kind":kind,"severity":level,"old_name": next((str(p.get("name") or "") for p in products if int(p["olsera_product_id"]) == pid),""),
                                 "suggested_name":"", "related_ids":",".join(str(x) for x in ids if x != pid), "reason":"Kandidat duplikat; periksa produk/varian/ukuran sebelum memutuskan"})
    return sorted(findings,key=lambda f:(f["product_id"],f["kind"]))
