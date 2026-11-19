"""Open-set classification and dynamic discovery of new candidate product classes."""

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, List, Set

from .registry import TaxonomySnapshot, normalize_taxonomy_name


@dataclass
class NewClassCandidateProduct:
    product_id: int
    name: str
    tokens: Set[str]
    max_known_similarity: float


@dataclass
class ProposedNewClass:
    proposed_name: str
    item_count: int
    product_ids: List[int]
    product_names: List[str]
    top_tokens: List[str]
    confidence_score: float
    explanation: str


STOPWORDS = {
    "dan", "atau", "di", "ke", "dari", "yang", "untuk", "dengan", "pada", "oleh",
    "isi", "dus", "pcs", "bks", "gr", "gram", "ml", "liter", "kg", "pack", "btl",
    "bag", "botol", "sachet", "rcg", "box", "1l", "1kg", "500ml", "100g", "200g",
}


def _extract_product_tokens(name: str) -> Set[str]:
    """Extract clean content tokens from product name."""
    words = re.findall(r"[A-Za-z0-9]{3,}", (name or "").lower())
    return {w for w in words if w not in STOPWORDS and not w.isdigit()}


def _jaccard_token_similarity(t1: Set[str], t2: Set[str]) -> float:
    """Calculate Jaccard similarity between two token sets."""
    if not t1 or not t2:
        return 0.0
    intersection = len(t1 & t2)
    union = len(t1 | t2)
    return intersection / union if union > 0 else 0.0


def cluster_open_set_products(
    candidates: List[NewClassCandidateProduct],
    snapshot: TaxonomySnapshot,
    min_cluster_size: int = 2,
    similarity_threshold: float = 0.35,
) -> List[ProposedNewClass]:
    """Group unclassified products into coherent candidate new classification clusters."""
    if len(candidates) < min_cluster_size:
        return []

    # Build adjacency graph of candidate items
    n = len(candidates)
    adj: Dict[int, List[int]] = defaultdict(list)
    for i in range(n):
        for j in range(i + 1, n):
            sim = _jaccard_token_similarity(candidates[i].tokens, candidates[j].tokens)
            common_count = len(candidates[i].tokens & candidates[j].tokens)
            if sim >= similarity_threshold or common_count >= 2:
                adj[i].append(j)
                adj[j].append(i)

    visited: Set[int] = set()
    proposals: List[ProposedNewClass] = []

    for i in range(n):
        if i in visited:
            continue

        # BFS / DFS connected component
        component = []
        queue = [i]
        visited.add(i)

        while queue:
            curr = queue.pop(0)
            component.append(curr)
            for neighbor in adj[curr]:
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)

        if len(component) < min_cluster_size:
            continue

        comp_items = [candidates[idx] for idx in component]
        token_counter: Counter = Counter()
        for item in comp_items:
            for t in item.tokens:
                token_counter[t] += 1

        # Extract top 2 most representative words
        top_words = [word for word, count in token_counter.most_common(4) if count >= min_cluster_size]
        if not top_words:
            top_words = [token_counter.most_common(1)[0][0]] if token_counter else ["BARU"]

        raw_proposed_name = " ".join(top_words[:2]).upper()
        norm_proposed = normalize_taxonomy_name(raw_proposed_name)

        # Safety check: prevent duplicate against existing taxonomy
        is_duplicate = (
            norm_proposed in snapshot.norm_to_ids
            or any(norm_proposed in k.normalized_name for k in snapshot.klasifikasi_map.values())
        )
        if is_duplicate:
            continue

        p_ids = [item.product_id for item in comp_items]
        p_names = [item.name for item in comp_items]

        proposals.append(
            ProposedNewClass(
                proposed_name=raw_proposed_name,
                item_count=len(comp_items),
                product_ids=p_ids,
                product_names=p_names,
                top_tokens=top_words,
                confidence_score=round(min(0.85, 0.50 + 0.10 * len(comp_items)), 2),
                explanation=(
                    f"Ditemukan kelompok {len(comp_items)} produk dengan kemiripan kata '{', '.join(top_words)}' "
                    f"yang tidak cocok dengan klasifikasi yang ada."
                ),
            )
        )

    proposals.sort(key=lambda x: x.item_count, reverse=True)
    return proposals


def persist_new_class_proposals(con, proposals: List[ProposedNewClass]) -> int:
    """Store open-set new class proposals in cleaner_classification_new_class_proposals."""
    inserted = 0
    for p in proposals:
        existing = con.execute(
            "SELECT id FROM cleaner_classification_new_class_proposals WHERE proposed_name = ? AND status = 'PENDING'",
            (p.proposed_name,),
        ).fetchone()
        if existing:
            con.execute(
                """
                UPDATE cleaner_classification_new_class_proposals
                SET item_count = ?, sample_product_ids_json = ?, sample_product_names_json = ?,
                    top_tokens_json = ?, confidence_score = ?
                WHERE id = ?
                """,
                (
                    p.item_count,
                    json.dumps(p.product_ids[:20]),
                    json.dumps(p.product_names[:10]),
                    json.dumps(p.top_tokens),
                    p.confidence_score,
                    existing[0],
                ),
            )
        else:
            con.execute(
                """
                INSERT INTO cleaner_classification_new_class_proposals
                    (proposed_name, item_count, sample_product_ids_json, sample_product_names_json,
                     top_tokens_json, confidence_score, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'PENDING', CURRENT_TIMESTAMP)
                """,
                (
                    p.proposed_name,
                    p.item_count,
                    json.dumps(p.product_ids[:20]),
                    json.dumps(p.product_names[:10]),
                    json.dumps(p.top_tokens),
                    p.confidence_score,
                ),
            )
            inserted += 1
    return inserted
