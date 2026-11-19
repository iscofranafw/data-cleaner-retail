"""Comprehensive tests for Smart Category Audit & AI Classification."""

import json
from unittest.mock import MagicMock, patch
import pytest

from olsera_cleaner.category.classifier import CategoryPatternClassifier
from olsera_cleaner.category.discovery import discover_categories
from olsera_cleaner.category.engine import CategoryFinding, run_category_audit
from olsera_cleaner.category.features import extract_features, normalize_text
from olsera_cleaner.category.schema import setup
from olsera_cleaner.category.store import (
    bulk_review_category_findings,
    export_category_audit_csv,
    get_recent_category_audit,
    review_category_finding,
    save_category_audit,
)
from olsera_cleaner.category.verifier import CategoryAIVerifier
from olsera_cleaner.database.repository import Database


def _setup_test_db(tmp_path):
    db = Database(tmp_path / "test.db")
    setup(db)
    with db.connect() as con:
        run_id = con.execute(
            "INSERT INTO sync_runs (resource, status) VALUES ('products', 'COMPLETED')"
        ).lastrowid
        con.execute(
            """
            INSERT INTO products_current (
                olsera_product_id, store_id, name, sku, barcode,
                category_id, category_name, klasifikasi_id, klasifikasi,
                brand_id, brand_name, product_group_id, uom,
                has_variant, published, pos_hidden, stock_qty, buy_price,
                sell_price, sell_price_pos, payload_json, payload_hash, sync_run_id
            ) VALUES
            (1, 10, 'INDOMIE GORENG SPESIAL 85GR', 'SKU-001', '899123456001', 2901, 'Food', 101, 'MIE INSTANT', 201, 'INDOMIE', 301, 'PCS', 0, 1, 0, '100', '2500', '3500', '3500', '{}', 'hash_1', ?),
            (2, 10, 'INDOMIE KUAH AYAM BAWANG', 'SKU-002', '899123456002', 2901, 'Food', 101, 'MIE INSTANT', 201, 'INDOMIE', 301, 'PCS', 0, 1, 0, '100', '2500', '3500', '3500', '{}', 'hash_2', ?),
            (3, 10, 'POCKY CHOCOLATE 47G', 'SKU-003', '899123456003', 2901, 'Food', 102, 'JAJANAN', 202, 'GLICO', 301, 'PCS', 0, 1, 0, '50', '8000', '9500', '9500', '{}', 'hash_3', ?),
            (4, 10, 'ROTI TAWAR SARI ROTI', 'SKU-004', '899123456004', 2901, 'Food', 103, 'ROTI', 203, 'SARI ROTI', 301, 'PCS', 0, 1, 0, '20', '12000', '15000', '15000', '{}', 'hash_4', ?),
            (5, 10, 'OREO BISKUIT VANILLA 137G', 'SKU-005', '899123456005', 2901, 'Food', 104, 'BISKUIT', 204, 'OREO', 301, 'PCS', 0, 1, 0, '40', '9000', '11000', '11000', '{}', 'hash_5', ?),
            (6, 10, 'INDOMIE GORENG IGA PENYET 85G', 'SKU-006', '899123456006', 9901, 'Others', 101, 'MIE INSTANT', 201, 'INDOMIE', 301, 'PCS', 0, 1, 0, '80', '2600', '3600', '3600', '{}', 'hash_6', ?),
            (7, 10, 'SABUN MANDI LIFEBUOY TOTAL 10', 'SKU-007', '899123456007', 9901, 'Others', 105, 'ALAT MANDI', 205, 'LIFEBUOY', 301, 'PCS', 0, 1, 0, '60', '4000', '5000', '5000', '{}', 'hash_7', ?),
            (8, 10, 'POCKY STRAWBERRY 45G', 'SKU-008', '', 9901, 'Others', 102, 'JAJANAN', 202, 'GLICO', 301, 'PCS', 0, 1, 0, '30', '8000', '9500', '9500', '{}', 'hash_8', ?),
            (9, 10, 'PRODUK MISTERIUS TANPA KATEGORI', '', '', NULL, NULL, 106, 'UMUM', NULL, NULL, 301, 'PCS', 0, 1, 0, '10', '1000', '2000', '2000', '{}', 'hash_9', ?),
            (10, 10, 'KERTAS HVS A4 SIDU', 'SKU-010', '899123456010', 8801, 'Stationery', 107, 'ATK', 206, 'SIDU', 301, 'PCS', 0, 1, 0, '15', '45000', '50000', '50000', '{}', 'hash_10', ?)
            """,
            (run_id, run_id, run_id, run_id, run_id, run_id, run_id, run_id, run_id, run_id),
        )
    return db


