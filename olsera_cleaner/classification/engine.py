"""Adaptive multi-class classification audit orchestrator with open-set discovery and dynamic deduplication."""

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from .classifier import AdaptiveKlasifikasiClassifier, AdaptiveKlasifikasiMatchResult
from .deduplication import DynamicRedundancyProposal, detect_taxonomy_redundancies
from .feedback import get_negative_feedback_map
from .open_set import (
    NewClassCandidateProduct,
    ProposedNewClass,
    _extract_product_tokens,
    cluster_open_set_products,
    persist_new_class_proposals,
)
from .registry import TaxonomyRegistry, TaxonomySnapshot
from .verifier import KlasifikasiAIVerifier
from olsera_cleaner.category.features import ProductFeatures, extract_features


@dataclass
class ClassificationFinding:
    product_id: int
    product_name: str
    sku: Optional[str]
    barcode: Optional[str]
    current_klasifikasi_id: int
    current_klasifikasi: str
    suggested_klasifikasi_id: int
    suggested_klasifikasi: str
    anomaly_type: str
    confidence_score: float
    confidence_level: str
    classification_method: str
    reason: str
    comparison_product_id: Optional[int]
    comparison_product_name: Optional[str]
    comparison_similarity: Optional[float]
    source_payload_hash: Optional[str]
    needs_review: bool = False


def _load_training_data(
    con,
    verified_map: Dict[int, int],
) -> Tuple[List[Dict[str, Any]], List[ProductFeatures], List[Tuple[ProductFeatures, int, bool]]]:
    """Load products from products_current and tag verified ground truth samples."""
    rows = con.execute(
        """
        SELECT olsera_product_id, name, sku, barcode, category_id, category_name,
               klasifikasi_id, klasifikasi, brand_name, uom, payload_hash
        FROM products_current
        WHERE klasifikasi_id IS NOT NULL AND klasifikasi IS NOT NULL
        ORDER BY olsera_product_id
        """
    ).fetchall()

    items: List[Dict[str, Any]] = []
    features_list: List[ProductFeatures] = []
    train_tuples: List[Tuple[ProductFeatures, int, bool]] = []

    for r in rows:
        pid = int(r[0])
        d = {
            "olsera_product_id": pid,
            "name": r[1],
            "sku": r[2],
            "barcode": r[3],
            "category_id": r[4],
            "category_name": r[5],
            "klasifikasi_id": r[6],
            "klasifikasi": r[7],
            "brand_name": r[8],
            "uom": r[9],
            "payload_hash": r[10],
        }
        items.append(d)
        feat = extract_features(d)
        features_list.append(feat)

        if pid in verified_map:
            train_tuples.append((feat, verified_map[pid], True))
        else:
            train_tuples.append((feat, int(r[6]), False))

    return items, features_list, train_tuples


def _create_open_set_finding(
    prod: Dict[str, Any],
    match: AdaptiveKlasifikasiMatchResult,
    curr_kid: int,
    curr_kname: str,
) -> Tuple[ClassificationFinding, NewClassCandidateProduct]:
    """Build finding and candidate cluster record for an unclassifiable open-set product."""
    tokens = _extract_product_tokens(prod["name"])
    cand = NewClassCandidateProduct(
        product_id=prod["olsera_product_id"],
        name=prod["name"],
        tokens=tokens,
        max_known_similarity=match.similarity_score,
    )
    finding = ClassificationFinding(
        product_id=prod["olsera_product_id"],
        product_name=prod["name"],
        sku=prod["sku"],
        barcode=prod["barcode"],
        current_klasifikasi_id=curr_kid,
        current_klasifikasi=curr_kname,
        suggested_klasifikasi_id=0,
        suggested_klasifikasi="UNKNOWN",
        anomaly_type="NEW_CLASS_CANDIDATE",
        confidence_score=match.similarity_score,
        confidence_level="MEDIUM",
        classification_method="OPEN_SET_ESTIMATOR",
        reason=(
            f"Karakteristik teks produk tidak cocok dengan 61 klasifikasi eksisting "
            f"(kemiripan tertinggi hanya {match.similarity_score:.2f}). Diusulkan untuk pembentukan kelompok kelas baru."
        ),
        comparison_product_id=match.comparison_product_id,
        comparison_product_name=match.comparison_product_name,
        comparison_similarity=match.comparison_similarity,
        source_payload_hash=prod["payload_hash"],
        needs_review=True,
    )
    return finding, cand


