"""Unit tests for Ground Truth Evaluation, Data Leakage Prevention, and AI Quality."""

import json
from unittest.mock import patch
import pytest

from olsera_cleaner.category.evaluator import (
    FOOD_KLASIFIKASI,
    NON_FOOD_KLASIFIKASI,
    evaluate_model_on_ground_truth,
    get_ground_truth_dataset,
    save_ground_truth,
    seed_representative_ground_truth,
)
from olsera_cleaner.category.schema import setup
from olsera_cleaner.category.verifier import CategoryAIVerifier
from olsera_cleaner.database.repository import Database


def _setup_eval_db(tmp_path):
    db = Database(tmp_path / "test_eval.db")
    setup(db)
    with db.connect() as con:
        run_id = con.execute("INSERT INTO sync_runs (resource, status) VALUES ('products', 'COMPLETED')").lastrowid
        # Seed 10 food items in Food, 10 food items in Others, and 10 non-food items in Others
        items = [
            # Food in Food
            (1, "INDOMIE GORENG 85G", 2901, "Food", "MIE INSTANT"),
            (2, "INDOMIE AYAM BAWANG", 2901, "Food", "MIE INSTANT"),
            (3, "POCKY CHOCOLATE 47G", 2901, "Food", "JAJANAN"),
            (4, "POCKY STRAWBERRY 45G", 2901, "Food", "JAJANAN"),
            (5, "BISKUIT ROMA KELAPA", 2901, "Food", "BISKUIT"),
            (6, "ROTI TAWAR SARI ROTI", 2901, "Food", "ROTI KEJU SELAI"),
            (7, "CHIKI BALL KEJU", 2901, "Food", "CHIKI"),
            (8, "MINUMAN TEH PUCUK", 2901, "Food", "MINUMAN"),
            (9, "SOSIS KANZLER JAPAPENO", 2901, "Food", "FROZENFOOD"),
            (10, "MINUMAN AQUA 600ML", 2901, "Food", "MINUMAN"),
            # Food in Others (misclassified)
            (11, "INDOMIE KARI AYAM", 9901, "Others", "MIE INSTANT"),
            (12, "POCKY MATCHA 33G", 9901, "Others", "JAJANAN"),
            (13, "CHIKI BALL COKLAT", 9901, "Others", "CHIKI"),
            (14, "BISKUIT OREO VANILLA", 9901, "Others", "BISKUIT"),
            (15, "MINUMAN COCA COLA 330ML", 9901, "Others", "MINUMAN"),
            (16, "MIE SEDAAP GORENG", 9901, "Others", "MIE INSTANT"),
            (17, "KENTANG CHITATO SAPI PANGGANG", 9901, "Others", "CHIKI"),
            (18, "ROTI SOBEK COKLAT", 9901, "Others", "ROTI KEJU SELAI"),
            (19, "NUGGET FIESTA CRISPY", 9901, "Others", "FROZENFOOD"),
            (20, "BUMBU SAJIKU NASI GORENG", 9901, "Others", "BUMBU"),
            # Non-Food in Others (true Others)
            (21, "SABUN MANDI LIFEBUOY TOTAL 10", 9901, "Others", "ALAT MANDI"),
            (22, "SABUN CUCI PIRING SUNLIGHT LIME", 9901, "Others", "SABUN CUCI KAMPER"),
            (23, "DETERJEN RINSO ANTI NODA", 9901, "Others", "DETERGENT"),
            (24, "SHAMPO PANTENE TOTAL CARE", 9901, "Others", "ALAT MANDI"),
            (25, "PASTA GIGI PEPSODENT 190G", 9901, "Others", "ALAT MANDI"),
            (26, "LIPSTIK WARDAH MATTE", 9901, "Others", "KOSMETIK"),
            (27, "BEDAK MARCKS NATURAL", 9901, "Others", "KOSMETIK"),
            (28, "ROKOK SAMPOERNA MILD", 9901, "Others", "ROKOK"),
            (29, "PANADOL EXTRA 10 KAPLET", 9901, "Others", "OBATOBATAN"),
            (30, "PAMPERS MAMYPOKO M34", 9901, "Others", "PEMBALUT & PAMPERS"),
        ]
        sql = """
            INSERT INTO products_current (
                olsera_product_id, name, category_id, category_name, klasifikasi,
                payload_json, payload_hash, sync_run_id
            ) VALUES (?, ?, ?, ?, ?, '{}', 'hash', ?)
        """
        for pid, name, cid, cname, kla in items:
            con.execute(sql, (pid, name, cid, cname, kla, run_id))
    return db