def test_schema_setup_is_additive(tmp_path):
    db = Database(tmp_path / "test.db")
    setup(db)
    with db.connect() as con:
        tables = [
            row[0]
            for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        ]
        assert "cleaner_category_audit_runs" in tables
        assert "cleaner_category_findings" in tables
        assert "cleaner_category_reviews" in tables
        assert "cleaner_category_ai_cache" in tables
        # Core schema version must remain 1
        assert con.execute("SELECT version FROM schema_info").fetchone()[0] == 1


def test_category_discovery_valid_and_uncategorized(tmp_path):
    db = _setup_test_db(tmp_path)
    with db.connect() as con:
        summary = discover_categories(con)

    assert summary.total_products == 10
    assert summary.categorized_count == 9
    assert summary.uncategorized_count == 1
    assert 9 in summary.uncategorized_product_ids

    assert 2901 in summary.categories
    assert summary.categories[2901].name == "Food"
    assert summary.categories[2901].product_count == 5
    assert not summary.categories[2901].is_catch_all

    assert 9901 in summary.categories
    assert summary.categories[9901].is_catch_all is True

    # Stationery has 1 product (< 5) so it is marked low-sample
    assert 8801 in summary.categories
    assert summary.categories[8801].is_low_sample is True


def test_category_discovery_inconsistent_categories(tmp_path):
    db = Database(tmp_path / "test.db")
    with db.connect() as con:
        run_id = con.execute("INSERT INTO sync_runs (resource, status) VALUES ('products', 'COMPLETED')").lastrowid
        con.execute(
            """
            INSERT INTO products_current (olsera_product_id, name, category_id, category_name, payload_json, payload_hash, sync_run_id)
            VALUES (1, 'Prod A', 100, 'Beverages', '{}', 'h1', ?),
                   (2, 'Prod B', 100, 'Minuman Dingin', '{}', 'h2', ?)
            """,
            (run_id, run_id),
        )
        summary = discover_categories(con)
    assert summary.inconsistent_count == 2
    assert set(summary.inconsistent_product_ids) == {1, 2}


def test_feature_extraction_preserves_raw_name():
    row = {
        "olsera_product_id": 99,
        "name": "  Indomie   Goreng Spesial 85gr ",
        "brand_name": "Indomie",
        "klasifikasi": "Mie Instant",
        "sku": "SKU-99",
        "barcode": "899000",
        "uom": "PCS",
    }
    feat = extract_features(row)
    assert feat.raw_name == "  Indomie   Goreng Spesial 85gr "
    assert feat.product_id == 99
    assert feat.normalized_name == "INDOMIE GORENG SPESIAL 85GR"
    assert "indomie" in feat.tokens
    assert "goreng" in feat.tokens
    assert feat.unit_size == "85GR"
    assert feat.sku == "SKU-99"
    assert feat.barcode == "899000"
    assert "indomie_goreng" in feat.bigrams
    assert feat.brand == "Indomie"
    assert feat.klasifikasi == "Mie Instant"


def test_feature_extraction_empty_sku_barcode():
    row = {
        "olsera_product_id": 100,
        "name": "Barang Tanpa Kode",
        "sku": "",
        "barcode": None,
    }
    feat = extract_features(row)
    assert feat.sku is None
    assert feat.barcode is None
    assert feat.raw_name == "Barang Tanpa Kode"


