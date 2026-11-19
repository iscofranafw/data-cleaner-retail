"""Dynamic algorithmic detection of redundant, duplicate, and overlapping taxonomy clusters."""

import re
from dataclasses import dataclass
from typing import List, Set, Tuple

from .registry import TaxonomySnapshot, normalize_taxonomy_name


def _levenshtein_distance(s1: str, s2: str) -> int:
    """Compute basic Levenshtein edit distance between two strings."""
    if s1 == s2:
        return 0
    if not s1:
        return len(s2)
    if not s2:
        return len(s1)

    prev_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (0 if c1 == c2 else 1)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row
    return prev_row[-1]


def _similarity_ratio(s1: str, s2: str) -> float:
    """Calculate normalized character similarity ratio between 0.0 and 1.0."""
    max_len = max(len(s1), len(s2))
    if max_len == 0:
        return 1.0
    dist = _levenshtein_distance(s1, s2)
    return max(0.0, 1.0 - (dist / max_len))


def _phonetic_normalize(name: str) -> str:
    """Approximate phonetic simplification for common Indonesian retail naming variations."""
    s = normalize_taxonomy_name(name)
    s = s.replace("ch", "c")
    s = s.replace("kh", "k")
    s = s.replace("dh", "d")
    s = s.replace("sy", "s")
    s = s.replace("sh", "s")
    s = s.replace("oe", "u")
    s = s.replace("tj", "c")
    s = s.replace("dj", "j")
    s = re.sub(r"(.)\1+", r"\1", s)
    return "".join(s.split())


@dataclass
class DynamicRedundancyProposal:
    cluster_name: str
    canonical_id: int
    canonical_name: str
    canonical_count: int
    redundant_id: int
    redundant_name: str
    redundant_count: int
    redundancy_type: str
    similarity_score: float
    recommendation: str


def _get_class_tokens(con, kid: int, limit: int = 40) -> Set[str]:
    """Retrieve unique non-stopword tokens from products in a given classification."""
    rows = con.execute(
        "SELECT name FROM products_current WHERE klasifikasi_id = ? LIMIT ?",
        (kid, limit),
    ).fetchall()
    tokens: Set[str] = set()
    stopwords = {"dan", "atau", "di", "ke", "dari", "yang", "untuk", "isi", "dus", "pcs", "bks", "gr", "ml"}
    for r in rows:
        words = re.findall(r"[A-Za-z0-9]{2,}", str(r[0]).lower())
        for w in words:
            if w not in stopwords:
                tokens.add(w)
    return tokens


def detect_taxonomy_redundancies(con, snapshot: TaxonomySnapshot) -> List[DynamicRedundancyProposal]:
    """Dynamically discover redundant/duplicate classifications using edit distance and token overlap."""
    classes = list(snapshot.klasifikasi_map.values())
    proposals: List[DynamicRedundancyProposal] = []
    seen_pairs: Set[Tuple[int, int]] = set()

    for i, c1 in enumerate(classes):
        for c2 in classes[i + 1:]:
            pair_key = (min(c1.klasifikasi_id, c2.klasifikasi_id), max(c1.klasifikasi_id, c2.klasifikasi_id))
            if pair_key in seen_pairs:
                continue

            # Determine canonical vs redundant by product count, then name clarity
            if c1.product_count >= c2.product_count:
                can, red = c1, c2
            else:
                can, red = c2, c1

            n1 = "".join(can.normalized_name.split())
            n2 = "".join(red.normalized_name.split())

            # 1. Exact string match after removing spaces and punctuation
            if n1 == n2 and can.klasifikasi_id != red.klasifikasi_id:
                seen_pairs.add(pair_key)
                proposals.append(
                    DynamicRedundancyProposal(
                        cluster_name=f"Variasi Spasi: {can.name} vs {red.name}",
                        canonical_id=can.klasifikasi_id,
                        canonical_name=can.name,
                        canonical_count=can.product_count,
                        redundant_id=red.klasifikasi_id,
                        redundant_name=red.name,
                        redundant_count=red.product_count,
                        redundancy_type="SYNTAX_SPACING",
                        similarity_score=1.0,
                        recommendation=f"Standardisasi ejaan spasi: satukan {red.product_count} produk dari '{red.name}' ke '{can.name}' (ID {can.klasifikasi_id}).",
                    )
                )
                continue

            # 2. Phonetic equivalence
            p1 = _phonetic_normalize(can.name)
            p2 = _phonetic_normalize(red.name)
            if p1 == p2 and len(p1) >= 3:
                seen_pairs.add(pair_key)
                proposals.append(
                    DynamicRedundancyProposal(
                        cluster_name=f"Variasi Fonetik: {can.name} vs {red.name}",
                        canonical_id=can.klasifikasi_id,
                        canonical_name=can.name,
                        canonical_count=can.product_count,
                        redundant_id=red.klasifikasi_id,
                        redundant_name=red.name,
                        redundant_count=red.product_count,
                        redundancy_type="PHONETIC_SIMILARITY",
                        similarity_score=0.95,
                        recommendation=f"Standardisasi ejaan fonetik: satukan {red.product_count} produk dari '{red.name}' ke '{can.name}' (ID {can.klasifikasi_id}).",
                    )
                )
                continue

            # 3. High edit similarity + token overlap
            sim = _similarity_ratio(can.normalized_name, red.normalized_name)
            if sim >= 0.80 and len(can.normalized_name) >= 4:
                tokens_can = _get_class_tokens(con, can.klasifikasi_id)
                tokens_red = _get_class_tokens(con, red.klasifikasi_id)
                union_len = len(tokens_can | tokens_red)
                jaccard = (len(tokens_can & tokens_red) / union_len) if union_len > 0 else 0.0

                if jaccard >= 0.10 or len(tokens_can & tokens_red) >= 2 or red.product_count < 5:
                    seen_pairs.add(pair_key)
                    proposals.append(
                        DynamicRedundancyProposal(
                            cluster_name=f"Kemiripan Nama & Produk: {can.name} vs {red.name}",
                            canonical_id=can.klasifikasi_id,
                            canonical_name=can.name,
                            canonical_count=can.product_count,
                            redundant_id=red.klasifikasi_id,
                            redundant_name=red.name,
                            redundant_count=red.product_count,
                            redundancy_type="FUZZY_SEMANTIC",
                            similarity_score=round(sim, 3),
                            recommendation=f"Konsolidasikan '{red.name}' ({red.product_count} item) ke '{can.name}' ({can.product_count} item) setelah verifikasi operator.",
                        )
                    )

    proposals.sort(key=lambda x: (x.canonical_count + x.redundant_count), reverse=True)
    return proposals
