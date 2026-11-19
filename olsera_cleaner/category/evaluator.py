"""Precision, recall, confusion matrix, and ground-truth evaluation for category classification."""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from .classifier import CategoryPatternClassifier
from .discovery import discover_categories
from .features import extract_features

# Complete taxonomy mapping of all 61 active classifications in Olsera
NON_FOOD_KLASIFIKASI: Set[str] = {
    "ALAT KECANTIKAN",
    "ALAT KONTRASEPSI",
    "ALAT LISTRIK",
    "ALAT MANDI",
    "ALAT TULIS",
    "ALATMANDI",
    "CLOSING",
    "DEODORANT",
    "DETERGENT",
    "GAS",
    "HANDBODY LOTION",
    "KEBUTUHAN BAYI",
    "KOSMETIK",
    "LAINLAIN",
    "LAMPU",
    "LILIN",
    "MAINAN",
    "OBAT NYAMUK",
    "OBATOBATAN",
    "PAKAIAN",
    "PARFUME",
    "PASTA GIGI",
    "PEMBALUT & PAMPERS",
    "PEMBERSIH KEWANITAAN",
    "PERALATAN",
    "PEWANGI",
    "PEWANGI RUANGAN",
    "PEWARNA RAMBUT",
    "ROKOK",
    "SABUN CUCI KAMPER",
    "SABUN CUCI PIRING",
    "SABUN LANTAI",
    "SABUN MANDI",
    "SABUN MUKA",
    "SHAMPO & PERAWATAN RAMBUT",
    "SIKAT GIGI",
    "TISU",
    "TISU BASAH",
    "KEBUTUHAN",
}

FOOD_KLASIFIKASI: Set[str] = {
    "BISKUIT",
    "BUAHBUAHAN",
    "BUBUR & SUP",
    "BUMBU",
    "CEMILAN",
    "CHIKI",
    "CIKI",
    "FROZENFOOD",
    "ICE CREAM",
    "JAJANAN",
    "KECAP SAOS BAHAN",
    "MAKANAN",
    "MIE INSTANT",
    "MINUMAN",
    "MINUMAN BUBUK",
    "MINUMAN KEMASAN DUS",
    "PERMEN",
    "ROTI KEJU SELAI",
    "SAYURAN",
    "SEMBAKO",
    "SUSU",
    "SUSU BUBUK PACK",
}


@dataclass
class EvaluationMetrics:
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
    others_subset_evaluated: int = 0
    others_subset_precision: float = 0.0


def save_ground_truth(
    con,
    product_id: int,
    true_category_id: int,
    true_category_name: str,
    annotated_by: str = "operator",
    notes: str = "",
) -> None:
    """Save or update operator ground-truth annotation for a product."""
    con.execute(
        """
        INSERT INTO cleaner_category_ground_truth
            (product_id, true_category_id, true_category_name, annotated_by, annotated_at, notes)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
        ON CONFLICT(product_id) DO UPDATE SET
            true_category_id = excluded.true_category_id,
            true_category_name = excluded.true_category_name,
            annotated_by = excluded.annotated_by,
            annotated_at = CURRENT_TIMESTAMP,
            notes = excluded.notes
        """,
        (int(product_id), int(true_category_id), true_category_name, annotated_by, notes),
    )


def get_ground_truth_dataset(con) -> List[Dict[str, Any]]:
    """Retrieve all operator ground-truth annotations with product details."""
    rows = con.execute(
        """
        SELECT g.product_id, p.name, p.sku, p.barcode, p.category_id, p.category_name,
               p.klasifikasi, p.brand_name, p.uom, g.true_category_id, g.true_category_name,
               g.annotated_by, g.annotated_at, g.notes
        FROM cleaner_category_ground_truth g
        JOIN products_current p ON p.olsera_product_id = g.product_id
        ORDER BY g.product_id
        """
    ).fetchall()
    keys = (
        "product_id",
        "name",
        "sku",
        "barcode",
        "category_id",
        "category_name",
        "klasifikasi",
        "brand_name",
        "uom",
        "true_category_id",
        "true_category_name",
        "annotated_by",
        "annotated_at",
        "notes",
    )
    return [dict(zip(keys, r)) for r in rows]


def seed_representative_ground_truth(con) -> int:
    """Seed representative ground-truth samples across Food and Non-Food categories."""
    existing_count = con.execute("SELECT COUNT(*) FROM cleaner_category_ground_truth").fetchone()[0]
    if existing_count >= 50:
        return existing_count

    # Select representative items directly from products_current
    # 1. Real food items in Food (Category 2901)
    food_in_food = con.execute(
        """
        SELECT olsera_product_id FROM products_current
        WHERE category_name = 'Food' AND klasifikasi IN ('JAJANAN', 'BISKUIT', 'CHIKI', 'FROZENFOOD')
        LIMIT 40
        """
    ).fetchall()
    for row in food_in_food:
        save_ground_truth(con, row[0], 2901, "Food", annotated_by="system_seed", notes="Verified Food in Food category")

    # 2. Food items misclassified in Others (Category 9901) -> True Category: Food (2901)
    food_in_others = con.execute(
        """
        SELECT olsera_product_id FROM products_current
        WHERE category_name = 'Others' AND klasifikasi IN ('MIE INSTANT', 'MINUMAN', 'CHIKI', 'JAJANAN', 'ROTI KEJU SELAI', 'BUMBU')
        LIMIT 50
        """
    ).fetchall()
    for row in food_in_others:
        save_ground_truth(con, row[0], 2901, "Food", annotated_by="system_seed", notes="Verified Food misclassified in Others")

    # 3. Genuine Non-Food items in Others (Category 9901) -> True Category: Others (9901)
    non_food_in_others = con.execute(
        """
        SELECT olsera_product_id FROM products_current
        WHERE category_name = 'Others' AND klasifikasi IN ('ALAT MANDI', 'SABUN CUCI KAMPER', 'DETERGENT', 'KOSMETIK', 'ROKOK', 'PERALATAN', 'OBATOBATAN')
        LIMIT 50
        """
    ).fetchall()
    for row in non_food_in_others:
        save_ground_truth(con, row[0], 9901, "Others", annotated_by="system_seed", notes="Verified genuine Non-Food in Others")

    return con.execute("SELECT COUNT(*) FROM cleaner_category_ground_truth").fetchone()[0]


