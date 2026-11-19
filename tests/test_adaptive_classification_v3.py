"""End-to-End Acceptance Tests for Dynamic & Adaptive Classification Engine V3 (Phase I)."""

import json
import sqlite3
import pytest

from olsera_cleaner.category.features import extract_features
from olsera_cleaner.classification.classifier import AdaptiveKlasifikasiClassifier
from olsera_cleaner.classification.deduplication import detect_taxonomy_redundancies
from olsera_cleaner.classification.engine import run_classification_audit
from olsera_cleaner.classification.feedback import get_negative_feedback_map, record_operator_feedback
from olsera_cleaner.classification.model_registry import ModelRegistry
from olsera_cleaner.classification.monitoring import generate_monitoring_report
from olsera_cleaner.classification.open_set import (
    NewClassCandidateProduct,
    _extract_product_tokens,
    cluster_open_set_products,
    persist_new_class_proposals,
)
from olsera_cleaner.classification.registry import TaxonomyRegistry
from olsera_cleaner.classification.schema import setup as setup_classification_schema
from olsera_cleaner.classification.store import (
    bulk_review_classification_findings,
    get_recent_classification_audit,
    review_classification_finding,
    save_classification_audit,
)


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


def _create_base_db():
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
            payload_hash TEXT DEFAULT 'hash_initial',
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

    initial_products = [
        # Snack classes
        (1, "CHIKI BALL KEJU 55G", "S01", "899001", 2901, "Food", 101, "CHIKI", 101, "CHIKI", "BKS"),
        (2, "CHIKI TWIST JAGUNG 40G", "S02", "899002", 2901, "Food", 101, "CHIKI", 101, "CHIKI", "BKS"),
        (3, "CIKI ZEKER COKELAT 30G", "S03", "899003", 2901, "Food", 102, "CIKI", 102, "CIKI", "BKS"),
        # Bath tools
        (4, "SIKAT GIGI PEPSODENT SOFT", "B01", "899004", 9901, "Others", 201, "ALAT MANDI", 201, "PEPSODENT", "PCS"),
        (5, "ODOL PEPSODENT ACTION 120G", "B02", "899005", 9901, "Others", 201, "ALAT MANDI", 201, "PEPSODENT", "PCS"),
        (6, "SIKAT GIGI FORMULA CLEAN", "B03", "899006", 9901, "Others", 202, "ALATMANDI", 202, "FORMULA", "PCS"),
        # Cleaning products
        (7, "SUNLIGHT LIME 750ML POUCH", "C01", "899007", 9901, "Others", 301, "SABUN CUCI PIRING", 301, "SUNLIGHT", "PC"),
        (8, "MAMA LEMON JERUK NIPIS 780ML", "C02", "899008", 9901, "Others", 301, "SABUN CUCI PIRING", 301, "MAMA LEMON", "PC"),
        (9, "RINSO ANTI NODA PLUS 800G", "C03", "899009", 9901, "Others", 302, "DETERGENT", 302, "RINSO", "PC"),
        (10, "DAIA BUNGA DETERJEN 850G", "C04", "899010", 9901, "Others", 302, "DETERGENT", 302, "DAIA", "PC"),
        # Generic bucket
        (11, "PEMBERSIH KACA CLING REFILL 450ML", "X01", "899011", 9901, "Others", 999, "LAINLAIN", 999, "CLING", "PC"),
        (12, "MR MUSCLE PEMBERSIH KACA 500ML", "X02", "899012", 9901, "Others", 999, "LAINLAIN", 999, "MR MUSCLE", "BTL"),
    ]

    con.executemany(
        """
        INSERT INTO products_current (
            olsera_product_id, name, sku, barcode, category_id, category_name,
            klasifikasi_id, klasifikasi, product_group_id, brand_name, uom
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        initial_products,
    )
    con.commit()
    return con


def test_phase1_and_2_dynamic_discovery_and_new_classification():
    con = _create_base_db()
    registry = TaxonomyRegistry()
    snap1 = registry.discover_live_taxonomy(con)

    assert snap1.classifications_count == 7  # CHIKI, CIKI, ALAT MANDI, ALATMANDI, SABUN CUCI PIRING, DETERGENT, LAINLAIN (7 total or 61 in real)
    assert 101 in snap1.valid_ids
    assert snap1.klasifikasi_map[999].is_catch_all is True

    # 1. Add completely new classification dynamically without code change
    new_class_products = [
        (13, "ROKOK SAMPOERNA MILD 16", "R01", "899013", 9901, "Others", 401, "ROKOK", 401, "SAMPOERNA", "BKS"),
        (14, "ROKOK SURYA 12 FILTER", "R02", "899014", 9901, "Others", 401, "ROKOK", 401, "GUDANG GARAM", "BKS"),
    ]
    con.executemany(
        """
        INSERT INTO products_current (
            olsera_product_id, name, sku, barcode, category_id, category_name,
            klasifikasi_id, klasifikasi, product_group_id, brand_name, uom
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        new_class_products,
    )
    con.commit()

    # 2. Rediscover taxonomy dynamically
    snap2 = registry.discover_live_taxonomy(con)
    assert 401 in snap2.valid_ids
    assert snap2.klasifikasi_map[401].name == "ROKOK"
    assert snap2.version_hash != snap1.version_hash

    # 3. Check diff detection
    diff = registry.diff_snapshots(snap1, snap2)
    assert 401 in diff.added_ids
    assert diff.has_changes is True


def test_phase3_and_4_dynamic_model_refresh_and_new_candidate():
    con = _create_base_db()
    db = MockDatabaseWrapper(con)
    setup_classification_schema(db)

    registry = TaxonomyRegistry()
    model_reg = ModelRegistry(registry)

    # Initial snapshot & candidate model
    snap1 = registry.discover_live_taxonomy(con)
    _, info1 = model_reg.train_candidate_model(con, snap1)
    promoted, msg, _ = model_reg.evaluate_and_promote(con, info1)
    assert promoted is True

    active1 = model_reg.get_active_model_info(con)
    assert active1 is not None
    assert active1.model_version == info1.model_version

    # Insert new classification & products
    con.executemany(
        """
        INSERT INTO products_current (
            olsera_product_id, name, sku, barcode, category_id, category_name,
            klasifikasi_id, klasifikasi, product_group_id, brand_name, uom
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (15, "INDOMIE GORENG SPESIAL", "M01", "899015", 2901, "Food", 501, "MIE INSTANT", 501, "INDOMIE", "BKS"),
            (16, "SEDAP GORENG AYAM KRISPI", "M02", "899016", 2901, "Food", 501, "MIE INSTANT", 501, "MIE SEDAAP", "BKS"),
        ],
    )
    con.commit()

    snap2 = registry.discover_live_taxonomy(con)
    assert model_reg.is_model_stale(con, snap2) is True

    # Refresh model dynamically without source code change
    classifier2, info2 = model_reg.train_candidate_model(con, snap2)
    assert 501 in classifier2.target_klasifikasi_ids

    # New product classified using newly trained dynamic model
    new_prod_feat = extract_features({"name": "INDOMIE AYAM BAWANG", "brand_name": "INDOMIE", "uom": "BKS"})
    res = classifier2.predict(new_prod_feat, current_kla_id=999)
    assert res.predicted_klasifikasi_id == 501
    assert res.predicted_klasifikasi_name == "MIE INSTANT"


def test_phase5_open_set_new_class_discovery():
    con = _create_base_db()
    registry = TaxonomyRegistry()
    snap = registry.discover_live_taxonomy(con)

    # Unknown product that doesn't fit existing classes (e.g. drilling machine / power tool)
    classifier = AdaptiveKlasifikasiClassifier(snapshot=snap, open_set_threshold=0.28)
    # Train only on bath and snack
    classifier.train([
        (extract_features({"name": "SIKAT GIGI PEPSODENT"}), 201, True),
        (extract_features({"name": "CHIKI BALL KEJU"}), 101, True),
    ])

    unknown_prod_feat = extract_features({"name": "BOR LISTRIK BOSCH DRILL MACHINE", "brand_name": "BOSCH", "uom": "UNIT"})
    res = classifier.predict(unknown_prod_feat, current_kla_id=999)

    # Verify open-set detection
    assert res.is_unknown is True
    assert res.predicted_klasifikasi_name == "UNKNOWN"

    # Verify clustering of multiple unknown products
    candidates = [
        NewClassCandidateProduct(product_id=501, name="BOR LISTRIK BOSCH IMPACT DRILL", tokens=_extract_product_tokens("BOR LISTRIK BOSCH IMPACT DRILL"), max_known_similarity=0.15),
        NewClassCandidateProduct(product_id=502, name="BOR LISTRIK MAKITA ROTARY HAMMER", tokens=_extract_product_tokens("BOR LISTRIK MAKITA ROTARY HAMMER"), max_known_similarity=0.12),
    ]
    proposals = cluster_open_set_products(candidates, snap, min_cluster_size=2)
    assert len(proposals) == 1
    assert "BOR" in proposals[0].proposed_name or "LISTRIK" in proposals[0].proposed_name
    assert proposals[0].item_count == 2

    # Verify saving to proposals table
    db = MockDatabaseWrapper(con)
    setup_classification_schema(db)
    inserted = persist_new_class_proposals(con, proposals)
    assert inserted == 1

    row = con.execute("SELECT proposed_name, item_count, status FROM cleaner_classification_new_class_proposals").fetchone()
    assert row[1] == 2
    assert row[2] == "PENDING"


def test_phase6_dynamic_deduplication_without_hardcoded_pairs():
    con = _create_base_db()
    # Add an arbitrary un-hardcoded pair with spacing and phonetic differences
    con.executescript("""
        INSERT INTO products_current (olsera_product_id, name, klasifikasi_id, klasifikasi, product_group_id)
        VALUES
            (601, "ROKOK GUDANG GARAM MERAH", 701, "ROKOK FILTER", 701),
            (602, "ROKOK DJARUM SUPER 12", 701, "ROKOK FILTER", 701),
            (603, "ROKOK KAMPUS FILTER 12", 702, "ROKOKFILTER", 702);
    """)
    con.commit()

    registry = TaxonomyRegistry()
    snap = registry.discover_live_taxonomy(con)
    redundancies = detect_taxonomy_redundancies(con, snap)

    cluster_names = [r.cluster_name for r in redundancies]
    # Verify both standard test cases AND the newly injected arbitrary pair are detected algorithmically!
    assert any("ALAT MANDI vs ALATMANDI" in c for c in cluster_names)
    assert any("CHIKI vs CIKI" in c for c in cluster_names)
    assert any("ROKOK FILTER vs ROKOKFILTER" in c for c in cluster_names)

    rf_summary = next(r for r in redundancies if "ROKOK FILTER" in r.cluster_name)
    assert rf_summary.canonical_id == 701
    assert rf_summary.redundant_id == 702
    assert rf_summary.redundancy_type == "SYNTAX_SPACING"


def test_phase7_operator_feedback_loop_positive_and_negative():
    con = _create_base_db()
    db = MockDatabaseWrapper(con)
    setup_classification_schema(db)

    # 1. Operator REJECTS recommendation
    # Product #1 was suggested to class 301 (SABUN CUCI PIRING) incorrectly
    record_operator_feedback(
        con,
        product_id=1,
        decision="REJECTED",
        suggested_klasifikasi_id=301,
        suggested_klasifikasi="SABUN CUCI PIRING",
        reason="Chiki is food, not dish soap",
    )

    neg_map = get_negative_feedback_map(con)
    assert 1 in neg_map
    assert 301 in neg_map[1]

    # Verify classifier suppresses the rejected class
    registry = TaxonomyRegistry()
    snap = registry.discover_live_taxonomy(con)
    classifier = AdaptiveKlasifikasiClassifier(snap, negative_feedback_map=neg_map)
    classifier.train([(extract_features({"name": "SUNLIGHT CUCI PIRING"}), 301, False)])

    feat_chiki = extract_features({"name": "CHIKI BALL KEJU", "product_id": 1})
    feat_chiki.product_id = 1
    res = classifier.predict(feat_chiki, current_kla_id=101)
    # Target class 301 must NOT be chosen because it's in negative feedback!
    assert res.predicted_klasifikasi_id != 301

    # 2. Operator APPROVES recommendation
    record_operator_feedback(
        con,
        product_id=11,  # Cling pembersih kaca
        decision="APPROVED",
        suggested_klasifikasi_id=301,
        suggested_klasifikasi="SABUN CUCI PIRING",
    )
    # Product #11 becomes verified ground truth
    gt_row = con.execute("SELECT true_klasifikasi_id FROM cleaner_classification_ground_truth WHERE product_id = 11").fetchone()
    assert gt_row is not None
    assert gt_row[0] == 301


def test_phase8_model_quality_gate_and_rollback():
    con = _create_base_db()
    db = MockDatabaseWrapper(con)
    setup_classification_schema(db)

    registry = TaxonomyRegistry()
    model_reg = ModelRegistry(registry)

    # Establish active model with good F1 score
    con.execute(
        """
        INSERT INTO cleaner_classification_models
            (model_version, taxonomy_version, status, trained_on_items, macro_f1, accuracy, created_at)
        VALUES ('mod_v1_good', 'tax_v1', 'ACTIVE', 50, 0.95, 0.96, CURRENT_TIMESTAMP)
        """
    )
    con.commit()

    # Candidate model with low quality
    cand_info = ModelRegistry(registry).train_candidate_model(con, registry.discover_live_taxonomy(con))[1]

    # Mock evaluate_and_promote with severe regression
    import olsera_cleaner.classification.model_registry as mr_mod
    original_eval = mr_mod.evaluate_klasifikasi_model

    class DegradedMetrics:
        f1_macro = 0.50
        accuracy = 0.52

    mr_mod.evaluate_klasifikasi_model = lambda con, test_ratio: (DegradedMetrics(), "OK")
    try:
        ok, msg, _ = model_reg.evaluate_and_promote(con, cand_info)
        assert ok is False
        assert "regresi" in msg.lower()

        # Active model is still mod_v1_good!
        active = model_reg.get_active_model_info(con)
        assert active.model_version == "mod_v1_good"
        assert active.status == "ACTIVE"
    finally:
        mr_mod.evaluate_klasifikasi_model = original_eval


def test_phase9_safety_boundary_zero_live_writes():
    con = _create_base_db()
    db = MockDatabaseWrapper(con)
    setup_classification_schema(db)

    count_before = con.execute("SELECT COUNT(*) FROM products_current").fetchone()[0]
    hashes_before = [r[0] for r in con.execute("SELECT payload_hash FROM products_current").fetchall()]

    # Run complete audit, deduplication, feedback, and open-set clustering
    snap, findings, redundancies = run_classification_audit(con)
    run_id = save_classification_audit(db, snap, findings, redundancies)

    count_after = con.execute("SELECT COUNT(*) FROM products_current").fetchone()[0]
    hashes_after = [r[0] for r in con.execute("SELECT payload_hash FROM products_current").fetchall()]

    assert count_before == count_after
    assert hashes_before == hashes_after
    assert run_id > 0