def test_ground_truth_storage_and_retrieval(tmp_path):
    db = _setup_eval_db(tmp_path)
    with db.connect() as con:
        save_ground_truth(con, product_id=1, true_category_id=2901, true_category_name="Food", annotated_by="auditor_1", notes="Verified")
        save_ground_truth(con, product_id=21, true_category_id=9901, true_category_name="Others", annotated_by="auditor_1", notes="Verified soap")

        ds = get_ground_truth_dataset(con)
        assert len(ds) == 2
        f1 = next(d for d in ds if d["product_id"] == 1)
        assert f1["true_category_name"] == "Food"
        assert f1["annotated_by"] == "auditor_1"

        f21 = next(d for d in ds if d["product_id"] == 21)
        assert f21["true_category_name"] == "Others"


def test_insufficient_ground_truth_handled(tmp_path):
    db = _setup_eval_db(tmp_path)
    with db.connect() as con:
        # Only 2 items, < 10 threshold
        save_ground_truth(con, 1, 2901, "Food")
        save_ground_truth(con, 2, 2901, "Food")
        metrics, msg = evaluate_model_on_ground_truth(con)
        assert metrics is None
        assert "belum memadai" in msg


def test_seed_representative_ground_truth(tmp_path):
    db = _setup_eval_db(tmp_path)
    with db.connect() as con:
        count = seed_representative_ground_truth(con)
        assert count >= 20  # seeded representative items from catalog
        ds = get_ground_truth_dataset(con)
        food_count = sum(1 for d in ds if d["true_category_name"] == "Food")
        others_count = sum(1 for d in ds if d["true_category_name"] == "Others")
        assert food_count > 0
        assert others_count > 0


def test_evaluation_metrics_no_data_leakage(tmp_path):
    db = _setup_eval_db(tmp_path)
    with db.connect() as con:
        seed_representative_ground_truth(con)
        metrics, msg = evaluate_model_on_ground_truth(con, test_ratio=0.5, random_seed=42)

    assert msg == "OK"
    assert metrics is not None
    assert metrics.total_evaluated > 0
    assert 0.0 <= metrics.accuracy <= 1.0
    assert 0.0 <= metrics.precision_macro <= 1.0
    assert 0.0 <= metrics.recall_macro <= 1.0
    assert 0.0 <= metrics.precision_high_confidence <= 1.0
    assert "Food" in metrics.confusion_matrix or "Others" in metrics.confusion_matrix
    assert metrics.others_subset_evaluated > 0


def test_domain_guard_blocks_soap_strawberry_from_food(tmp_path):
    """Verify that a non-food item with a fruit/food word in title does not get classified into Food."""
    db = _setup_eval_db(tmp_path)
    with db.connect() as con:
        run_id = con.execute("SELECT id FROM sync_runs LIMIT 1").fetchone()[0]
        # Add a deceptive soap item with fruit name
        con.execute(
            """
            INSERT INTO products_current (olsera_product_id, name, category_id, category_name, klasifikasi, payload_json, payload_hash, sync_run_id)
            VALUES (999, 'SABUN MANDI STRAWBERRY 250ML', 9901, 'Others', 'ALAT MANDI', '{}', 'h_soap', ?)
            """,
            (run_id,),
        )
        seed_representative_ground_truth(con)
        save_ground_truth(con, 999, 9901, "Others", notes="Soap is non-food")
        metrics, msg = evaluate_model_on_ground_truth(con, test_ratio=0.5, random_seed=42)

    assert msg == "OK"
    assert metrics is not None
    # Sabun mandi strawberry must NOT be a false positive (it must stay Others)
    fp_ids = [fp["product_id"] for fp in metrics.false_positives]
    assert 999 not in fp_ids


def test_ai_verifier_telemetry_and_caching(tmp_path):
    db = Database(tmp_path / "test_ai.db")
    setup(db)
    verifier = CategoryAIVerifier(model="llama3:8b")

    prod = {"olsera_product_id": 999, "name": "CHIKI CHIKITO KEJU", "category_name": "Others", "klasifikasi": "CHIKI"}
    valid_cats = [{"id": 2901, "name": "Food"}, {"id": 9901, "name": "Others"}]

    mock_resp = {
        "response": json.dumps({
            "suggested_category_id": 2901,
            "suggested_category_name": "Food",
            "confidence": "HIGH",
            "reasoning": "Chiki keju adalah makanan ringan/snack",
        })
    }

    with patch("requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = mock_resp

        with db.connect() as con:
            res1 = verifier.verify_one(con, prod, valid_cats, layer_b_result={"predicted_category_id": 2901})
            assert res1["status"] == "COMPLETED"

            # Second call: Cache Hit
            res2 = verifier.verify_one(con, prod, valid_cats, layer_b_result={"predicted_category_id": 2901})
            assert res2["status"] == "CACHED"

        telemetry = verifier.get_telemetry()
        assert telemetry["total_queries"] == 2
        assert telemetry["cache_hits"] == 1
        assert telemetry["cache_misses"] == 1
        assert telemetry["agreed_with_statistical"] == 2
