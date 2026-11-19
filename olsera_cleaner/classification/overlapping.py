"""Audits overlapping, redundant, and granular classification taxonomy clusters."""

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# Mapping of known redundant or overlapping taxonomy clusters in Olsera
# Primary target is the canonical/larger cluster, secondary is the redundant/smaller cluster
REDUNDANCY_PAIRS = [
    {
        "cluster_name": "Ejaan Spasi: ALAT MANDI vs ALATMANDI",
        "canonical_name": "ALAT MANDI",
        "canonical_id": 602036,
        "redundant_name": "ALATMANDI",
        "redundant_id": 595877,
        "anomaly_type": "SYNTAX_REDUNDANCY",
        "explanation": "Standardisasi ejaan spasi: 'ALATMANDI' disarankan disatukan ke 'ALAT MANDI'.",
    },
    {
        "cluster_name": "Ejaan Fonetik: CHIKI vs CIKI",
        "canonical_name": "CHIKI",
        "canonical_id": 675538,
        "redundant_name": "CIKI",
        "redundant_id": 596210,
        "anomaly_type": "PHONETIC_REDUNDANCY",
        "explanation": "Standardisasi ejaan fonetik snack: 'CIKI' disarankan disatukan ke 'CHIKI'.",
    },
]

# Granular keyword patterns for soap/detergent and beverage clusters
DETERGENT_KEYWORDS = re.compile(r"\b(RINSO|DAIA|ATTACK|SO\s*KLIN\s*DETERGENT|BOOM|DETERJEN|DETERGENT)\b", re.IGNORECASE)
DISH_SOAP_KEYWORDS = re.compile(r"\b(SUNLIGHT|MAMA\s*LEMON|ECONOMY|CUCI\s*PIRING)\b", re.IGNORECASE)
FLOOR_SOAP_KEYWORDS = re.compile(r"\b(WIPOL|SUPER\s*PELL|SO\s*KLIN\s*LANTAI|SOS\s*LANTAI|PEMBERSIH\s*LANTAI)\b", re.IGNORECASE)
FRAGRANCE_KEYWORDS = re.compile(r"\b(DOWNY|MOLTO|SO\s*KLIN\s*PEWANGI|PEWANGI\s*PAKAIAN|SOFTENER)\b", re.IGNORECASE)
BODY_SOAP_KEYWORDS = re.compile(r"\b(LUX|LIFEBUOY|GIV|SHINZUI|DETTOL|BIORE|NUVO|SABUN\s*MANDI|SABUN\s*BATANG)\b", re.IGNORECASE)

POWDER_DRINK_KEYWORDS = re.compile(r"\b(BUBUK|SACHET|RENCENG|SEDUH|NUTRISARI|JASJUS|MARIMAS|POP\s*ICE|TEH\s*TARIK|KOPI\s*BUBUK|CHOCOLATOS\s*DRINK|EXTRA\s*JOSS|KUKUBIMA)\b", re.IGNORECASE)
BOX_DRINK_KEYWORDS = re.compile(r"\b(KEMASAN\s*DUS|KEMASAN\s*KARTON|KOTAK|TETRA\s*PAK|TEH\s*KOTAK|ULTRA\s*MILK\s*1L|BUAVITA\s*1L)\b", re.IGNORECASE)


@dataclass
class RedundancySummary:
    cluster_name: str
    canonical_id: int
    canonical_name: str
    canonical_count: int
    redundant_id: int
    redundant_name: str
    redundant_count: int
    recommendation: str


def find_taxonomy_redundancies(con) -> List[RedundancySummary]:
    """Find known redundant and duplicate taxonomy pairs across the catalog."""
    results: List[RedundancySummary] = []
    for pair in REDUNDANCY_PAIRS:
        c_count = con.execute("SELECT COUNT(*) FROM products_current WHERE klasifikasi_id = ?", (pair["canonical_id"],)).fetchone()[0]
        r_count = con.execute("SELECT COUNT(*) FROM products_current WHERE klasifikasi_id = ?", (pair["redundant_id"],)).fetchone()[0]
        if c_count > 0 or r_count > 0:
            results.append(
                RedundancySummary(
                    cluster_name=pair["cluster_name"],
                    canonical_id=pair["canonical_id"],
                    canonical_name=pair["canonical_name"],
                    canonical_count=c_count,
                    redundant_id=pair["redundant_id"],
                    redundant_name=pair["redundant_name"],
                    redundant_count=r_count,
                    recommendation=f"Konsolidasikan {r_count} produk dari '{pair['redundant_name']}' ke '{pair['canonical_name']}' (ID {pair['canonical_id']}).",
                )
            )
    return results


