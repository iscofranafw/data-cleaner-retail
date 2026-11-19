"""Layer B: Pure-Python TF-IDF & Nearest-Neighbor Pattern Learner for categories."""

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from .discovery import CATCH_ALL_NAMES, DiscoverySummary
from .features import ProductFeatures


@dataclass
class MatchResult:
    predicted_category_id: int
    predicted_category_name: str
    similarity_score: float
    comparison_product_id: Optional[int]
    comparison_product_name: Optional[str]
    comparison_similarity: float
    is_ambiguous: bool
    runner_up_category_id: Optional[int] = None
    runner_up_similarity: float = 0.0


class CategoryPatternClassifier:
    """Learns category token patterns and evaluates product nearest neighbors."""

    def __init__(self, discovery: DiscoverySummary):
        self.discovery = discovery
        self.doc_count = 0
        self.idf: Dict[str, float] = {}
        # Inverted index: token -> list of (doc_index, weight)
        self.inverted_index: Dict[str, List[Tuple[int, float]]] = defaultdict(list)
        # Stored product metadata
        self.docs: List[ProductFeatures] = []
        self.doc_cat_ids: List[Optional[int]] = []
        self.doc_vectors: List[Dict[str, float]] = []
        # Category centroids: cat_id -> unit vector
        self.centroids: Dict[int, Dict[str, float]] = {}
        # Non-catch-all specific categories that serve as reliable targets
        self.target_category_ids: Set[int] = {
            cid for cid, meta in discovery.categories.items()
            if not meta.is_catch_all and not meta.is_low_sample
        }
        # If all categories happen to be low sample or catch-all, keep all valid non-catch-all
        if not self.target_category_ids:
            self.target_category_ids = {
                cid for cid, meta in discovery.categories.items()
                if not meta.is_catch_all
            }
        # If still empty (e.g., catalog only has 'Others'), keep all valid
        if not self.target_category_ids:
            self.target_category_ids = set(discovery.valid_category_ids)

    def train(self, items: List[Tuple[ProductFeatures, Optional[int]]]) -> "CategoryPatternClassifier":
        """Index training corpus and compute category centroid profiles."""
        self.docs = [item[0] for item in items]
        self.doc_cat_ids = [item[1] for item in items]
        self.doc_count = len(self.docs)

        if self.doc_count == 0:
            return self

        df: Dict[str, int] = Counter()
        for feat in self.docs:
            unique_terms = set(feat.feature_tokens)
            for t in unique_terms:
                df[t] += 1

        for t, freq in df.items():
            self.idf[t] = math.log((1.0 + self.doc_count) / (1.0 + freq)) + 1.0

        self.doc_vectors = []
        category_accumulators: Dict[int, Counter] = defaultdict(Counter)

        for idx, feat in enumerate(self.docs):
            cat_id = self.doc_cat_ids[idx]
            counts = Counter(feat.feature_tokens)
            raw_vec: Dict[str, float] = {}
            for t, cnt in counts.items():
                tfidf = (1.0 + math.log(cnt)) * self.idf.get(t, 1.0)
                raw_vec[t] = tfidf

            norm = math.sqrt(sum(v * v for v in raw_vec.values())) or 1.0
            unit_vec = {t: v / norm for t, v in raw_vec.items()}
            self.doc_vectors.append(unit_vec)

            for t, weight in unit_vec.items():
                self.inverted_index[t].append((idx, weight))

            # Accumulate into target category centroids, ignoring generic catch-all labels
            if cat_id is not None and cat_id in self.target_category_ids:
                for t, w in unit_vec.items():
                    category_accumulators[cat_id][t] += w

        for cat_id, acc in category_accumulators.items():
            norm = math.sqrt(sum(v * v for v in acc.values())) or 1.0
            self.centroids[cat_id] = {t: v / norm for t, v in acc.items()}

        return self

    def _vectorize(self, feat: ProductFeatures) -> Dict[str, float]:
        """Convert a single product's features to a normalized TF-IDF vector."""
        counts = Counter(feat.feature_tokens)
        raw_vec: Dict[str, float] = {}
        for t, cnt in counts.items():
            if t in self.idf:
                tfidf = (1.0 + math.log(cnt)) * self.idf[t]
                raw_vec[t] = tfidf
        norm = math.sqrt(sum(v * v for v in raw_vec.values())) or 1.0
        return {t: v / norm for t, v in raw_vec.items()}

    def predict(self, feat: ProductFeatures, current_cat_id: Optional[int]) -> Optional[MatchResult]:
        """Predict best category using centroid similarity and nearest neighbor evidence."""
        if not self.target_category_ids or not self.centroids:
            return None

        q_vec = self._vectorize(feat)
        if not q_vec:
            return None

        cat_scores: List[Tuple[int, float]] = []
        for cat_id in self.target_category_ids:
            centroid = self.centroids.get(cat_id)
            if not centroid:
                continue
            sim = sum(w * centroid[t] for t, w in q_vec.items() if t in centroid)
            cat_scores.append((cat_id, sim))

        cat_scores.sort(key=lambda x: x[1], reverse=True)
        if not cat_scores:
            return None

        top_cat_id, top_centroid_sim = cat_scores[0]
        runner_up_id = cat_scores[1][0] if len(cat_scores) > 1 else None
        runner_up_sim = cat_scores[1][1] if len(cat_scores) > 1 else 0.0

        doc_dot_products: Dict[int, float] = Counter()
        for t, q_weight in q_vec.items():
            for doc_idx, doc_weight in self.inverted_index.get(t, []):
                if self.docs[doc_idx].product_id == feat.product_id:
                    continue
                doc_dot_products[doc_idx] += q_weight * doc_weight

        # Filter candidate neighbors in the candidate target category
        best_neighbor_id: Optional[int] = None
        best_neighbor_name: Optional[str] = None
        best_neighbor_sim: float = 0.0

        if doc_dot_products:
            sorted_neighbors = sorted(doc_dot_products.items(), key=lambda x: x[1], reverse=True)
            for doc_idx, sim in sorted_neighbors:
                neighbor_cat = self.doc_cat_ids[doc_idx]
                # Look for nearest neighbor inside the target category
                if neighbor_cat == top_cat_id:
                    best_neighbor_id = self.docs[doc_idx].product_id
                    best_neighbor_name = self.docs[doc_idx].raw_name
                    best_neighbor_sim = round(sim, 4)
                    break

            # Fallback to absolute closest neighbor regardless of category for comparison
            if not best_neighbor_name and sorted_neighbors:
                top_doc_idx, top_sim = sorted_neighbors[0]
                best_neighbor_id = self.docs[top_doc_idx].product_id
                best_neighbor_name = self.docs[top_doc_idx].raw_name
                best_neighbor_sim = round(top_sim, 4)

        # Combined similarity score: weighted blend of centroid and nearest neighbor
        if best_neighbor_sim > 0:
            combined_sim = round(0.4 * top_centroid_sim + 0.6 * best_neighbor_sim, 4)
        else:
            combined_sim = round(top_centroid_sim, 4)

        # Ambiguity check: close second category or low margin
        margin = top_centroid_sim - runner_up_sim
        is_ambiguous = combined_sim < 0.40 or (margin < 0.08 and runner_up_sim > 0.25)

        cat_meta = self.discovery.categories.get(top_cat_id)
        cat_name = cat_meta.name if cat_meta else f"Category {top_cat_id}"

        return MatchResult(
            predicted_category_id=top_cat_id,
            predicted_category_name=cat_name,
            similarity_score=combined_sim,
            comparison_product_id=best_neighbor_id,
            comparison_product_name=best_neighbor_name,
            comparison_similarity=best_neighbor_sim,
            is_ambiguous=is_ambiguous,
            runner_up_category_id=runner_up_id,
            runner_up_similarity=round(runner_up_sim, 4),
        )
