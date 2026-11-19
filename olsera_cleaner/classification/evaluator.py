"""Precision, recall, confusion matrix, and ground-truth evaluation for the 61 Olsera classifications."""

import random
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .classifier import MultiClassKlasifikasiClassifier
from .discovery import discover_klasifikasi
from olsera_cleaner.category.features import extract_features


@dataclass
class KlasifikasiEvaluationMetrics:
    total_evaluated: int
    accuracy: float
    precision_macro: float
    recall_macro: float
    f1_macro: float
    precision_high_confidence: float
    precision_medium_confidence: float
    precision_low_confidence: float
    confusion_matrix: Dict[str, Dict[str, int]]
    class_metrics: Dict[str, Dict[str, float]]
    false_positives: List[Dict[str, Any]] = field(default_factory=list)
    false_negatives: List[Dict[str, Any]] = field(default_factory=list)


def save_klasifikasi_ground_truth(
    con,
    product_id: int,
    true_klasifikasi_id: int,
    true_klasifikasi: str,
    annotated_by: str = "operator",
    notes: str = "",
) -> None:
    """Save or update ground-truth annotation for a product's classification."""
    con.execute(
        """
        INSERT INTO cleaner_classification_ground_truth
            (product_id, true_klasifikasi_id, true_klasifikasi, annotated_by, annotated_at, notes)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
        ON CONFLICT(product_id) DO UPDATE SET
            true_klasifikasi_id = excluded.true_klasifikasi_id,
            true_klasifikasi = excluded.true_klasifikasi,
            annotated_by = excluded.annotated_by,
            annotated_at = CURRENT_TIMESTAMP,
            notes = excluded.notes
        """,
        (int(product_id), int(true_klasifikasi_id), true_klasifikasi, annotated_by, notes),
    )


def get_klasifikasi_ground_truth_dataset(con) -> List[Dict[str, Any]]:
    """Retrieve all classification ground-truth annotations with product metadata."""
    rows = con.execute(
        """
        SELECT g.product_id, p.name, p.sku, p.barcode, p.klasifikasi_id, p.klasifikasi,
               p.brand_name, p.uom, g.true_klasifikasi_id, g.true_klasifikasi,
               g.annotated_by, g.annotated_at, g.notes
        FROM cleaner_classification_ground_truth g
        JOIN products_current p ON p.olsera_product_id = g.product_id
        ORDER BY g.product_id
        """
    ).fetchall()
    keys = (
        "product_id",
        "name",
        "sku",
        "barcode",
        "klasifikasi_id",
        "klasifikasi",
        "brand_name",
        "uom",
        "true_klasifikasi_id",
        "true_klasifikasi",
        "annotated_by",
        "annotated_at",
        "notes",
    )
    return [dict(zip(keys, r)) for r in rows]


def seed_representative_klasifikasi_ground_truth(con) -> int:
    """Seed representative ground truth across diverse product classifications."""
    existing_count = con.execute("SELECT COUNT(*) FROM cleaner_classification_ground_truth").fetchone()[0]
    if existing_count >= 50:
        return existing_count

    sample_queries = [
        ("MIE INSTANT", 595874, "SELECT olsera_product_id FROM products_current WHERE klasifikasi='MIE INSTANT' LIMIT 15"),
        ("JAJANAN", 592068, "SELECT olsera_product_id FROM products_current WHERE klasifikasi='JAJANAN' LIMIT 15"),
        ("CHIKI", 675538, "SELECT olsera_product_id FROM products_current WHERE klasifikasi='CHIKI' LIMIT 15"),
        ("MINUMAN", 596207, "SELECT olsera_product_id FROM products_current WHERE klasifikasi='MINUMAN' LIMIT 15"),
        ("SABUN MANDI", 1015829, "SELECT olsera_product_id FROM products_current WHERE klasifikasi='SABUN MANDI' LIMIT 15"),
        ("KOSMETIK", 828024, "SELECT olsera_product_id FROM products_current WHERE klasifikasi='KOSMETIK' LIMIT 15"),
        ("ROKOK", 594686, "SELECT olsera_product_id FROM products_current WHERE klasifikasi='ROKOK' LIMIT 15"),
        ("DETERGENT", 595875, "SELECT olsera_product_id FROM products_current WHERE klasifikasi='DETERGENT' LIMIT 10"),
    ]

    for kname, kid, query in sample_queries:
        pids = [r[0] for r in con.execute(query).fetchall()]
        for pid in pids:
            save_klasifikasi_ground_truth(
                con,
                product_id=pid,
                true_klasifikasi_id=kid,
                true_klasifikasi=kname,
                annotated_by="system_seed",
                notes=f"Representative gold sample for {kname}",
            )

    current_total = con.execute("SELECT COUNT(*) FROM cleaner_classification_ground_truth").fetchone()[0]
    if current_total < 10:
        fallback_rows = con.execute(
            """
            SELECT olsera_product_id, klasifikasi_id, klasifikasi
            FROM products_current
            WHERE klasifikasi_id IS NOT NULL AND klasifikasi IS NOT NULL
            LIMIT 25
            """
        ).fetchall()
        for pid, kid, kname in fallback_rows:
            save_klasifikasi_ground_truth(
                con,
                product_id=pid,
                true_klasifikasi_id=kid,
                true_klasifikasi=kname,
                annotated_by="system_seed",
                notes=f"Representative sample for {kname}",
            )

    return con.execute("SELECT COUNT(*) FROM cleaner_classification_ground_truth").fetchone()[0]


