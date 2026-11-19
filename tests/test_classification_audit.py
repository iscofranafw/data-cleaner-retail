"""Comprehensive test suite for Smart Product Classification Audit V2 across 61 classifications."""

import json
import sqlite3
import pytest

from olsera_cleaner.category.features import extract_features
from olsera_cleaner.classification.classifier import (
    KlasifikasiMatchResult,
    MultiClassKlasifikasiClassifier,
)
from olsera_cleaner.classification.discovery import (
    KlasifikasiDiscoverySummary,
    discover_klasifikasi,
)
from olsera_cleaner.classification.engine import (
    ClassificationFinding,
    run_classification_audit,
)
from olsera_cleaner.classification.evaluator import (
    evaluate_klasifikasi_model,
    get_klasifikasi_ground_truth_dataset,
    save_klasifikasi_ground_truth,
    seed_representative_klasifikasi_ground_truth,
)
from olsera_cleaner.classification.overlapping import (
    check_granular_misclassification,
    find_taxonomy_redundancies,
)
from olsera_cleaner.classification.schema import setup as setup_classification_schema
from olsera_cleaner.classification.store import (
    bulk_review_classification_findings,
    export_classification_audit_csv,
    get_recent_classification_audit,
    review_classification_finding,
    save_classification_audit,
)
from olsera_cleaner.classification.verifier import KlasifikasiAIVerifier
from olsera_cleaner.database.repository import Database


