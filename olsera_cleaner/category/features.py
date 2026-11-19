"""Feature extraction and text normalization for product classification."""

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

_UNIT_PATTERN = re.compile(
    r"\b(\d+(?:[.,]\d+)?)\s*(GR|GRAM|G|KG|ML|L|LITER|PCS|PC|SACHET|SCT|PACK|BTL|BOTOL|CAN|KALENG|DUS|BOX)\b",
    re.IGNORECASE,
)
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+", re.UNICODE)

# Common Indonesian stop words in retail titles that add little discriminatory value across categories
_STOP_WORDS: Set[str] = {
    "dan",
    "atau",
    "dengan",
    "untuk",
    "yg",
    "yang",
    "di",
    "ke",
    "dari",
    "isi",
    "netto",
    "berat",
    "free",
    "gratis",
    "promo",
    "diskon",
    "asli",
    "original",
    "ori",
}


@dataclass
class ProductFeatures:
    product_id: int
    raw_name: str
    normalized_name: str
    tokens: List[str]
    bigrams: List[str]
    brand: Optional[str]
    klasifikasi: Optional[str]
    uom: Optional[str]
    unit_size: Optional[str]
    sku: Optional[str]
    barcode: Optional[str]

    @property
    def feature_tokens(self) -> List[str]:
        items: List[str] = list(self.tokens)
        items.extend(self.bigrams)
        if self.brand:
            items.extend([t.lower() for t in _TOKEN_PATTERN.findall(self.brand) if t.lower() not in _STOP_WORDS])
        if self.klasifikasi:
            k_toks = [t.lower() for t in _TOKEN_PATTERN.findall(self.klasifikasi) if t.lower() not in _STOP_WORDS]
            items.extend(k_toks * 2)
        return items


def normalize_text(text: str) -> str:
    """Normalize text into clean uppercase representation for deterministic matching."""
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", text)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized.upper()


def extract_features(row: Dict[str, Any]) -> ProductFeatures:
    """Extract structured features from a product dictionary."""
    pid = int(row.get("olsera_product_id") or row.get("id") or 0)
    raw_name = str(row.get("name") or "")
    norm_name = normalize_text(raw_name)

    # Tokenize
    raw_tokens = [t.lower() for t in _TOKEN_PATTERN.findall(norm_name)]
    clean_tokens = [t for t in raw_tokens if t not in _STOP_WORDS and len(t) > 1]

    # Bigrams
    bigrams = [f"{clean_tokens[i]}_{clean_tokens[i+1]}" for i in range(len(clean_tokens) - 1)]

    # Unit / size
    unit_match = _UNIT_PATTERN.search(norm_name)
    unit_size = unit_match.group(0).upper() if unit_match else None

    brand = (row.get("brand_name") or "").strip() or None
    klasifikasi = (row.get("klasifikasi") or "").strip() or None
    uom = (row.get("uom") or "").strip() or None
    sku = (row.get("sku") or "").strip() or None
    barcode = (row.get("barcode") or "").strip() or None

    return ProductFeatures(
        product_id=pid,
        raw_name=raw_name,
        normalized_name=norm_name,
        tokens=clean_tokens,
        bigrams=bigrams,
        brand=brand,
        klasifikasi=klasifikasi,
        uom=uom,
        unit_size=unit_size,
        sku=sku,
        barcode=barcode,
    )
