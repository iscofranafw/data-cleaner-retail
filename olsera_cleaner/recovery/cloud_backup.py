"""Stateless cloud backup, granular export, and secure ZIP recovery with Zip-Slip protection."""

from datetime import datetime, timezone
import hashlib
import io
import json
import sqlite3
from typing import Any, Dict, List, Tuple
import zipfile

from olsera_cleaner.database.schema import SCHEMA_VERSION

MAX_ALLOWED_UNCOMPRESSED_BYTES = 100 * 1024 * 1024  # 100 MB safety limit
MAX_ALLOWED_FILES = 100


class SecurityZipError(PermissionError):
    """Raised when an uploaded ZIP file violates security constraints (e.g. Zip-Slip)."""
    pass


class CloudBackupEngine:
    """Manages granular exports, full ZIP packaging, and secure verification/restoration."""

    def __init__(self, con, expected_store_id: str = ""):
        self.con = con
        self.expected_store_id = str(expected_store_id or "").strip()

    def get_products_data(self) -> List[Dict[str, Any]]:
        """Extract products from current snapshot."""
        cur = self.con.execute(
            """
            SELECT olsera_product_id, store_id, name, sku, barcode, klasifikasi_id,
                   klasifikasi, category_id, category_name, brand_id, brand_name,
                   product_group_id, uom, stock_qty, sell_price_pos, payload_hash, modified_time
            FROM products_current
            ORDER BY olsera_product_id
            """
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def get_category_findings_data(self) -> List[Dict[str, Any]]:
        """Extract Category Audit findings and reviews."""
        try:
            cur = self.con.execute(
                """
                SELECT f.product_id, p.name, f.current_category_id, f.suggested_category_id,
                       f.confidence, f.review_status, f.created_at
                FROM cleaner_category_findings f
                LEFT JOIN products_current p ON p.olsera_product_id = f.product_id
                ORDER BY f.id
                """
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        except (sqlite3.OperationalError, sqlite3.DatabaseError):
            return []

    def get_classification_findings_data(self) -> List[Dict[str, Any]]:
        """Extract Classification V3 findings and review actions."""
        try:
            cur = self.con.execute(
                """
                SELECT f.id, f.product_id, p.name, f.current_klasifikasi_id, f.current_klasifikasi,
                       f.suggested_klasifikasi_id, f.suggested_klasifikasi, f.anomaly_type,
                       f.confidence_score, f.confidence_level, f.classification_method,
                       f.reason, COALESCE(r.decision, 'PENDING') as review_status, f.created_at
                FROM cleaner_classification_findings f
                LEFT JOIN products_current p ON p.olsera_product_id = f.product_id
                LEFT JOIN cleaner_classification_reviews r ON r.finding_id = f.id
                ORDER BY f.id
                """
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        except (sqlite3.OperationalError, sqlite3.DatabaseError):
            return []

    def get_approvals_data(self) -> List[Dict[str, Any]]:
        """Extract all operator approvals and rejections across category and classification."""
        approvals = []
        try:
            cur = self.con.execute(
                """
                SELECT f.product_id, f.current_klasifikasi_id, f.suggested_klasifikasi_id,
                       r.decision, r.reviewed_at as created_at, 'CLASSIFICATION' as audit_type
                FROM cleaner_classification_reviews r
                JOIN cleaner_classification_findings f ON f.id = r.finding_id
                WHERE r.decision IN ('APPROVED', 'REJECTED')
                """
            )
            for r in cur.fetchall():
                approvals.append({
                    "product_id": r[0],
                    "old_id": r[1],
                    "new_id": r[2],
                    "decision": r[3],
                    "timestamp": r[4],
                    "audit_type": r[5],
                })
        except (sqlite3.OperationalError, sqlite3.DatabaseError):
            pass

        try:
            cur = self.con.execute(
                """
                SELECT f.product_id, f.current_category_id, f.suggested_category_id,
                       r.decision, r.reviewed_at as created_at, 'CATEGORY' as audit_type
                FROM cleaner_category_reviews r
                JOIN cleaner_category_findings f ON f.id = r.finding_id
                WHERE r.decision IN ('APPROVED', 'REJECTED')
                """
            )
            for r in cur.fetchall():
                approvals.append({
                    "product_id": r[0],
                    "old_id": r[1],
                    "new_id": r[2],
                    "decision": r[3],
                    "timestamp": r[4],
                    "audit_type": r[5],
                })
        except (sqlite3.OperationalError, sqlite3.DatabaseError):
            pass

        return approvals

    def get_ground_truth_data(self) -> List[Dict[str, Any]]:
        """Extract verified classification ground truth."""
        try:
            cur = self.con.execute(
                "SELECT product_id, true_klasifikasi_id, true_klasifikasi, annotated_by, annotated_at, notes FROM cleaner_classification_ground_truth"
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        except (sqlite3.OperationalError, sqlite3.DatabaseError):
            return []

    def get_negative_feedback_data(self) -> List[Dict[str, Any]]:
        """Extract rejected negative associations."""
        try:
            cur = self.con.execute(
                "SELECT product_id, rejected_klasifikasi_id, reason, created_at FROM cleaner_classification_negative_feedback"
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        except (sqlite3.OperationalError, sqlite3.DatabaseError):
            return []

    def create_full_backup_zip(self, store_id: str = "") -> Tuple[bytes, Dict[str, Any]]:
        """Pack all operational and audit data into a verified ZIP archive with manifest."""
        target_store_id = str(store_id or self.expected_store_id or "").strip()
        if not target_store_id:
            row = self.con.execute("SELECT DISTINCT store_id FROM products_current WHERE store_id IS NOT NULL LIMIT 1").fetchone()
            if row and row[0]:
                target_store_id = str(row[0])

        products = self.get_products_data()
        category_findings = self.get_category_findings_data()
        classification_findings = self.get_classification_findings_data()
        approvals = self.get_approvals_data()
        ground_truth = self.get_ground_truth_data()
        negative_feedback = self.get_negative_feedback_data()

        files_to_pack: Dict[str, str] = {
            "products.json": json.dumps(products, ensure_ascii=False, indent=2),
            "category_findings.json": json.dumps(category_findings, ensure_ascii=False, indent=2),
            "classification_findings.json": json.dumps(classification_findings, ensure_ascii=False, indent=2),
            "approvals.json": json.dumps(approvals, ensure_ascii=False, indent=2),
            "ground_truth.json": json.dumps(ground_truth, ensure_ascii=False, indent=2),
            "negative_feedback.json": json.dumps(negative_feedback, ensure_ascii=False, indent=2),
        }

        checksums: Dict[str, str] = {}
        for fname, content in files_to_pack.items():
            checksums[fname] = hashlib.sha256(content.encode("utf-8")).hexdigest()

        manifest: Dict[str, Any] = {
            "backup_format_version": "1.0",
            "schema_version": SCHEMA_VERSION,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "store_id": target_store_id,
            "record_counts": {
                "products": len(products),
                "category_findings": len(category_findings),
                "classification_findings": len(classification_findings),
                "approvals": len(approvals),
                "ground_truth": len(ground_truth),
                "negative_feedback": len(negative_feedback),
            },
            "file_checksums": checksums,
        }

        manifest_content = json.dumps(manifest, ensure_ascii=False, indent=2)
        files_to_pack["manifest.json"] = manifest_content

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for fname, content in files_to_pack.items():
                zf.writestr(fname, content.encode("utf-8"))

        return zip_buffer.getvalue(), manifest

    def restore_from_backup_zip(self, zip_bytes: bytes) -> Dict[str, Any]:
        """Safely restore database state from a validated ZIP file with path-traversal guards."""
        if not zip_bytes or len(zip_bytes) < 10:
            raise ValueError("File cadangan kosong atau tidak valid.")

        zip_stream = io.BytesIO(zip_bytes)
        if not zipfile.is_zipfile(zip_stream):
            raise ValueError("File yang diunggah bukan arsip ZIP yang valid.")

        with zipfile.ZipFile(zip_stream, "r") as zf:
            infolist = zf.infolist()
            if len(infolist) > MAX_ALLOWED_FILES:
                raise SecurityZipError(f"Jumlah file dalam ZIP ({len(infolist)}) melebihi batas aman.")

            total_size = sum(info.file_size for info in infolist)
            if total_size > MAX_ALLOWED_UNCOMPRESSED_BYTES:
                raise SecurityZipError(f"Ukuran tidak terkompresi ({total_size / 1024 / 1024:.1f} MB) melebihi batas aman 100 MB.")

            for info in infolist:
                norm_name = info.filename.replace("\\", "/")
                if ".." in norm_name or norm_name.startswith("/") or norm_name.startswith("../"):
                    raise SecurityZipError(f"Pemberhentian Keamanan: Percobaan path traversal terdeteksi pada file '{info.filename}'.")

            names = zf.namelist()
            if "manifest.json" not in names:
                raise ValueError("Format tidak valid: 'manifest.json' tidak ditemukan di dalam file cadangan.")

            manifest_raw = zf.read("manifest.json").decode("utf-8")
            manifest = json.loads(manifest_raw)

            backup_store_id = str(manifest.get("store_id") or "").strip()
            if self.expected_store_id and backup_store_id and backup_store_id != self.expected_store_id:
                raise ValueError(
                    f"Pemberhentian Keamanan: File cadangan milik Store ID '{backup_store_id}', "
                    f"tidak sesuai dengan Store ID aktif toko Anda ('{self.expected_store_id}')."
                )

            expected_checksums = manifest.get("file_checksums", {})
            for fname, expected_hash in expected_checksums.items():
                if fname in names:
                    actual_content = zf.read(fname)
                    actual_hash = hashlib.sha256(actual_content).hexdigest()
                    if actual_hash != expected_hash:
                        raise ValueError(f"Integritas data rusak: Checksum file '{fname}' tidak cocok (file telah dimodifikasi).")

            with self.con:
                restored_counts = self._apply_restored_records(zf)

        return {
            "status": "RESTORE_COMPLETED",
            "store_id": backup_store_id,
            "created_at_utc": manifest.get("created_at_utc"),
            "restored_counts": restored_counts,
        }

    def _apply_restored_records(self, zf: zipfile.ZipFile) -> Dict[str, int]:
        """Apply records into database idempotently."""
        counts = {"products": 0, "approvals": 0, "ground_truth": 0, "negative_feedback": 0}

        if "products.json" in zf.namelist():
            products = json.loads(zf.read("products.json").decode("utf-8"))
            for p in products:
                pid = int(p["olsera_product_id"])
                raw_json = json.dumps(p)
                digest = p.get("payload_hash") or hashlib.sha256(raw_json.encode()).hexdigest()
                self.con.execute(
                    """
                    INSERT INTO products_current (
                        olsera_product_id, store_id, name, sku, barcode, klasifikasi_id,
                        klasifikasi, category_id, category_name, brand_id, brand_name,
                        product_group_id, uom, stock_qty, sell_price_pos, payload_json,
                        payload_hash, sync_run_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(olsera_product_id) DO UPDATE SET
                        name = excluded.name,
                        sku = excluded.sku,
                        barcode = excluded.barcode,
                        klasifikasi_id = excluded.klasifikasi_id,
                        klasifikasi = excluded.klasifikasi,
                        payload_json = excluded.payload_json
                    """,
                    (
                        pid, p.get("store_id"), str(p.get("name") or ""), p.get("sku"), p.get("barcode"),
                        p.get("klasifikasi_id"), p.get("klasifikasi"), p.get("category_id"), p.get("category_name"),
                        p.get("brand_id"), p.get("brand_name"), p.get("product_group_id"), p.get("uom"),
                        p.get("stock_qty"), p.get("sell_price_pos"), raw_json, digest,
                    ),
                )
                counts["products"] += 1

        if "ground_truth.json" in zf.namelist():
            gt_items = json.loads(zf.read("ground_truth.json").decode("utf-8"))
            for g in gt_items:
                self.con.execute(
                    """
                    INSERT INTO cleaner_classification_ground_truth
                        (product_id, true_klasifikasi_id, true_klasifikasi, annotated_by, annotated_at, notes)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(product_id) DO UPDATE SET
                        true_klasifikasi_id = excluded.true_klasifikasi_id,
                        true_klasifikasi = excluded.true_klasifikasi,
                        notes = excluded.notes
                    """,
                    (
                        int(g["product_id"]), int(g["true_klasifikasi_id"]), g.get("true_klasifikasi", ""),
                        g.get("annotated_by", "restore"), g.get("annotated_at", "NOW"), g.get("notes", ""),
                    ),
                )
                counts["ground_truth"] += 1

        if "negative_feedback.json" in zf.namelist():
            neg_items = json.loads(zf.read("negative_feedback.json").decode("utf-8"))
            for n in neg_items:
                self.con.execute(
                    """
                    INSERT INTO cleaner_classification_negative_feedback
                        (product_id, rejected_klasifikasi_id, reason, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(product_id, rejected_klasifikasi_id) DO NOTHING
                    """,
                    (int(n["product_id"]), int(n["rejected_klasifikasi_id"]), n.get("reason", "restore"), n.get("created_at", "NOW")),
                )
                counts["negative_feedback"] += 1

        if "approvals.json" in zf.namelist():
            approvals = json.loads(zf.read("approvals.json").decode("utf-8"))
            counts["approvals"] = len(approvals)

        return counts