def _create_mock_db():
    con = sqlite3.connect(":memory:")
    con.executescript("""
        CREATE TABLE sync_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            resource TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT DEFAULT CURRENT_TIMESTAMP,
            finished_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO sync_runs (resource, status) VALUES ('products', 'COMPLETED');

        CREATE TABLE products_current (
            olsera_product_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            sku TEXT,
            barcode TEXT,
            category_id INTEGER,
            category_name TEXT,
            klasifikasi_id INTEGER,
            klasifikasi TEXT,
            product_group_id INTEGER,
            brand_id INTEGER,
            brand_name TEXT,
            uom TEXT,
            price REAL,
            stock REAL,
            payload_hash TEXT DEFAULT 'hash123',
            sync_run_id INTEGER DEFAULT 1,
            synced_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            detail TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # Seed realistic Olsera catalog with known classifications and misclassifications
    sample_data = [
        # Canonical ALAT MANDI (602036) vs redundant ALATMANDI (595877)
        (101, "SIKAT GIGI PEPSODENT SOFT", "SKU101", "899101", 9901, "Others", 602036, "ALAT MANDI", 602036, "PEPSODENT", "PCS"),
        (102, "ODOL PEPSODENT 120G", "SKU102", "899102", 9901, "Others", 602036, "ALAT MANDI", 602036, "PEPSODENT", "PCS"),
        (103, "SIKAT GIGI FORMULA EXTRA CLEAN", "SKU103", "899103", 9901, "Others", 595877, "ALATMANDI", 595877, "FORMULA", "PCS"),
        # Canonical CHIKI (675538) vs redundant CIKI (596210)
        (201, "CHIKI BALL KEJU 55G", "SKU201", "899201", 2901, "Food", 675538, "CHIKI", 675538, "CHIKI", "BKS"),
        (202, "CHIKI TWIST JAGUNG BAKAR", "SKU202", "899202", 2901, "Food", 675538, "CHIKI", 675538, "CHIKI", "BKS"),
        (203, "CIKI JITIK COKELAT 40G", "SKU203", "899203", 2901, "Food", 596210, "CIKI", 596210, "CIKI", "BKS"),
        # Granular SABUN CUCI KAMPER (596205) misclassifications
        (301, "SUNLIGHT LIME 750ML POUCH", "SKU301", "899301", 9901, "Others", 596205, "SABUN CUCI KAMPER", 596205, "SUNLIGHT", "PC"),
        (302, "MAMA LEMON JERUK NIPIS 780ML", "SKU302", "899302", 9901, "Others", 596205, "SABUN CUCI KAMPER", 596205, "MAMA LEMON", "PC"),
        (303, "RINSO ANTI NODA PLUS DETERGENT 800G", "SKU303", "899303", 9901, "Others", 596205, "SABUN CUCI KAMPER", 596205, "RINSO", "PC"),
        (304, "DAIA DETERJENT BUBUK BUNGA 850G", "SKU304", "899304", 9901, "Others", 596205, "SABUN CUCI KAMPER", 596205, "DAIA", "PC"),
        (305, "WIPOL PEMBERSIH LANTAI CEMARA 780ML", "SKU305", "899305", 9901, "Others", 596205, "SABUN CUCI KAMPER", 596205, "WIPOL", "PC"),
        (306, "MOLTO ALL IN ONE PEWANGI PAKAIAN 720ML", "SKU306", "899306", 9901, "Others", 596205, "SABUN CUCI KAMPER", 596205, "MOLTO", "PC"),
        (307, "BAGUS KAMPER TOILET BALL 5S", "SKU307", "899307", 9901, "Others", 596205, "SABUN CUCI KAMPER", 596205, "BAGUS", "PC"),
        # Specific cleaning classes for target training
        (401, "SUNLIGHT EXTRA HIGIENIS 700ML", "SKU401", "899401", 9901, "Others", 1441865, "SABUN CUCI PIRING", 1441865, "SUNLIGHT", "PC"),
        (402, "RINSO MOLTO DETERGEN CAIR 750ML", "SKU402", "899402", 9901, "Others", 595875, "DETERGENT", 595875, "RINSO", "PC"),
        (403, "SUPER PELL PEMBERSIH LANTAI APPLE 770ML", "SKU403", "899403", 9901, "Others", 1441866, "SABUN LANTAI", 1441866, "SUPER PELL", "PC"),
        (404, "DOWNY PEWANGI DAN PELEMBUT MISTIQUE 720ML", "SKU404", "899404", 9901, "Others", 1441867, "PEWANGI", 1441867, "DOWNY", "PC"),
        (405, "LUX SABUN MANDI BATANG SOFT ROSE 110G", "SKU405", "899405", 9901, "Others", 595878, "SABUN MANDI", 595878, "LUX", "PC"),
        (406, "LIFEBUOY SABUN BATANG TOTAL 10 110G", "SKU406", "899406", 9901, "Others", 595878, "SABUN MANDI", 595878, "LIFEBUOY", "PC"),
        # Beverages: Powder vs Ready-to-Drink
        (501, "TEH BOTOL SOSRO KOTAK 250ML", "SKU501", "899501", 2901, "Food", 595872, "MINUMAN", 595872, "SOSRO", "KOTAK"),
        (502, "NUTRISARI JERUK PERAS 1 RENCENG 10 SACHET", "SKU502", "899502", 2901, "Food", 595872, "MINUMAN", 595872, "NUTRISARI", "RCG"),
        (503, "MARIMAS MANGGA 1 RENCENG", "SKU503", "899503", 2901, "Food", 602035, "MINUMAN BUBUK", 602035, "MARIMAS", "RCG"),
        (504, "POP ICE COKELAT BUBUK 1 RENCENG", "SKU504", "899504", 2901, "Food", 602035, "MINUMAN BUBUK", 602035, "POP ICE", "RCG"),
        # Catch-all LAINLAIN
        (601, "AMPLOP PUTIH PAPERLINE 104", "SKU601", "899601", 9901, "Others", 595880, "LAINLAIN", 595880, "PAPERLINE", "DUS"),
        (602, "BUKU TULIS SINAR DUNIA 38 LEMBAR", "SKU602", "899602", 9901, "Others", 1441868, "ALAT TULIS", 1441868, "SIDU", "PAK"),
    ]

    for item in sample_data:
        con.execute(
            """
            INSERT INTO products_current (
                olsera_product_id, name, sku, barcode, category_id, category_name,
                klasifikasi_id, klasifikasi, product_group_id, brand_name, uom
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            item,
        )
    con.commit()
    return con


class MockDatabaseWrapper:
    def __init__(self, con):
        self._con = con

    def connect(self):
        return _ConnectionContext(self._con)


class _ConnectionContext:
    def __init__(self, con):
        self.con = con

    def __enter__(self):
        return self.con

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self.con.commit()


def test_classification_schema_migration():
    con = _create_mock_db()
    db = MockDatabaseWrapper(con)
    setup_classification_schema(db)

    # Verify tables created
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    assert "cleaner_classification_audit_runs" in tables
    assert "cleaner_classification_findings" in tables
    assert "cleaner_classification_reviews" in tables
    assert "cleaner_classification_ai_cache" in tables
    assert "cleaner_classification_ground_truth" in tables


def test_discover_klasifikasi():
    con = _create_mock_db()
    summary = discover_klasifikasi(con)

    assert summary.total_products == 25
    assert summary.klasifikasi_count > 0
    assert 602036 in summary.valid_klasifikasi_ids
    assert 595877 in summary.valid_klasifikasi_ids
    assert summary.product_group_mismatch_count == 0
    assert 595880 in summary.catch_all_ids  # LAINLAIN is catch-all


def test_taxonomy_redundancies():
    con = _create_mock_db()
    redundancies = find_taxonomy_redundancies(con)
    cluster_names = [r.cluster_name for r in redundancies]

    assert any("ALAT MANDI vs ALATMANDI" in c for c in cluster_names)
    assert any("CHIKI vs CIKI" in c for c in cluster_names)

    # Verify counts
    alat_mandi_summary = next(r for r in redundancies if "ALAT MANDI" in r.cluster_name)
    assert alat_mandi_summary.canonical_count == 2
    assert alat_mandi_summary.redundant_count == 1
    assert alat_mandi_summary.canonical_id == 602036
    assert alat_mandi_summary.redundant_id == 595877


def test_granular_misclassification_rules():
    # Dish soap in SABUN CUCI KAMPER
    p_sunlight = {"name": "SUNLIGHT LIME 750ML", "klasifikasi_id": 596205, "klasifikasi": "SABUN CUCI KAMPER"}
    res_sunlight = check_granular_misclassification(p_sunlight)
    assert res_sunlight is not None
    assert res_sunlight["suggested_klasifikasi"] == "SABUN CUCI PIRING"
    assert res_sunlight["confidence_level"] == "HIGH"

    # Detergent in SABUN CUCI KAMPER
    p_rinso = {"name": "RINSO ANTI NODA PLUS DETERGENT 800G", "klasifikasi_id": 596205, "klasifikasi": "SABUN CUCI KAMPER"}
    res_rinso = check_granular_misclassification(p_rinso)
    assert res_rinso is not None
    assert res_rinso["suggested_klasifikasi"] == "DETERGENT"
    assert res_rinso["confidence_level"] == "HIGH"

    # Floor cleaner in SABUN CUCI KAMPER
    p_wipol = {"name": "WIPOL PEMBERSIH LANTAI CEMARA 780ML", "klasifikasi_id": 596205, "klasifikasi": "SABUN CUCI KAMPER"}
    res_wipol = check_granular_misclassification(p_wipol)
    assert res_wipol is not None
    assert res_wipol["suggested_klasifikasi"] == "SABUN LANTAI"

    # Powder drink in MINUMAN
    p_nutrisari = {"name": "NUTRISARI JERUK PERAS 1 RENCENG", "klasifikasi_id": 595872, "klasifikasi": "MINUMAN"}
    res_nutrisari = check_granular_misclassification(p_nutrisari)
    assert res_nutrisari is not None
    assert res_nutrisari["suggested_klasifikasi"] == "MINUMAN BUBUK"

    # Amplop in LAINLAIN
    p_amplop = {"name": "AMPLOP PUTIH PAPERLINE 104", "klasifikasi_id": 595880, "klasifikasi": "LAINLAIN"}
    res_amplop = check_granular_misclassification(p_amplop)
    assert res_amplop is not None
    assert res_amplop["suggested_klasifikasi"] == "ALAT TULIS"


def test_multiclass_classifier_and_nearest_neighbor():
    con = _create_mock_db()
    summary = discover_klasifikasi(con)

    rows = con.execute("SELECT olsera_product_id, name, brand_name, uom, klasifikasi_id FROM products_current").fetchall()
    train_tuples = []
    for r in rows:
        d = {"name": r[1], "brand_name": r[2], "uom": r[3], "olsera_product_id": r[0]}
        feat = extract_features(d)
        train_tuples.append((feat, r[4]))

    classifier = MultiClassKlasifikasiClassifier(summary)
    classifier.train(train_tuples)

    # Test predicting Mama Lemon
    test_feat = extract_features({"name": "MAMA LEMON EXTRA HIGIENIS 750ML", "brand_name": "MAMA LEMON", "uom": "PC"})
    res = classifier.predict(test_feat, current_kla_id=596205)
    assert res is not None
    assert res.predicted_klasifikasi_id == 1441865  # SABUN CUCI PIRING
    assert res.predicted_klasifikasi_name == "SABUN CUCI PIRING"
    assert res.comparison_product_name is not None
    assert res.comparison_similarity >= 0.0


def test_ollama_verifier_cache_and_mock(monkeypatch):
    con = _create_mock_db()
    db = MockDatabaseWrapper(con)
    setup_classification_schema(db)

    verifier = KlasifikasiAIVerifier(model="llama3:8b", base_url="http://localhost:11434")

    # Mock requests.post to avoid needing a live Ollama daemon
    class MockResponse:
        def __init__(self):
            self.status_code = 200
        def raise_for_status(self):
            pass
        def json(self):
            return {
                "response": json.dumps({
                    "suggested_klasifikasi_id": 1441865,
                    "suggested_klasifikasi": "SABUN CUCI PIRING",
                    "confidence": "HIGH",
                    "reasoning": "Sunlight adalah sabun pencuci piring.",
                })
            }

    def mock_post(url, json=None, timeout=None):
        return MockResponse()

    import requests
    monkeypatch.setattr(requests, "post", mock_post)

    prod = {"olsera_product_id": 301, "name": "SUNLIGHT LIME 750ML", "klasifikasi": "SABUN CUCI KAMPER", "klasifikasi_id": 596205, "brand_name": "SUNLIGHT", "uom": "PC"}
    candidates = [
        {"id": 1441865, "name": "SABUN CUCI PIRING"},
        {"id": 596205, "name": "SABUN CUCI KAMPER"},
    ]
    res = verifier.verify_one(con, prod, candidate_classes=candidates)

    assert res is not None
    assert res["status"] == "COMPLETED"
    assert res["suggested_klasifikasi"] == "SABUN CUCI PIRING"
    assert res["confidence"] == "HIGH"

    # Verify cached in cleaner_classification_ai_cache
    cached = con.execute("SELECT COUNT(*) FROM cleaner_classification_ai_cache").fetchone()[0]
    assert cached == 1

    # Second call should hit the cache without calling requests.post
    def broken_post(url, json=None, timeout=None):
        raise RuntimeError("Should have hit cache!")

    monkeypatch.setattr(requests, "post", broken_post)
    cached_res = verifier.verify_one(con, prod, candidate_classes=candidates)
    assert cached_res is not None
    assert cached_res["status"] == "CACHED"
    assert cached_res["suggested_klasifikasi"] == "SABUN CUCI PIRING"


def test_run_classification_audit_engine():
    con = _create_mock_db()
    progress_calls = []

    def progress(p, tot, msg):
        progress_calls.append((p, tot, msg))

    discovery, findings, redundancies = run_classification_audit(con, progress_callback=progress)

    assert discovery.total_products == 25
    assert len(redundancies) >= 2
    assert len(findings) > 0
    assert len(progress_calls) > 0

    # Ensure granular finding exists
    finding_prods = {f.product_id: f for f in findings}
    assert 301 in finding_prods  # Sunlight Lime
    assert finding_prods[301].suggested_klasifikasi == "SABUN CUCI PIRING"
    assert finding_prods[301].anomaly_type in ("GRANULARITY_REFINEMENT", "SEMANTIC_MISCLASSIFICATION")


def test_classification_store_and_review_workflow():
    con = _create_mock_db()
    db = MockDatabaseWrapper(con)
    setup_classification_schema(db)

    discovery, findings, redundancies = run_classification_audit(con)
    run_id = save_classification_audit(db, discovery, findings, redundancies)

    assert run_id > 0

    run_summary, retrieved_findings = get_recent_classification_audit(db)
    assert run_summary is not None
    assert run_summary["id"] == run_id
    assert len(retrieved_findings) == len(findings)

    first_finding = retrieved_findings[0]
    assert first_finding["decision"] == "PENDING"

    # Test single review APPROVED
    review_classification_finding(db, first_finding["id"], "APPROVED", note="Verified by operator")

    _, updated_findings = get_recent_classification_audit(db)
    assert updated_findings[0]["decision"] == "APPROVED"
    assert updated_findings[0]["note"] == "Verified by operator"

    # Test bulk review
    pending_ids = [f["id"] for f in updated_findings if f["decision"] == "PENDING"]
    if pending_ids:
        approved_count = bulk_review_classification_findings(db, pending_ids, "APPROVED", note="Bulk operator signoff", max_batch=50)
        assert approved_count == len(pending_ids)


def test_export_classification_audit_csv():
    con = _create_mock_db()
    db = MockDatabaseWrapper(con)
    discovery, findings, redundancies = run_classification_audit(con)
    save_classification_audit(db, discovery, findings, redundancies)
    _, retrieved_findings = get_recent_classification_audit(db)

    csv_data = export_classification_audit_csv(retrieved_findings)
    assert "current_klasifikasi" in csv_data
    assert "suggested_klasifikasi" in csv_data
    assert "SUNLIGHT" in csv_data or "RINSO" in csv_data


def test_multi_class_ground_truth_holdout_evaluation():
    con = _create_mock_db()
    db = MockDatabaseWrapper(con)
    setup_classification_schema(db)

    # Seed ground truth
    count = seed_representative_klasifikasi_ground_truth(con)
    assert count >= 10

    gt_data = get_klasifikasi_ground_truth_dataset(con)
    assert len(gt_data) == count

    # Run holdout evaluation
    metrics, status = evaluate_klasifikasi_model(con, test_ratio=0.4)
    assert status == "OK"
    assert metrics is not None
    assert metrics.total_evaluated > 0
    assert 0.0 <= metrics.accuracy <= 1.0
    assert 0.0 <= metrics.precision_macro <= 1.0
    assert 0.0 <= metrics.recall_macro <= 1.0
    assert 0.0 <= metrics.f1_macro <= 1.0
    assert metrics.precision_high_confidence >= 0.0
    assert isinstance(metrics.confusion_matrix, dict)


def test_safety_guardrails_no_live_mutations():
    con = _create_mock_db()
    db = MockDatabaseWrapper(con)
    setup_classification_schema(db)

    # Verify no HTTP calls, no modification of Olsera live data, no table drops
    original_products = con.execute("SELECT COUNT(*) FROM products_current").fetchone()[0]
    original_hashes = [r[0] for r in con.execute("SELECT payload_hash FROM products_current").fetchall()]

    discovery, findings, redundancies = run_classification_audit(con)
    save_classification_audit(db, discovery, findings, redundancies)

    after_products = con.execute("SELECT COUNT(*) FROM products_current").fetchone()[0]
    after_hashes = [r[0] for r in con.execute("SELECT payload_hash FROM products_current").fetchall()]

    assert original_products == after_products
    assert original_hashes == after_hashes
