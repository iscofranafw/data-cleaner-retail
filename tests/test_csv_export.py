import csv
from olsera_cleaner.database.repository import Database
from olsera_cleaner.reporting import CsvExporter


def test_before_immutable_and_comparison(tmp_path):
    db = Database(tmp_path / "test.db")
    export = CsvExporter(db, tmp_path / "reports")
    first = db.start_run("products")
    db.stage_page(first, 1, [{"id": 1, "name": "aqua 600ml", "sku": "001", "barcode": "0001", "store_id": 1}], "products")
    db.finish_run(first, 1, 1, "products")
    original, created = export.export_baseline()
    assert created
    assert "aqua 600ml" in original.read_text(encoding="utf-8")
    second = db.start_run("products")
    db.stage_page(second, 1, [{"id": 1, "name": "AQUA 600 ML", "sku": "001", "barcode": "0001", "store_id": 1}], "products")
    db.finish_run(second, 1, 1, "products")
    again, created = export.export_baseline()
    assert again == original and not created
    assert "aqua 600ml" in original.read_text(encoding="utf-8")
    result = export.export_current_and_diff()
    with result["comparison"].open(encoding="utf-8", newline="") as f:
        changes = list(csv.DictReader(f))
    assert len(changes) == 1
    assert changes[0]["before_name"] == "aqua 600ml"
    assert changes[0]["after_name"] == "AQUA 600 ML"
    assert changes[0]["change_type"] == "CHANGED_IN_OLSERA"


def test_csv_formula_is_escaped(tmp_path):
    db = Database(tmp_path / "test.db")
    run = db.start_run("products")
    db.stage_page(run, 1, [{"id": 2, "name": "=HYPERLINK(foo)", "store_id": 1}], "products")
    db.finish_run(run, 1, 1, "products")
    path, _ = CsvExporter(db, tmp_path).export_baseline()
    assert "'=HYPERLINK(foo)" in path.read_text(encoding="utf-8")