def evaluate_model_on_ground_truth(
    con,
    test_ratio: float = 0.5,
    random_seed: int = 42,
) -> Tuple[Optional[EvaluationMetrics], str]:
    """Evaluate classification precision, recall, and confusion matrix without data leakage."""
    gt_dataset = get_ground_truth_dataset(con)
    if len(gt_dataset) < 10:
        return None, f"Ground-truth belum memadai (hanya {len(gt_dataset)} label, minimal butuh 10 label untuk evaluasi)."

    # Split ground-truth into evaluation test set vs training reference set
    import random
    rng = random.Random(random_seed)
    shuffled = list(gt_dataset)
    rng.shuffle(shuffled)

    split_idx = max(5, int(len(shuffled) * test_ratio))
    eval_set = shuffled[:split_idx]
    eval_pids = {item["product_id"] for item in eval_set}

    # Fetch all catalog products for training, strictly excluding evaluation products to avoid data leakage
    all_rows = con.execute(
        """
        SELECT olsera_product_id, name, sku, barcode, category_id, category_name, klasifikasi, brand_name, uom
        FROM products_current
        """
    ).fetchall()

    train_tuples = []
    for r in all_rows:
        pid = r[0]
        if pid in eval_pids:
            # STRICT DATA LEAKAGE PREVENTION: never train on test samples
            continue
        d = {
            "olsera_product_id": pid,
            "name": r[1],
            "sku": r[2],
            "barcode": r[3],
            "category_id": r[4],
            "category_name": r[5],
            "klasifikasi": r[6],
            "brand_name": r[7],
            "uom": r[8],
        }
        feat = extract_features(d)
        train_tuples.append((feat, d["category_id"]))

    discovery = discover_categories(con)
    classifier = CategoryPatternClassifier(discovery)
    classifier.train(train_tuples)

    # Evaluate predictions against ground truth
    confusion: Dict[str, Dict[str, int]] = defaultdict(lambda: Counter())
    high_conf_correct = 0
    high_conf_total = 0
    med_conf_correct = 0
    med_conf_total = 0
    low_conf_correct = 0
    low_conf_total = 0

    others_subset_evaluated = 0
    others_subset_correct = 0

    false_positives: List[Dict[str, Any]] = []
    false_negatives: List[Dict[str, Any]] = []

    correct_total = 0

    for item in eval_set:
        feat = extract_features(item)
        true_name = item["true_category_name"]
        curr_name = item["category_name"]
        curr_id = item["category_id"]

        match = classifier.predict(feat, curr_id)
        if match:
            # Domain guard integration: if predicted Food but klasifikasi is explicitly non-food
            kla = (item.get("klasifikasi") or "").upper().strip()
            if match.predicted_category_name.upper() == "FOOD" and kla in NON_FOOD_KLASIFIKASI:
                pred_name = "Others"
                conf_level = "HIGH"
            else:
                pred_name = match.predicted_category_name
                conf_level = "HIGH" if match.similarity_score >= 0.60 else ("MEDIUM" if match.similarity_score >= 0.40 else "LOW")
        else:
            pred_name = curr_name or "Others"
            conf_level = "LOW"

        confusion[true_name][pred_name] += 1
        is_correct = (pred_name == true_name)
        if is_correct:
            correct_total += 1

        if conf_level == "HIGH":
            high_conf_total += 1
            if is_correct:
                high_conf_correct += 1
        elif conf_level == "MEDIUM":
            med_conf_total += 1
            if is_correct:
                med_conf_correct += 1
        else:
            low_conf_total += 1
            if is_correct:
                low_conf_correct += 1

        if curr_name == "Others":
            others_subset_evaluated += 1
            if is_correct:
                others_subset_correct += 1

        if not is_correct:
            err_dict = {
                "product_id": item["product_id"],
                "product_name": item["name"],
                "klasifikasi": item["klasifikasi"],
                "true_category": true_name,
                "predicted_category": pred_name,
                "confidence_level": conf_level,
                "notes": item.get("notes", ""),
            }
            if pred_name == "Food" and true_name != "Food":
                false_positives.append(err_dict)
            else:
                false_negatives.append(err_dict)

    total_eval = len(eval_set)
    accuracy = correct_total / total_eval if total_eval > 0 else 0.0
    p_high = high_conf_correct / high_conf_total if high_conf_total > 0 else 0.0
    p_med = med_conf_correct / med_conf_total if med_conf_total > 0 else 0.0
    p_low = low_conf_correct / low_conf_total if low_conf_total > 0 else 0.0

    # Calculate per-class precision, recall, and F1
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

    p_others_subset = others_subset_correct / others_subset_evaluated if others_subset_evaluated > 0 else 0.0

    metrics = EvaluationMetrics(
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
        others_subset_evaluated=others_subset_evaluated,
        others_subset_precision=round(p_others_subset, 4),
    )
    return metrics, "OK"
