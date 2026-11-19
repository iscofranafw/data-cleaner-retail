"""Safe CSV exports for BEFORE baseline, current snapshot, and source-data comparisons.

Exports are local, never uploaded. GitHub publication is an operator decision.
"""
import csv
import hashlib
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

FIELDS = ["product_id", "name", "sku", "barcode", "klasifikasi", "category_name", "brand_name", "uom", "has_variant", "published", "pos_hidden"]
DIFF_FIELDS = ["product_id", "before_name", "after_name", "change_type", "before_sku", "after_sku", "before_barcode", "after_barcode"]


def _cell(value):
    if value is None:
        return ""
    value = str(value)
    # Prevent spreadsheet formula injection when users open the CSV in Excel.
    if value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n")):
        return "'" + value
    return value


def _atomic_csv(path, fields, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".export_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({key: _cell(row.get(key)) for key in fields})
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path


def _snapshot_products(con, run_id):
    import json
    data = {}
    for pid, raw in con.execute(
        "SELECT olsera_product_id, payload_json FROM product_snapshots WHERE run_id=? ORDER BY olsera_product_id", (run_id,)
    ):
        p = json.loads(raw)
        data[str(pid)] = {
            "product_id": str(pid),
            "name": p.get("name"),
            "sku": p.get("sku"),
            "barcode": p.get("barcode"),
            "klasifikasi": p.get("klasifikasi"),
            "category_name": p.get("category_name"),
            "brand_name": p.get("brand_name"),
            "uom": p.get("uom"),
            "has_variant": p.get("has_variant"),
            "published": p.get("published"),
            "pos_hidden": p.get("pos_hidden"),
        }
    return data


class CsvExporter:
    def __init__(self, db, directory):
        self.db = db
        self.directory = Path(directory)

    def _runs(self, con):
        rows = con.execute(
            "SELECT id FROM sync_runs WHERE resource='products' AND status='COMPLETED' ORDER BY id"
        ).fetchall()
        if not rows:
            raise ValueError("Belum ada Full Sync produk yang sukses. Jalankan sinkronisasi terlebih dahulu.")
        return [r[0] for r in rows]

    def _dated_file(self, prefix, run_id):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return self.directory / f"{prefix}_run{run_id}_{stamp}.csv"

    def export_baseline(self):
        """Preserve FIRST completed sync as baseline; never silently replace it."""
        with self.db.connect() as con:
            run_id = self._runs(con)[0]
            products = _snapshot_products(con, run_id)
        path = self.directory / f"products_before_run{run_id}.csv"
        if path.exists():
            return path, False
        # Exclusive claim prevents accidental overwrite of an existing baseline.
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            writer.writeheader()
            for item in products.values():
                writer.writerow({key: _cell(item.get(key)) for key in FIELDS})
        return path, True

    def export_current_and_diff(self):
        """Comparison is between Olsera full-sync snapshots, not asserted Cleaner updates."""
        with self.db.connect() as con:
            runs = self._runs(con)
            baseline_id, latest_id = runs[0], runs[-1]
            before = _snapshot_products(con, baseline_id)
            after = _snapshot_products(con, latest_id)
        self.export_baseline()
        now_file = self._dated_file("products_current", latest_id)
        _atomic_csv(now_file, FIELDS, after.values())
        changes = []
        for pid in sorted(before.keys() | after.keys(), key=int):
            old, new = before.get(pid), after.get(pid)
            if old is None:
                change_type = "ADDED_IN_OLSERA"
            elif new is None:
                change_type = "MISSING_IN_LATEST_SYNC"
            elif any(old[k] != new[k] for k in FIELDS if k != "product_id"):
                change_type = "CHANGED_IN_OLSERA"
            else:
                continue
            changes.append({
                "product_id": pid,
                "before_name": old.get("name") if old else None,
                "after_name": new.get("name") if new else None,
                "change_type": change_type,
                "before_sku": old.get("sku") if old else None,
                "after_sku": new.get("sku") if new else None,
                "before_barcode": old.get("barcode") if old else None,
                "after_barcode": new.get("barcode") if new else None,
            })
        diff_file = self._dated_file("products_comparison", latest_id)
        _atomic_csv(diff_file, DIFF_FIELDS, changes)
        return {"baseline_run": baseline_id, "latest_run": latest_id, "current": now_file, "comparison": diff_file, "changed_count": len(changes)}

    @staticmethod
    def sha256(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