def _verify_ambiguous_ai(
    prod: Dict[str, Any],
    pred_kid: int,
    curr_kid: int,
    match: AdaptiveKlasifikasiMatchResult,
    snapshot: TaxonomySnapshot,
    ai_ctx: Dict[str, Any],
) -> Tuple[int, str, str, str, str]:
    """Call Ollama verifier if quota permits and return adjusted prediction parameters."""
    con = ai_ctx.get("con")
    verifier = ai_ctx.get("verifier")
    ai_state = ai_ctx.get("ai_state", {})

    cand_kids = [pred_kid, curr_kid]
    if match.runner_up_klasifikasi_id:
        cand_kids.append(match.runner_up_klasifikasi_id)
    cands = [
        {"id": cid, "name": snapshot.klasifikasi_map[cid].name}
        for cid in cand_kids if cid in snapshot.klasifikasi_map
    ]

    ai_state["count"] = ai_state.get("count", 0) + 1
    ai_res = verifier.verify_one(con, product=prod, candidate_classes=cands)

    if ai_res.get("status") in ("COMPLETED", "CACHED") and ai_res.get("suggested_klasifikasi_id"):
        return (
            ai_res["suggested_klasifikasi_id"],
            ai_res["suggested_klasifikasi"],
            ai_res["confidence"],
            "HYBRID_OLLAMA",
            f"Verifikasi AI Ollama: {ai_res['reasoning']}",
        )
    return (
        pred_kid,
        match.predicted_klasifikasi_name,
        "MEDIUM",
        "ADAPTIVE_CENTROID_NN",
        "Pola teks condong ke klasifikasi rekomendasi.",
    )


def _evaluate_candidate_product(
    prod: Dict[str, Any],
    feat: ProductFeatures,
    classifier: AdaptiveKlasifikasiClassifier,
    snapshot: TaxonomySnapshot,
    ai_ctx: Dict[str, Any],
) -> Tuple[Optional[ClassificationFinding], Optional[NewClassCandidateProduct]]:
    """Evaluate an individual product for classification anomalies or open-set candidacy."""
    curr_kid = int(prod["klasifikasi_id"])
    curr_kname = prod["klasifikasi"].strip()
    match: AdaptiveKlasifikasiMatchResult = classifier.predict(feat, curr_kid)

    if match.is_unknown:
        return _create_open_set_finding(prod, match, curr_kid, curr_kname)

    pred_kid = match.predicted_klasifikasi_id
    if not pred_kid or pred_kid == curr_kid:
        return None, None

    pred_kname = match.predicted_klasifikasi_name
    score = match.similarity_score
    curr_meta = snapshot.klasifikasi_map.get(curr_kid)
    is_catch = curr_meta.is_catch_all if curr_meta else False

    should_flag = False
    anomaly = "SEMANTIC_MISCLASSIFICATION"
    conf_level = "LOW"
    finding_reason = ""
    method = "ADAPTIVE_CENTROID_NN"

    comp_hint = f" (serupa dengan '{match.comparison_product_name}')" if match.comparison_product_name else ""
    if is_catch and score >= 0.35:
        should_flag = True
        anomaly = "CATCHALL_RECLASSIFICATION"
        conf_level = "HIGH" if score >= 0.55 else "MEDIUM"
        finding_reason = f"Produk berada di klasifikasi umum '{curr_kname}'. Pola teks cocok dengan '{pred_kname}' (kemiripan {score:.2f}{comp_hint})."
    elif score >= 0.50 and (score - match.runner_up_similarity) >= 0.08:
        should_flag = True
        conf_level = "HIGH" if score >= 0.70 else "MEDIUM"
        finding_reason = f"Karakteristik produk condong ke '{pred_kname}' daripada '{curr_kname}' (kemiripan {score:.2f}{comp_hint})."

    if not should_flag:
        return None, None

    verifier = ai_ctx.get("verifier")
    ai_state = ai_ctx.get("ai_state", {})
    if verifier and match.is_ambiguous and ai_state.get("count", 0) < ai_state.get("max", 0):
        pred_kid, pred_kname, conf_level, method, finding_reason = _verify_ambiguous_ai(
            prod, pred_kid, curr_kid, match, snapshot, ai_ctx
        )

    return (
        ClassificationFinding(
            product_id=prod["olsera_product_id"],
            product_name=prod["name"],
            sku=prod["sku"],
            barcode=prod["barcode"],
            current_klasifikasi_id=curr_kid,
            current_klasifikasi=curr_kname,
            suggested_klasifikasi_id=pred_kid,
            suggested_klasifikasi=pred_kname,
            anomaly_type=anomaly,
            confidence_score=round(score, 4),
            confidence_level=conf_level,
            classification_method=method,
            reason=finding_reason,
            comparison_product_id=match.comparison_product_id,
            comparison_product_name=match.comparison_product_name,
            comparison_similarity=match.comparison_similarity,
            source_payload_hash=prod["payload_hash"],
            needs_review=(conf_level == "LOW" or score < 0.40),
        ),
        None,
    )