def test_classifier_pattern_learning_and_nearest_neighbor(tmp_path):
    db = _setup_test_db(tmp_path)
    with db.connect() as con:
        discovery = discover_categories(con)
        rows = con.execute("SELECT olsera_product_id, name, sku, barcode, category_id, category_name, klasifikasi, brand_name, uom, payload_hash FROM products_current").fetchall()

    train_data = []
    prod_dict = {}
    for r in rows:
        d = {"olsera_product_id": r[0], "name": r[1], "sku": r[2], "barcode": r[3], "category_id": r[4], "category_name": r[5], "klasifikasi": r[6], "brand_name": r[7], "uom": r[8]}
        feat = extract_features(d)
        train_data.append((feat, d["category_id"]))
        prod_dict[r[0]] = (d, feat)

    clf = CategoryPatternClassifier(discovery)
    clf.train(train_data)

    # Product 6: 'INDOMIE GORENG IGA PENYET 85G' is in Others (9901)
    # It must be predicted as Food (2901) with nearest neighbor INDOMIE GORENG SPESIAL
    d6, feat6 = prod_dict[6]
    match6 = clf.predict(feat6, d6["category_id"])
    assert match6 is not None
    assert match6.predicted_category_id == 2901
    assert match6.predicted_category_name == "Food"
    assert match6.similarity_score >= 0.45
    assert match6.comparison_product_id in (1, 2)
    assert "INDOMIE" in match6.comparison_product_name


def test_classifier_similar_tokens_different_context():
    """Verify discrimination when partial tokens overlap."""
    from types import SimpleNamespace
    mock_discovery = SimpleNamespace(
        categories={
            10: SimpleNamespace(name="Personal Care", is_catch_all=False, is_low_sample=False),
            20: SimpleNamespace(name="Fresh Produce", is_catch_all=False, is_low_sample=False),
        },
        valid_category_ids={10, 20},
    )
    clf = CategoryPatternClassifier(mock_discovery)
    items = [
        (extract_features({"id": 1, "name": "SABUN MANDI PAPAYA EXTRACT", "klasifikasi": "SABUN"}), 10),
        (extract_features({"id": 2, "name": "SABUN MANDI TOTAL 10", "klasifikasi": "SABUN"}), 10),
        (extract_features({"id": 3, "name": "BUAH PAPAYA CALIFORNIA SEGAR", "klasifikasi": "BUAH"}), 20),
        (extract_features({"id": 4, "name": "BUAH MANGGA SEGAR HARUM MANIS", "klasifikasi": "BUAH"}), 20),
    ]
    clf.train(items)

    query_soap = extract_features({"id": 5, "name": "SABUN CAIR PAPAYA 250ML", "klasifikasi": "SABUN"})
    match_soap = clf.predict(query_soap, None)
    assert match_soap.predicted_category_id == 10

    query_fruit = extract_features({"id": 6, "name": "BUAH PEPAYA LOKAL 1KG", "klasifikasi": "BUAH"})
    # Even if pepaya vs papaya differs, klasifikasi BUAH pushes it to 20
    match_fruit = clf.predict(query_fruit, None)
    assert match_fruit.predicted_category_id == 20


