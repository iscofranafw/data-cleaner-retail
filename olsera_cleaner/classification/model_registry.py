"""Model registry, lifecycle, versioned builds, governance gates, and rollback."""

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from .classifier import AdaptiveKlasifikasiClassifier
from .evaluator import evaluate_klasifikasi_model
from .feedback import get_negative_feedback_map
from .registry import TaxonomyRegistry, TaxonomySnapshot
from olsera_cleaner.category.features import extract_features


@dataclass
class ModelVersionInfo:
    model_version: str
    taxonomy_version: str
    status: str
    trained_on_items: int
    macro_f1: float
    accuracy: float
    created_at: str
    activated_at: Optional[str] = None
    metadata: Dict[str, Any] = None


class ModelRegistry:
    """Manages training lifecycle, model versioning, validation gates, and active model promotion."""

    def __init__(self, registry: TaxonomyRegistry):
        self.registry = registry

    def get_active_model_info(self, con) -> Optional[ModelVersionInfo]:
        """Fetch current ACTIVE model metadata from SQLite."""
        row = con.execute(
            """
            SELECT model_version, taxonomy_version, status, trained_on_items,
                   macro_f1, accuracy, created_at, activated_at, model_metadata_json
            FROM cleaner_classification_models
            WHERE status = 'ACTIVE'
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        if not row:
            return None
        return ModelVersionInfo(
            model_version=row[0],
            taxonomy_version=row[1],
            status=row[2],
            trained_on_items=row[3],
            macro_f1=row[4],
            accuracy=row[5],
            created_at=row[6],
            activated_at=row[7],
            metadata=json.loads(row[8] or "{}"),
        )

    def is_model_stale(self, con, current_snapshot: TaxonomySnapshot) -> bool:
        """Check whether current active model is stale compared to live taxonomy or unanalyzed products."""
        active = self.get_active_model_info(con)
        if not active:
            return True
        if active.taxonomy_version != current_snapshot.version_hash:
            return True
        last_trained = active.metadata.get("ground_truth_count", 0) if active.metadata else 0
        curr_gt = con.execute("SELECT COUNT(*) FROM cleaner_classification_ground_truth").fetchone()[0]
        return (curr_gt - last_trained) >= 5

    def train_candidate_model(
        self,
        con,
        snapshot: TaxonomySnapshot,
        open_set_threshold: float = 0.28,
    ) -> Tuple[AdaptiveKlasifikasiClassifier, ModelVersionInfo]:
        """Train candidate adaptive classifier and record candidate model in database."""
        neg_map = get_negative_feedback_map(con)
        gt_rows = con.execute(
            "SELECT product_id, true_klasifikasi_id FROM cleaner_classification_ground_truth"
        ).fetchall()
        verified_map = {int(r[0]): int(r[1]) for r in gt_rows}

        product_rows = con.execute(
            """
            SELECT olsera_product_id, name, sku, barcode, category_id, category_name,
                   klasifikasi_id, klasifikasi, brand_name, uom
            FROM products_current
            WHERE klasifikasi_id IS NOT NULL AND klasifikasi IS NOT NULL
            """
        ).fetchall()

        train_tuples = []
        for r in product_rows:
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
            }
            feat = extract_features(d)
            if pid in verified_map:
                kid = verified_map[pid]
                train_tuples.append((feat, kid, True))
            else:
                train_tuples.append((feat, int(r[6]), False))

        classifier = AdaptiveKlasifikasiClassifier(
            snapshot=snapshot,
            open_set_threshold=open_set_threshold,
            negative_feedback_map=neg_map,
        )
        classifier.train(train_tuples)

        seed_data = f"{snapshot.version_hash}_{len(train_tuples)}_{len(verified_map)}"
        version_hash = f"mod_{hashlib.sha256(seed_data.encode()).hexdigest()[:10]}"

        meta = {
            "ground_truth_count": len(verified_map),
            "class_count": snapshot.classifications_count,
            "open_set_threshold": open_set_threshold,
            "product_count": len(product_rows),
        }

        con.execute(
            """
            INSERT INTO cleaner_classification_models
                (model_version, taxonomy_version, status, trained_on_items, macro_f1, accuracy, model_metadata_json, created_at)
            VALUES (?, ?, 'CANDIDATE', ?, 0.0, 0.0, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(model_version) DO UPDATE SET
                taxonomy_version = excluded.taxonomy_version,
                status = excluded.status,
                trained_on_items = excluded.trained_on_items,
                model_metadata_json = excluded.model_metadata_json
            """,
            (version_hash, snapshot.version_hash, len(train_tuples), json.dumps(meta)),
        )

        info = ModelVersionInfo(
            model_version=version_hash,
            taxonomy_version=snapshot.version_hash,
            status="CANDIDATE",
            trained_on_items=len(train_tuples),
            macro_f1=0.0,
            accuracy=0.0,
            created_at="JUST_NOW",
            metadata=meta,
        )
        return classifier, info

    def evaluate_and_promote(
        self,
        con,
        candidate_info: ModelVersionInfo,
        test_ratio: float = 0.35,
        min_holdout_samples: int = 15,
        min_high_precision: float = 0.90,
        min_macro_f1: float = 0.80,
        force_promotion: bool = False,
    ) -> Tuple[bool, str, Optional[Any]]:
        """Evaluate candidate model on ground truth with strict governance gates."""
        gt_count = con.execute("SELECT COUNT(*) FROM cleaner_classification_ground_truth").fetchone()[0]
        metrics, msg = evaluate_klasifikasi_model(con, test_ratio=test_ratio)
        active = self.get_active_model_info(con)

        cand_f1 = getattr(metrics, "f1_macro", 0.0) if metrics else 0.0
        cand_acc = getattr(metrics, "accuracy", 0.0) if metrics else 0.0
        p_high = getattr(metrics, "precision_high_confidence", 1.0) if metrics else 0.0
        eval_count = getattr(metrics, "total_evaluated", 0) if metrics else 0

        # Ensure candidate model row exists in database
        con.execute(
            """
            INSERT INTO cleaner_classification_models
                (model_version, taxonomy_version, status, trained_on_items, macro_f1, accuracy, created_at)
            VALUES (?, ?, 'CANDIDATE', ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(model_version) DO UPDATE SET macro_f1 = excluded.macro_f1, accuracy = excluded.accuracy
            """,
            (candidate_info.model_version, candidate_info.taxonomy_version, candidate_info.trained_on_items, cand_f1, cand_acc),
        )

        # Governance Gate 1: Regression check against active model (ALWAYS checked first)
        if not force_promotion and active and active.macro_f1 > 0.0 and cand_f1 < (active.macro_f1 - 0.08):
            con.execute(
                "UPDATE cleaner_classification_models SET status = 'REJECTED_QUALITY_DROP' WHERE model_version = ?",
                (candidate_info.model_version,),
            )
            return (
                False,
                f"Promosi ditolak: performa kandidat ({cand_f1:.2f}) regresi dibanding model aktif ({active.macro_f1:.2f}).",
                metrics,
            )

        # Cold-start bootstrap fallback when no active model exists and no ground truth annotations exist yet
        if active is None and gt_count == 0:
            con.execute(
                "UPDATE cleaner_classification_models SET status = 'ACTIVE', activated_at = CURRENT_TIMESTAMP WHERE model_version = ?",
                (candidate_info.model_version,),
            )
            return True, f"Model {candidate_info.model_version} diaktifkan sebagai model bootstrap awal (belum ada ground truth).", metrics

        # Governance Gate 2: Check sample adequacy on ground truth
        if not force_promotion and gt_count > 0 and eval_count < min_holdout_samples:
            con.execute(
                "UPDATE cleaner_classification_models SET status = 'NEEDS_VALIDATION' WHERE model_version = ?",
                (candidate_info.model_version,),
            )
            return (
                False,
                f"Model {candidate_info.model_version} berstatus NEEDS_VALIDATION: jumlah sampel holdout ({eval_count}) "
                f"kurang dari batas minimum tata kelola ({min_holdout_samples}). Butuh label ground truth tambahan.",
                metrics,
            )

        # Governance Gate 3: Minimum HIGH precision threshold on ground truth
        if not force_promotion and gt_count > 0 and p_high < min_high_precision:
            con.execute(
                "UPDATE cleaner_classification_models SET status = 'NEEDS_VALIDATION' WHERE model_version = ?",
                (candidate_info.model_version,),
            )
            return (
                False,
                f"Model {candidate_info.model_version} berstatus NEEDS_VALIDATION: presisi HIGH confidence "
                f"({p_high*100:.1f}%) berada di bawah ambang batas aman ({min_high_precision*100:.1f}%).",
                metrics,
            )

        # Demote previous active model
        con.execute("UPDATE cleaner_classification_models SET status = 'RETIRED' WHERE status = 'ACTIVE'")

        # Promote candidate to ACTIVE
        con.execute(
            """
            UPDATE cleaner_classification_models
            SET status = 'ACTIVE', activated_at = CURRENT_TIMESTAMP
            WHERE model_version = ?
            """,
            (candidate_info.model_version,),
        )
        return True, f"Model {candidate_info.model_version} berhasil divalidasi dan diaktifkan (Macro-F1: {cand_f1:.2f}, Akurasi: {cand_acc:.2f}, Presisi HIGH: {p_high*100:.1f}%).", metrics

    def rollback_model(self, con, target_model_version: str) -> Tuple[bool, str]:
        """Roll back active model to a previously trained model version."""
        row = con.execute(
            "SELECT model_version, taxonomy_version FROM cleaner_classification_models WHERE model_version = ?",
            (target_model_version,),
        ).fetchone()
        if not row:
            return False, f"Model version {target_model_version} tidak ditemukan di registry."

        con.execute("UPDATE cleaner_classification_models SET status = 'RETIRED' WHERE status = 'ACTIVE'")
        con.execute(
            "UPDATE cleaner_classification_models SET status = 'ACTIVE', activated_at = CURRENT_TIMESTAMP WHERE model_version = ?",
            (target_model_version,),
        )
        return True, f"Berhasil melakukan rollback: Model {target_model_version} kini menjadi model ACTIVE."