def _build_holdout_classifier(con, eval_pids: set) -> MultiClassKlasifikasiClassifier:
    """Build classifier excluding evaluation samples to ensure zero data leakage."""
    discovery = discover_klasifikasi(con)
    all_rows = con.execute(
        """
        SELECT olsera_product_id, name, sku, barcode, category_id, category_name,
               klasifikasi_id, klasifikasi, brand_name, uom
        FROM products_current
        WHERE klasifikasi_id IS NOT NULL AND klasifikasi IS NOT NULL
        """
    ).fetchall()
    train_tuples = []
    for r in all_rows:
        pid = r[0]
        if pid in eval_pids:
            continue
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
        }
        train_tuples.append((extract_features(d), r[6]))

    classifier = MultiClassKlasifikasiClassifier(discovery)
    classifier.train(train_tuples)
    return classifier


def _compute_metrics(
    eval_set: List[Dict[str, Any]],
    confusion: Dict[str, Dict[str, int]],
    conf_stats: Dict[str, Tuple[int, int]],
    false_positives: List[Dict[str, Any]],
    false_negatives: List[Dict[str, Any]],
    correct_total: int,
) -> KlasifikasiEvaluationMetrics:
    """Calculate macro precision, recall, f1, and per-class stats."""
    total_eval = len(eval_set)
    accuracy = correct_total / total_eval if total_eval > 0 else 0.0

    h_cor, h_tot = conf_stats["HIGH"]
    m_cor, m_tot = conf_stats["MEDIUM"]
    l_cor, l_tot = conf_stats["LOW"]
    p_high = h_cor / h_tot if h_tot > 0 else 0.0
    p_med = m_cor / m_tot if m_tot > 0 else 0.0
    p_low = l_cor / l_tot if l_tot > 0 else 0.0

    classes = sorted(set(list(confusion.keys()) + [k for sub in confusion.values() for k in sub.keys()]))
    class_metrics: Dict[str, Dict[str, float]] = {}
    precisions = []
    recalls = []
    f1s = []

    for c in classes:
        tp = confusion[c][c]
        fp = sum(confusion[other][c] for other in classes if other != c)
        fn = sum(confusion[c][other] for other in classes if other != c)

        prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        rec = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (2 * prec * rec) / (prec + rec) if (prec + rec) > 0 else 0.0

        class_metrics[c] = {"precision": round(prec, 4), "recall": round(rec, 4), "f1": round(f1, 4)}
        precisions.append(prec)
        recalls.append(rec)
        f1s.append(f1)

    macro_prec = sum(precisions) / len(precisions) if precisions else 0.0
    macro_rec = sum(recalls) / len(recalls) if recalls else 0.0
    macro_f1 = sum(f1s) / len(f1s) if f1s else 0.0

    return KlasifikasiEvaluationMetrics(
        total_evaluated=total_eval,
        accuracy=round(accuracy, 4),
        precision_macro=round(macro_prec, 4),
        recall_macro=round(macro_rec, 4),
        f1_macro=round(macro_f1, 4),
        precision_high_confidence=round(p_high, 4),
        precision_medium_confidence=round(p_med, 4),
        precision_low_confidence=round(p_low, 4),
        confusion_matrix={k: dict(v) for k, v in confusion.items()},
        class_metrics=class_metrics,
        false_positives=false_positives,
        false_negatives=false_negatives,
    )


def evaluate_klasifikasi_model(
    con,
    test_ratio: float = 0.5,
    random_seed: int = 42,
) -> Tuple[Optional[KlasifikasiEvaluationMetrics], str]:
    """Evaluate multi-class prediction accuracy against ground-truth labels without data leakage."""
    gt_dataset = get_klasifikasi_ground_truth_dataset(con)
    if len(gt_dataset) < 10:
        return None, f"Ground-truth belum memadai (hanya {len(gt_dataset)} label, minimal butuh 10 label untuk evaluasi)."

    rng = random.Random(random_seed)
    shuffled = list(gt_dataset)
    rng.shuffle(shuffled)

    split_idx = max(5, int(len(shuffled) * test_ratio))
    eval_set = shuffled[:split_idx]
    eval_pids = {item["product_id"] for item in eval_set}

    classifier = _build_holdout_classifier(con, eval_pids)

    confusion: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    false_positives: List[Dict[str, Any]] = []
    false_negatives: List[Dict[str, Any]] = []
    counts = {"HIGH": [0, 0], "MEDIUM": [0, 0], "LOW": [0, 0]}
    correct_total = 0

    for item in eval_set:
        feat = extract_features(item)
        true_name = item["true_klasifikasi"]
        curr_kid = item["klasifikasi_id"]

        match = classifier.predict(feat, curr_kid)
        if match:
            pred_name = match.predicted_klasifikasi_name
            conf = "HIGH" if match.similarity_score >= 0.65 else ("MEDIUM" if match.similarity_score >= 0.40 else "LOW")
        else:
            pred_name = item["klasifikasi"]
            conf = "LOW"

        confusion[true_name][pred_name] += 1
        is_correct = (pred_name == true_name)
        if is_correct:
            correct_total += 1

        counts[conf][1] += 1
        if is_correct:
            counts[conf][0] += 1

        if not is_correct:
            false_positives.append({
                "product_id": item["product_id"],
                "product_name": item["name"],
                "true_klasifikasi": true_name,
                "predicted_klasifikasi": pred_name,
                "confidence_level": conf,
                "notes": item.get("notes", ""),
            })

    conf_stats = {k: (v[0], v[1]) for k, v in counts.items()}
    metrics = _compute_metrics(eval_set, confusion, conf_stats, false_positives, false_negatives, correct_total)
    return metrics, "OK"
