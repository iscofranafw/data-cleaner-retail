"""Hybrid classification engine: combines Layer A features, Layer B statistical patterns, and Layer C AI verification."""

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from .classifier import CategoryPatternClassifier, MatchResult
from .discovery import CATCH_ALL_NAMES, DiscoverySummary, discover_categories
from .evaluator import FOOD_KLASIFIKASI, NON_FOOD_KLASIFIKASI
from .features import ProductFeatures, extract_features
from .verifier import CategoryAIVerifier


@dataclass
class CategoryFinding:
    product_id: int
    product_name: str
    sku: Optional[str]
    barcode: Optional[str]
    current_category_id: Optional[int]
    current_category_name: Optional[str]
    suggested_category_id: int
    suggested_category_name: str
    confidence_score: float
    confidence_level: str
    classification_method: str
    reason: str
    comparison_product_id: Optional[int]
    comparison_product_name: Optional[str]
    comparison_similarity: Optional[float]
    source_payload_hash: Optional[str]
    needs_review: bool = False


def run_category_audit(
    con,
    verifier: Optional[CategoryAIVerifier] = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    max_ai_verifications: int = 50,
) -> Tuple[DiscoverySummary, List[CategoryFinding]]:
    """Run full category audit pipeline across all products in products_current."""
    if progress_callback:
        progress_callback(0, 100, "Menemukan kategori yang tersedia di database...")
    discovery = discover_categories(con)
    if not discovery.valid_category_ids:
        return discovery, []

    rows = con.execute(
        """
        SELECT olsera_product_id, name, sku, barcode, category_id, category_name,
               klasifikasi, brand_name, uom, payload_hash
        FROM products_current
        ORDER BY olsera_product_id
        """
    ).fetchall()
    total_items = len(rows)
    if total_items == 0:
        return discovery, []

    if progress_callback:
        progress_callback(10, 100, "Mengekstraksi fitur dan mempelajari pola kategori...")

    items: List[Dict[str, Any]] = []
    features_list: List[ProductFeatures] = []
    train_tuples = []

    for r in rows:
        d = {
            "olsera_product_id": r[0],
            "name": r[1],
            "sku": r[2],
            "barcode": r[3],
            "category_id": r[4],
            "category_name": r[5],
            "klasifikasi": r[6],
            "brand_name": r[7],
            "uom": r[8],
            "payload_hash": r[9],
        }
        items.append(d)
        feat = extract_features(d)
        features_list.append(feat)
        train_tuples.append((feat, d["category_id"]))

    classifier = CategoryPatternClassifier(discovery)
    classifier.train(train_tuples)

    # Valid categories list for verifier
    valid_cat_list = [
        {"id": cid, "name": meta.name}
        for cid, meta in discovery.categories.items()
    ]

    findings: List[CategoryFinding] = []
    ai_verified_count = 0

    if progress_callback:
        progress_callback(25, 100, "Menganalisis potensi salah kategori...")

    step_interval = max(1, total_items // 20)

    for idx, (prod, feat) in enumerate(zip(items, features_list)):
        if progress_callback and idx % step_interval == 0:
            pct = 25 + int((idx / total_items) * 65)
            progress_callback(pct, 100, f"Menganalisis produk {idx + 1}/{total_items}...")

        curr_id = prod["category_id"]
        curr_name = (prod["category_name"] or "").strip()
        is_uncategorized = curr_id is None or curr_id == 0 or not curr_name
        is_catch_all = curr_name.lower() in CATCH_ALL_NAMES

        match = classifier.predict(feat, curr_id)
        if not match:
            continue

        pred_id = match.predicted_category_id
        pred_name = match.predicted_category_name
        score = match.similarity_score

        # Check if an audit finding should be generated
        finding_reason = ""
        method = "TFIDF_NEAREST_NEIGHBOR"
        conf_level = "LOW"
        should_flag = False

        if is_uncategorized:
            should_flag = True
            method = "UNCATEGORIZED_RULE"
            if score >= 0.40:
                conf_level = "HIGH" if score >= 0.65 else "MEDIUM"
                finding_reason = (
                    f"Produk belum memiliki kategori. Direkomendasikan ke '{pred_name}' "
                    f"dengan kemiripan {score:.2f} terhadap pola produk sejenis."
                )
            else:
                conf_level = "LOW"
                finding_reason = (
                    f"Produk belum memiliki kategori. Kemiripan tertinggi ke '{pred_name}' ({score:.2f}); butuh tinjauan."
                )

        elif is_catch_all and pred_id != curr_id:
            kla_clean = (prod.get("klasifikasi") or "").upper().strip()
            # Domain guard: do not reclassify non-food retail products into Food
            if pred_name.upper() == "FOOD" and kla_clean in NON_FOOD_KLASIFIKASI:
                should_flag = False
            elif score >= 0.25:
                should_flag = True
                # Boost confidence if corroborated by food klasifikasi
                if score >= 0.60 or (score >= 0.45 and kla_clean in FOOD_KLASIFIKASI):
                    conf_level = "HIGH"
                elif score >= 0.35 or kla_clean in FOOD_KLASIFIKASI:
                    conf_level = "MEDIUM"
                else:
                    conf_level = "LOW"

                neighbor_hint = f" (serupa dengan '{match.comparison_product_name}')" if match.comparison_product_name else ""
                finding_reason = (
                    f"Kategori saat ini umum ('{curr_name}'). Karakteristik produk cocok dengan '{pred_name}' "
                    f"dengan kemiripan {score:.2f}{neighbor_hint}."
                )

        elif curr_id != pred_id:
            # Item is in specific category A, but model strongly points to category B
            if score >= 0.60 and (score - match.runner_up_similarity) >= 0.20:
                should_flag = True
                conf_level = "HIGH" if score >= 0.75 else "MEDIUM"
                neighbor_hint = f" (serupa dengan '{match.comparison_product_name}')" if match.comparison_product_name else ""
                finding_reason = (
                    f"Kemungkinan salah kategori ('{curr_name}'). Indikator produk lebih condong ke '{pred_name}' "
                    f"dengan kemiripan {score:.2f}{neighbor_hint}."
                )

        if not should_flag:
            continue

        if verifier and match.is_ambiguous and ai_verified_count < max_ai_verifications:
            ai_verified_count += 1
            ai_res = verifier.verify_one(
                con,
                product=prod,
                valid_categories=valid_cat_list,
                layer_b_result={
                    "predicted_category_name": pred_name,
                    "comparison_product_name": match.comparison_product_name,
                },
            )
            if ai_res.get("status") in ("COMPLETED", "CACHED") and ai_res.get("suggested_category_id"):
                pred_id = ai_res["suggested_category_id"]
                pred_name = ai_res["suggested_category_name"]
                conf_level = ai_res["confidence"]
                method = "HYBRID_OLLAMA"
                finding_reason = f"Verifikasi AI Ollama: {ai_res['reasoning']}"

        needs_review = conf_level == "LOW" or score < 0.40

        finding = CategoryFinding(
            product_id=prod["olsera_product_id"],
            product_name=prod["name"],
            sku=prod["sku"],
            barcode=prod["barcode"],
            current_category_id=curr_id,
            current_category_name=curr_name or "(Tanpa Kategori)",
            suggested_category_id=pred_id,
            suggested_category_name=pred_name,
            confidence_score=round(score, 4),
            confidence_level=conf_level,
            classification_method=method,
            reason=finding_reason,
            comparison_product_id=match.comparison_product_id,
            comparison_product_name=match.comparison_product_name,
            comparison_similarity=match.comparison_similarity,
            source_payload_hash=prod["payload_hash"],
            needs_review=needs_review,
        )
        findings.append(finding)

    if progress_callback:
        progress_callback(100, 100, f"Selesai! Ditemukan {len(findings)} rekomendasi kategori.")

    return discovery, findings