def check_granular_misclassification(prod: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Rule-based check for granular soap/cleaning and beverage misclassifications."""
    name = str(prod.get("name") or "").upper()
    curr_id = int(prod.get("klasifikasi_id") or 0)
    curr_name = str(prod.get("klasifikasi") or "").upper().strip()

    # 1. Soap / Detergent / Cleaner Granularity
    # Klasifikasi: SABUN CUCI KAMPER (ID 596205) often contains actual detergents, dish soaps, or floor cleaners
    if curr_id == 596205 or curr_name == "SABUN CUCI KAMPER":
        if DISH_SOAP_KEYWORDS.search(name):
            return {
                "suggested_klasifikasi_id": 1441865,
                "suggested_klasifikasi": "SABUN CUCI PIRING",
                "anomaly_type": "GRANULARITY_REFINEMENT",
                "confidence_score": 0.90,
                "confidence_level": "HIGH",
                "reason": "Produk pencuci piring (Sunlight/Mama Lemon) lebih tepat masuk ke 'SABUN CUCI PIRING' daripada 'SABUN CUCI KAMPER'.",
            }
        if DETERGENT_KEYWORDS.search(name):
            return {
                "suggested_klasifikasi_id": 595875,
                "suggested_klasifikasi": "DETERGENT",
                "anomaly_type": "GRANULARITY_REFINEMENT",
                "confidence_score": 0.90,
                "confidence_level": "HIGH",
                "reason": "Produk deterjen pakaian (Rinso/Daia/Attack) lebih tepat masuk ke 'DETERGENT' daripada 'SABUN CUCI KAMPER'.",
            }
        if FLOOR_SOAP_KEYWORDS.search(name):
            return {
                "suggested_klasifikasi_id": 1441864,
                "suggested_klasifikasi": "SABUN LANTAI",
                "anomaly_type": "GRANULARITY_REFINEMENT",
                "confidence_score": 0.90,
                "confidence_level": "HIGH",
                "reason": "Pembersih lantai (Wipol/Super Pell) lebih tepat masuk ke 'SABUN LANTAI' daripada 'SABUN CUCI KAMPER'.",
            }
        if FRAGRANCE_KEYWORDS.search(name):
            return {
                "suggested_klasifikasi_id": 595878,
                "suggested_klasifikasi": "PEWANGI",
                "anomaly_type": "GRANULARITY_REFINEMENT",
                "confidence_score": 0.90,
                "confidence_level": "HIGH",
                "reason": "Pewangi/pelembut pakaian (Downy/Molto) lebih tepat masuk ke 'PEWANGI' daripada 'SABUN CUCI KAMPER'.",
            }

    # 2. Body soap in generic ALAT MANDI / ALATMANDI
    if curr_id in (602036, 595877) or curr_name in ("ALAT MANDI", "ALATMANDI"):
        if BODY_SOAP_KEYWORDS.search(name) and "SIKAT" not in name and "HANDUK" not in name:
            return {
                "suggested_klasifikasi_id": 1015829,
                "suggested_klasifikasi": "SABUN MANDI",
                "anomaly_type": "GRANULARITY_REFINEMENT",
                "confidence_score": 0.85,
                "confidence_level": "HIGH",
                "reason": "Sabun mandi badan spesifik (Lux/Lifebuoy/Giv) lebih tepat masuk ke 'SABUN MANDI' daripada perlengkapan umum.",
            }

    # 3. Powder drink in generic MINUMAN
    if curr_id == 596207 or curr_name == "MINUMAN":
        if POWDER_DRINK_KEYWORDS.search(name):
            return {
                "suggested_klasifikasi_id": 596209,
                "suggested_klasifikasi": "MINUMAN BUBUK",
                "anomaly_type": "GRANULARITY_REFINEMENT",
                "confidence_score": 0.85,
                "confidence_level": "HIGH",
                "reason": "Minuman seduh/sachet/bubuk (Nutrisari/Jasjus/Kopi) lebih tepat masuk ke 'MINUMAN BUBUK'.",
            }

    # 4. Catch-all LAINLAIN / CLOSING items
    if curr_name in ("LAINLAIN", "LAIN-LAIN"):
        if "AMPLOP" in name:
            return {
                "suggested_klasifikasi_id": 1441869,  # ALAT TULIS
                "suggested_klasifikasi": "ALAT TULIS",
                "anomaly_type": "CATCHALL_RECLASSIFICATION",
                "confidence_score": 0.85,
                "confidence_level": "HIGH",
                "reason": "Amplop berada di klasifikasi catch-all 'LAINLAIN'; direkomendasikan ke 'ALAT TULIS'.",
            }

    return None