def run_classification_audit(
    con,
    verifier: Optional[KlasifikasiAIVerifier] = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    max_ai_verifications: int = 50,
    open_set_threshold: float = 0.28,
) -> Tuple[TaxonomySnapshot, List[ClassificationFinding], List[DynamicRedundancyProposal]]:
    """Execute dynamic classification audit, redundancy discovery, and open-set clustering."""
    if progress_callback:
        progress_callback(0, 100, "Menemukan taksonomi dinamis dari database...")

    registry = TaxonomyRegistry()
    snapshot = registry.discover_live_taxonomy(con)
    registry.persist_version(con, snapshot)

    redundancies = detect_taxonomy_redundancies(con, snapshot)
    redundancy_map = {r.redundant_id: r for r in redundancies}

    if not snapshot.valid_ids:
        return snapshot, [], redundancies

    try:
        gt_rows = con.execute("SELECT product_id, true_klasifikasi_id FROM cleaner_classification_ground_truth").fetchall()
        verified_map = {int(r[0]): int(r[1]) for r in gt_rows}
    except Exception:
        verified_map = {}

    items, features_list, train_tuples = _load_training_data(con, verified_map)
    total_items = len(items)
    if total_items == 0:
        return snapshot, [], redundancies

    if progress_callback:
        progress_callback(15, 100, "Membangun model klasifikasi adaptif...")

    neg_map = get_negative_feedback_map(con)
    classifier = AdaptiveKlasifikasiClassifier(
        snapshot=snapshot,
        open_set_threshold=open_set_threshold,
        negative_feedback_map=neg_map,
    )
    classifier.train(train_tuples)

    findings: List[ClassificationFinding] = []
    open_set_candidates: List[NewClassCandidateProduct] = []
    ai_ctx = {"con": con, "verifier": verifier, "ai_state": {"count": 0, "max": max_ai_verifications}}
    step_interval = max(1, total_items // 20)

    for idx, (prod, feat) in enumerate(zip(items, features_list)):
        if progress_callback and idx % step_interval == 0:
            pct = 25 + int((idx / total_items) * 60)
            progress_callback(pct, 100, f"Menganalisis produk {idx + 1}/{total_items}...")

        curr_kid = int(prod["klasifikasi_id"])
        curr_kname = prod["klasifikasi"].strip()

        if curr_kid in redundancy_map:
            red_info = redundancy_map[curr_kid]
            findings.append(
                ClassificationFinding(
                    product_id=prod["olsera_product_id"],
                    product_name=prod["name"],
                    sku=prod["sku"],
                    barcode=prod["barcode"],
                    current_klasifikasi_id=curr_kid,
                    current_klasifikasi=curr_kname,
                    suggested_klasifikasi_id=red_info.canonical_id,
                    suggested_klasifikasi=red_info.canonical_name,
                    anomaly_type=f"{red_info.redundancy_type}_REDUNDANCY",
                    confidence_score=red_info.similarity_score,
                    confidence_level="HIGH",
                    classification_method="DYNAMIC_CANONICALIZATION",
                    reason=red_info.recommendation,
                    comparison_product_id=None,
                    comparison_product_name=None,
                    comparison_similarity=None,
                    source_payload_hash=prod["payload_hash"],
                )
            )
            continue

        finding, cand = _evaluate_candidate_product(prod, feat, classifier, snapshot, ai_ctx)
        if finding:
            findings.append(finding)
        if cand:
            open_set_candidates.append(cand)

    if open_set_candidates:
        if progress_callback:
            progress_callback(90, 100, "Mengevaluasi pembentukan kelompok kelas baru (Open-Set)...")
        proposals = cluster_open_set_products(open_set_candidates, snapshot, min_cluster_size=2)
        persist_new_class_proposals(con, proposals)

    findings.sort(
        key=lambda f: (
            0 if f.confidence_level == "HIGH" else (1 if f.confidence_level == "MEDIUM" else 2),
            -f.confidence_score,
        )
    )

    if progress_callback:
        progress_callback(100, 100, "Audit klasifikasi adaptif selesai.")

    return snapshot, findings, redundancies
