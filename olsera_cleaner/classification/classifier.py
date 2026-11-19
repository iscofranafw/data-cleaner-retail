"""Data-driven Adaptive Multi-Class Classifier with sub-word ngrams, verified centroids, and open-set detection."""

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from .registry import TaxonomySnapshot
from olsera_cleaner.category.features import ProductFeatures


@dataclass
class AdaptiveKlasifikasiMatchResult:
    predicted_klasifikasi_id: Optional[int]
    predicted_klasifikasi_name: str
    similarity_score: float
    comparison_product_id: Optional[int]
    comparison_product_name: Optional[str]
    comparison_similarity: float
    is_ambiguous: bool
    is_unknown: bool
    runner_up_klasifikasi_id: Optional[int] = None
    runner_up_similarity: float = 0.0


def _generate_tokens_and_ngrams(text: str) -> List[str]:
    """Generate normalized word tokens and character 3-4 n-grams for subword robustness."""
    words = re.findall(r"[A-Za-z0-9]{2,}", (text or "").lower())
    features: List[str] = list(words)
    for w in words:
        if len(w) >= 4:
            for n in (3, 4):
                for i in range(len(w) - n + 1):
                    features.append(f"<{w[i:i+n]}>")
    return features


class AdaptiveKlasifikasiClassifier:
    """Adaptive multi-class text model learning dynamically from taxonomy snapshots and verified ground truth."""

    def __init__(
        self,
        snapshot: TaxonomySnapshot,
        open_set_threshold: float = 0.28,
        negative_feedback_map: Optional[Dict[int, Set[int]]] = None,
    ):
        self.snapshot = snapshot
        self.open_set_threshold = open_set_threshold
        self.negative_feedback_map = negative_feedback_map or {}

        self.doc_count = 0
        self.idf: Dict[str, float] = {}
        self.inverted_index: Dict[str, List[Tuple[int, float]]] = defaultdict(list)
        self.docs: List[ProductFeatures] = []
        self.doc_kla_ids: List[int] = []
        self.doc_vectors: List[Dict[str, float]] = []
        self.centroids: Dict[int, Dict[str, float]] = {}

        # Target classes for prediction: exclude catch-all generic buckets from target centroids
        self.target_klasifikasi_ids: Set[int] = {
            kid for kid, meta in snapshot.klasifikasi_map.items()
            if not meta.is_catch_all
        }
        if not self.target_klasifikasi_ids:
            self.target_klasifikasi_ids = set(snapshot.valid_ids)

    def train(self, items: List[Tuple[ProductFeatures, int, bool]]) -> "AdaptiveKlasifikasiClassifier":
        """Build vocabulary, inverted index, and centroid vectors dynamically.

        Args:
            items: List of (ProductFeatures, klasifikasi_id, is_verified_ground_truth).
        """
        self.docs = [item[0] for item in items]
        self.doc_kla_ids = [item[1] for item in items]
        self.doc_count = len(self.docs)

        if self.doc_count == 0:
            return self

        # Term frequency & document frequency across word & sub-word features
        doc_feature_tokens: List[List[str]] = []
        df: Counter = Counter()

        for idx, feat in enumerate(self.docs):
            brand_val = getattr(feat, "brand", getattr(feat, "brand_name", "")) or ""
            uom_val = getattr(feat, "uom", "") or ""
            full_text = f"{feat.raw_name} {brand_val} {uom_val}"
            tokens = _generate_tokens_and_ngrams(full_text)
            doc_feature_tokens.append(tokens)
            unique_terms = set(tokens)
            for t in unique_terms:
                df[t] += 1

        for t, freq in df.items():
            self.idf[t] = math.log((1.0 + self.doc_count) / (1.0 + freq)) + 1.0

        self.doc_vectors = []
        class_accumulators: Dict[int, Counter] = defaultdict(Counter)

        for idx, tokens in enumerate(doc_feature_tokens):
            kid = self.doc_kla_ids[idx]
            is_verified = items[idx][2] if len(items[idx]) > 2 else False
            sample_weight = 2.5 if is_verified else 1.0

            counts = Counter(tokens)
            raw_vec: Dict[str, float] = {}
            for t, cnt in counts.items():
                raw_vec[t] = (1.0 + math.log(cnt)) * self.idf.get(t, 1.0)

            norm = math.sqrt(sum(v * v for v in raw_vec.values())) or 1.0
            unit_vec = {t: (v / norm) for t, v in raw_vec.items()}
            self.doc_vectors.append(unit_vec)

            for t, weight in unit_vec.items():
                self.inverted_index[t].append((idx, weight))

            # Accumulate into target classification centroid
            if kid in self.target_klasifikasi_ids:
                for t, w in unit_vec.items():
                    class_accumulators[kid][t] += w * sample_weight

        for kid, acc in class_accumulators.items():
            norm = math.sqrt(sum(v * v for v in acc.values())) or 1.0
            self.centroids[kid] = {t: (v / norm) for t, v in acc.items()}

        return self

    def _vectorize(self, feat: ProductFeatures) -> Dict[str, float]:
        """Convert product features into normalized TF-IDF vector."""
        brand_val = getattr(feat, "brand", getattr(feat, "brand_name", "")) or ""
        uom_val = getattr(feat, "uom", "") or ""
        full_text = f"{feat.raw_name} {brand_val} {uom_val}"
        tokens = _generate_tokens_and_ngrams(full_text)
        counts = Counter(tokens)
        raw_vec: Dict[str, float] = {}
        for t, cnt in counts.items():
            if t in self.idf:
                raw_vec[t] = (1.0 + math.log(cnt)) * self.idf[t]
        norm = math.sqrt(sum(v * v for v in raw_vec.values())) or 1.0
        return {t: (v / norm) for t, v in raw_vec.items()}

    def _find_nearest_neighbor(
        self,
        q_vec: Dict[str, float],
        feat: ProductFeatures,
        top_kid: int,
    ) -> Tuple[Optional[int], Optional[str], float]:
        """Find the nearest exemplar product in the inverted index."""
        doc_dot_products: Counter = Counter()
        for t, q_weight in q_vec.items():
            for doc_idx, doc_weight in self.inverted_index.get(t, []):
                if self.docs[doc_idx].product_id == feat.product_id:
                    continue
                doc_dot_products[doc_idx] += q_weight * doc_weight

        if not doc_dot_products:
            return None, None, 0.0

        sorted_neighbors = sorted(doc_dot_products.items(), key=lambda x: x[1], reverse=True)
        for doc_idx, sim in sorted_neighbors:
            if self.doc_kla_ids[doc_idx] == top_kid:
                return self.docs[doc_idx].product_id, self.docs[doc_idx].raw_name, round(sim, 4)

        top_doc_idx, top_sim = sorted_neighbors[0]
        return self.docs[top_doc_idx].product_id, self.docs[top_doc_idx].raw_name, round(top_sim, 4)

    def predict(self, feat: ProductFeatures, current_kla_id: Optional[int]) -> AdaptiveKlasifikasiMatchResult:
        """Predict the best classification or flag as unknown / ambiguous."""
        q_vec = self._vectorize(feat)
        if not q_vec or not self.centroids:
            return AdaptiveKlasifikasiMatchResult(
                predicted_klasifikasi_id=None,
                predicted_klasifikasi_name="UNKNOWN",
                similarity_score=0.0,
                comparison_product_id=None,
                comparison_product_name=None,
                comparison_similarity=0.0,
                is_ambiguous=False,
                is_unknown=True,
            )

        rejected_ids = self.negative_feedback_map.get(feat.product_id, set())

        scores: List[Tuple[int, float]] = []
        for kid in self.target_klasifikasi_ids:
            if kid in rejected_ids:
                continue
            centroid = self.centroids.get(kid)
            if not centroid:
                continue
            sim = sum(w * centroid[t] for t, w in q_vec.items() if t in centroid)
            scores.append((kid, sim))

        scores.sort(key=lambda x: x[1], reverse=True)
        if not scores:
            return AdaptiveKlasifikasiMatchResult(
                predicted_klasifikasi_id=None,
                predicted_klasifikasi_name="UNKNOWN",
                similarity_score=0.0,
                comparison_product_id=None,
                comparison_product_name=None,
                comparison_similarity=0.0,
                is_ambiguous=False,
                is_unknown=True,
            )

        top_kid, top_centroid_sim = scores[0]
        runner_up_kid = scores[1][0] if len(scores) > 1 else None
        runner_up_sim = scores[1][1] if len(scores) > 1 else 0.0

        best_neighbor_id, best_neighbor_name, best_neighbor_sim = self._find_nearest_neighbor(q_vec, feat, top_kid)
        combined_sim = round(0.40 * top_centroid_sim + 0.60 * best_neighbor_sim, 4) if best_neighbor_sim > 0 else round(top_centroid_sim, 4)

        # Open-set classification check: does not fit any existing category
        if combined_sim < self.open_set_threshold:
            return AdaptiveKlasifikasiMatchResult(
                predicted_klasifikasi_id=None,
                predicted_klasifikasi_name="UNKNOWN",
                similarity_score=combined_sim,
                comparison_product_id=best_neighbor_id,
                comparison_product_name=best_neighbor_name,
                comparison_similarity=best_neighbor_sim,
                is_ambiguous=False,
                is_unknown=True,
                runner_up_klasifikasi_id=top_kid,
                runner_up_similarity=combined_sim,
            )

        margin = top_centroid_sim - runner_up_sim
        is_ambiguous = combined_sim < 0.42 or (margin < 0.07 and runner_up_sim > 0.28)

        meta = self.snapshot.klasifikasi_map.get(top_kid)
        kla_name = meta.name if meta else f"Klasifikasi {top_kid}"

        return AdaptiveKlasifikasiMatchResult(
            predicted_klasifikasi_id=top_kid,
            predicted_klasifikasi_name=kla_name,
            similarity_score=combined_sim,
            comparison_product_id=best_neighbor_id,
            comparison_product_name=best_neighbor_name,
            comparison_similarity=best_neighbor_sim,
            is_ambiguous=is_ambiguous,
            is_unknown=False,
            runner_up_klasifikasi_id=runner_up_kid,
            runner_up_similarity=round(runner_up_sim, 4),
        )


# Backward compatibility aliases
MultiClassKlasifikasiClassifier = AdaptiveKlasifikasiClassifier
KlasifikasiMatchResult = AdaptiveKlasifikasiMatchResult