def test_ollama_ai_verification_valid_json_and_caching(tmp_path):
    db = Database(tmp_path / "test.db")
    setup(db)
    verifier = CategoryAIVerifier(model="llama3:8b")

    product = {
        "olsera_product_id": 101,
        "name": "KENTANG GORENG CRISPY",
        "category_name": "Others",
        "klasifikasi": "SNACK",
    }
    valid_categories = [{"id": 2901, "name": "Food"}, {"id": 9901, "name": "Others"}]

    mock_resp = {
        "response": json.dumps({
            "suggested_category_id": 2901,
            "suggested_category_name": "Food",
            "confidence": "HIGH",
            "reasoning": "Produk adalah kentang goreng yang merupakan makanan/snack",
        })
    }

    with patch("requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = mock_resp

        with db.connect() as con:
            result = verifier.verify_one(con, product, valid_categories)
            assert result["status"] == "COMPLETED"
            assert result["suggested_category_id"] == 2901
            assert result["confidence"] == "HIGH"
            assert "makanan" in result["reasoning"]

            # Second call should be CACHED without calling requests.post again
            cached_result = verifier.verify_one(con, product, valid_categories)
            assert cached_result["status"] == "CACHED"
            assert cached_result["suggested_category_id"] == 2901
            assert mock_post.call_count == 1


def test_ollama_ai_verification_invalid_category_rejected(tmp_path):
    db = Database(tmp_path / "test.db")
    setup(db)
    verifier = CategoryAIVerifier(model="llama3:8b")

    product = {"olsera_product_id": 102, "name": "UNKNOWN PRODUCT"}
    valid_categories = [{"id": 2901, "name": "Food"}]

    # LLM hallucinates non-existent category 9999
    mock_resp = {
        "response": json.dumps({
            "suggested_category_id": 9999,
            "suggested_category_name": "Cosmetics",
            "confidence": "HIGH",
            "reasoning": "Hallucinated category",
        })
    }

    with patch("requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = mock_resp

        with db.connect() as con:
            result = verifier.verify_one(con, product, valid_categories)
            # Rejected and falls back safely
            assert result["status"] == "FALLBACK"
            assert result["suggested_category_id"] is None


def test_ollama_ai_verification_timeout_fallback(tmp_path):
    db = Database(tmp_path / "test.db")
    setup(db)
    verifier = CategoryAIVerifier(model="llama3:8b")

    product = {"olsera_product_id": 103, "name": "MIE SEDAP GORENG"}
    valid_categories = [{"id": 2901, "name": "Food"}]

    import requests
    with patch("requests.post", side_effect=requests.exceptions.Timeout("Timeout")):
        with db.connect() as con:
            result = verifier.verify_one(con, product, valid_categories)
            assert result["status"] == "FALLBACK"
            assert result["suggested_category_id"] is None
            assert "tidak tersedia" in result["reasoning"]


def test_full_hybrid_audit_engine(tmp_path):
    db = _setup_test_db(tmp_path)
    progress_calls = []

    def progress_cb(pct, total, msg):
        progress_calls.append((pct, msg))

    with db.connect() as con:
        discovery, findings = run_category_audit(con, progress_callback=progress_cb)

    assert len(progress_calls) > 0
    assert len(findings) >= 2  # Product 6 (Indomie) and Product 8 (Pocky) and Product 9 (Uncategorized)

    indomie_finding = next((f for f in findings if f.product_id == 6), None)
    assert indomie_finding is not None
    assert indomie_finding.current_category_name == "Others"
    assert indomie_finding.suggested_category_name == "Food"
    assert indomie_finding.confidence_score >= 0.45
    assert indomie_finding.confidence_level in ("HIGH", "MEDIUM")
    assert "Food" in indomie_finding.reason

    pocky_finding = next((f for f in findings if f.product_id == 8), None)
    assert pocky_finding is not None
    assert pocky_finding.suggested_category_name == "Food"

    uncat_finding = next((f for f in findings if f.product_id == 9), None)
    assert uncat_finding is not None
    assert uncat_finding.classification_method == "UNCATEGORIZED_RULE"


def test_audit_store_save_get_and_review(tmp_path):
    db = _setup_test_db(tmp_path)
    with db.connect() as con:
        discovery, findings = run_category_audit(con)

    run_id = save_category_audit(db, discovery, findings)
    assert run_id > 0

    run_summary, loaded_findings = get_recent_category_audit(db)
    assert run_summary["id"] == run_id
    assert run_summary["findings_count"] == len(findings)
    assert len(loaded_findings) == len(findings)

    first_finding = loaded_findings[0]
    assert first_finding["decision"] == "PENDING"

    # Review single finding: APPROVE
    review_category_finding(db, first_finding["id"], "APPROVED", note="Disetujui operator")
    _, updated = get_recent_category_audit(db)
    assert next(f for f in updated if f["id"] == first_finding["id"])["decision"] == "APPROVED"

    # Review single finding: REJECT
    second_finding = loaded_findings[1]
    review_category_finding(db, second_finding["id"], "REJECTED", note="Ditolak")
    _, updated2 = get_recent_category_audit(db)
    assert next(f for f in updated2 if f["id"] == second_finding["id"])["decision"] == "REJECTED"

    # Review single finding: DEFER
    review_category_finding(db, second_finding["id"], "DEFERRED", note="Tunda dulu")
    _, updated3 = get_recent_category_audit(db)
    assert next(f for f in updated3 if f["id"] == second_finding["id"])["decision"] == "DEFERRED"


def test_bulk_review_with_limit_cap(tmp_path):
    db = _setup_test_db(tmp_path)
    with db.connect() as con:
        discovery, findings = run_category_audit(con)
    save_category_audit(db, discovery, findings)
    _, loaded = get_recent_category_audit(db)

    ids = [f["id"] for f in loaded]
    # Test bulk approve capped at 2
    count = bulk_review_category_findings(db, ids, "APPROVED", max_batch=2)
    assert count == 2

    _, updated = get_recent_category_audit(db)
    approved_items = [f for f in updated if f["decision"] == "APPROVED"]
    assert len(approved_items) == 2


def test_source_changed_detection(tmp_path):
    db = _setup_test_db(tmp_path)
    with db.connect() as con:
        discovery, findings = run_category_audit(con)
    save_category_audit(db, discovery, findings)

    # Simulate product update in products_current after audit was generated
    with db.connect() as con:
        con.execute("UPDATE products_current SET payload_hash = 'changed_hash' WHERE olsera_product_id = 6")

    _, loaded = get_recent_category_audit(db)
    f6 = next(f for f in loaded if f["product_id"] == 6)
    assert f6["source_changed"] is True

    f8 = next(f for f in loaded if f["product_id"] == 8)
    assert f8["source_changed"] is False


def test_csv_export_format_and_injection_defense():
    findings = [
        {
            "id": 1,
            "product_id": 100,
            "product_name": "=CMD('calc')",
            "sku": "+SKU123",
            "barcode": "@12345",
            "current_category_id": 9901,
            "current_category_name": "Others",
            "suggested_category_id": 2901,
            "suggested_category_name": "Food",
            "confidence_score": 0.85,
            "confidence_level": "HIGH",
            "classification_method": "TFIDF",
            "decision": "APPROVED",
            "comparison_product_id": 1,
            "comparison_product_name": "-DANGEROUS",
            "comparison_similarity": 0.88,
            "reason": "Cocok dengan Food",
            "source_changed": False,
        }
    ]
    csv_str = export_category_audit_csv(findings)
    assert "'=CMD('calc')" in csv_str
    assert "'+SKU123" in csv_str
    assert "'@12345" in csv_str
    assert "'-DANGEROUS" in csv_str


def test_security_boundary_no_live_api_writes(tmp_path):
    """Verify that under no circumstances does the category audit call external write APIs."""
    db = _setup_test_db(tmp_path)
    with patch("requests.post") as mock_post, patch("requests.put") as mock_put, patch("requests.delete") as mock_del:
        with db.connect() as con:
            discovery, findings = run_category_audit(con)
        run_id = save_category_audit(db, discovery, findings)
        _, loaded = get_recent_category_audit(db)
        for f in loaded:
            review_category_finding(db, f["id"], "APPROVED")

        # None of the Olsera HTTP endpoints should have been called
        assert mock_put.call_count == 0
        assert mock_del.call_count == 0
        # post is only for local Ollama localhost:11434 if enabled
        for call_args in mock_post.call_args_list:
            url = call_args[0][0] if call_args[0] else call_args[1].get("url", "")
            assert "olsera" not in url.lower()
