
"""Controlled single-product update with verification."""

import json

from .schema import setup
from olsera_cleaner.audit.store import (
    setup as audit_setup,
)
from olsera_cleaner.api.client import (
    OlseraUpdateRejectedError,
    OlseraUpdateUncertainError,
)
from olsera_cleaner.recovery.backup import BackupManager


class UnsafeUpdate(RuntimeError):
    pass


def prepare(db, limit=20):
    setup(db)
    audit_setup(db)

    with db.connect() as con:
        approved = con.execute(
            """
            SELECT
                f.id,
                f.product_id,
                f.old_name,
                f.suggested_name,
                p.modified_time,
                p.name
            FROM cleaner_audit_findings f
            JOIN cleaner_name_reviews r
                ON r.finding_id = f.id
                AND r.decision = 'APPROVED'
            JOIN cleaner_audit_runs a
                ON a.id = f.audit_run_id
            JOIN products_current p
                ON p.olsera_product_id = f.product_id
            WHERE f.kind = 'NAME_FORMAT'
              AND f.old_name = p.name
              AND f.suggested_name != p.name
              AND a.id = (
                  SELECT MAX(id)
                  FROM cleaner_audit_runs
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM cleaner_audit_findings x
                  WHERE x.audit_run_id = f.audit_run_id
                    AND x.product_id = f.product_id
                    AND x.kind = 'ABBREVIATION_AMBIGUOUS'
              )
            ORDER BY f.product_id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        if not approved:
            return None, 0

        batch_id = con.execute(
            """
            INSERT INTO cleaner_change_batches
            DEFAULT VALUES
            """
        ).lastrowid

        con.executemany(
            """
            INSERT INTO cleaner_change_operations (
                batch_id,
                finding_id,
                product_id,
                old_name,
                new_name,
                old_modified_time
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    batch_id,
                    finding_id,
                    product_id,
                    old_name,
                    new_name,
                    modified_time,
                )
                for (
                    finding_id,
                    product_id,
                    old_name,
                    new_name,
                    modified_time,
                    _,
                ) in approved
            ],
        )

        return batch_id, len(approved)


def operations(db, batch_id):
    setup(db)

    with db.connect() as con:
        rows = con.execute(
            """
            SELECT
                id,
                product_id,
                old_name,
                new_name,
                status,
                error_summary
            FROM cleaner_change_operations
            WHERE batch_id = ?
            ORDER BY id
            """,
            (int(batch_id),),
        ).fetchall()

    columns = (
        "id",
        "product_id",
        "old_name",
        "new_name",
        "status",
        "error",
    )

    return [
        dict(zip(columns, row))
        for row in rows
    ]


def _detail(payload):
    data = payload.get("data")

    if isinstance(data, dict):
        if isinstance(data.get("product"), dict):
            return data["product"]

        return data

    raise UnsafeUpdate(
        "Struktur detail produk tidak dikenal: diblokir"
    )


def _set_status(
    db,
    operation_id,
    status,
    error=None,
    before=None,
    after=None,
):
    with db.connect() as con:
        con.execute(
            """
            UPDATE cleaner_change_operations
            SET
                status = ?,
                error_summary = ?,
                before_json = COALESCE(?, before_json),
                after_json = COALESCE(?, after_json),
                finished_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                status,
                error,
                (
                    json.dumps(
                        before,
                        ensure_ascii=False,
                    )
                    if before is not None
                    else None
                ),
                (
                    json.dumps(
                        after,
                        ensure_ascii=False,
                    )
                    if after is not None
                    else None
                ),
                operation_id,
            ),
        )

        con.execute(
            """
            INSERT INTO cleaner_system_events (
                kind,
                detail_json
            )
            VALUES (?, ?)
            """,
            (
                status,
                json.dumps({
                    "operation_id": operation_id,
                    "error": error,
                }),
            ),
        )


def _error_summary(context, exc):
    """Keep an actionable, bounded error without storing request credentials."""
    message = " ".join(str(exc).split())
    detail = f"{context} ({type(exc).__name__})"
    if message:
        detail += f": {message}"
    return detail[:700]


def _protected_differences(before, after):
    ignore = {
        "name",
        "modified_time",
        "stock_sync_time",
        "updated_at",
        "updated_time",
        "last_modified",
    }

    common_keys = before.keys() & after.keys()

    return {
        key: {
            "before": before.get(key),
            "after": after.get(key),
        }
        for key in common_keys
        if key not in ignore
        and before[key] != after[key]
    }


def execute_one(
    db,
    client,
    op_id,
    backup_dir,
    allow_live=False,
):
    if client and getattr(getattr(client, "settings", None), "is_cloud", False):
        raise UnsafeUpdate(
            "MUTASI DITOLAK: Operasi update LIVE diblokir permanen pada lingkungan cloud."
        )

    if not allow_live:
        raise UnsafeUpdate(
            "Live update dinonaktifkan"
        )

    setup(db)

    with db.connect() as con:
        operation = con.execute(
            """
            SELECT
                o.batch_id,
                o.product_id,
                o.old_name,
                o.new_name,
                o.status,
                b.status,
                f.kind,
                r.decision
            FROM cleaner_change_operations o
            JOIN cleaner_change_batches b
                ON b.id = o.batch_id
            JOIN cleaner_audit_findings f
                ON f.id = o.finding_id
            JOIN cleaner_name_reviews r
                ON r.finding_id = f.id
            WHERE o.id = ?
            """,
            (int(op_id),),
        ).fetchone()

    if (
        not operation
        or operation[4] != "READY"
        or operation[5] not in ("DRAFT", "RUNNING")
        or operation[6] != "NAME_FORMAT"
        or operation[7] != "APPROVED"
    ):
        raise UnsafeUpdate(
            "Operasi bukan perubahan yang disetujui"
        )

    batch_id = operation[0]
    product_id = operation[1]
    old_name = operation[2]
    new_name = operation[3]

    if (
        not old_name.strip()
        or not new_name.strip()
        or old_name == new_name
    ):
        raise UnsafeUpdate(
            "Nama produk tidak valid"
        )

    with db.connect() as con:
        previous_count = con.execute(
            """
            SELECT COUNT(*)
            FROM cleaner_change_operations
            WHERE batch_id = ?
              AND status IN (
                  'COMPLETED',
                  'UNKNOWN',
                  'FAILED',
                  'STALE'
              )
            """,
            (batch_id,),
        ).fetchone()[0]

    if previous_count == 0:
        BackupManager(
            db,
            backup_dir,
        ).create("pre_migration")

    remote = _detail(
        client.product_detail(product_id)
    )

    if remote.get("name") != old_name:
        _set_status(
            db,
            op_id,
            "STALE",
            "Nama telah berubah di Olsera; "
            "jangan ditimpa",
            remote,
        )
        return "STALE"

    with db.connect() as con:
        duplicate = con.execute(
            """
            SELECT COUNT(*)
            FROM cleaner_change_operations
            WHERE product_id = ?
              AND id <> ?
              AND status IN ('RUNNING', 'UNKNOWN')
            """,
            (product_id, op_id),
        ).fetchone()[0]

        if duplicate:
            raise UnsafeUpdate(
                "Produk memiliki operasi "
                "RUNNING/UNKNOWN lainnya"
            )

        changed = con.execute(
            """
            UPDATE cleaner_change_operations
            SET status = 'RUNNING'
            WHERE id = ?
              AND status = 'READY'
            """,
            (op_id,),
        ).rowcount

        if changed != 1:
            raise UnsafeUpdate(
                "Operasi sedang diproses"
            )

    # Jangan retry otomatis setelah POST. Hanya respons yang jelas ditolak
    # ditandai FAILED; gangguan transport/server harus direkonsiliasi dahulu.
    try:
        client.update_product_name(
            product_id,
            new_name,
            sku=remote.get("sku"),
            store_id=remote.get("store_id"),
        )
    except OlseraUpdateRejectedError as exc:
        _set_status(
            db,
            op_id,
            "FAILED",
            _error_summary("POST update ditolak", exc),
            remote,
        )
        return "FAILED"
    except OlseraUpdateUncertainError as exc:
        _set_status(
            db,
            op_id,
            "UNKNOWN",
            _error_summary(
                "POST update mungkin telah diterapkan; "
                "jangan ulangi sebelum rekonsiliasi",
                exc,
            ),
            remote,
        )
        return "UNKNOWN"
    except Exception as exc:
        # Errors raised before/during request construction are fail-closed.
        # OlseraClient converts uncertain POST transport errors above.
        _set_status(
            db,
            op_id,
            "FAILED",
            _error_summary("POST update tidak dikirim", exc),
            remote,
        )
        return "FAILED"

    try:
        verified = _detail(
            client.product_detail(product_id)
        )
    except Exception as exc:
        _set_status(
            db,
            op_id,
            "UNKNOWN",
            _error_summary(
                "POST telah dikirim, tetapi verifikasi GET gagal",
                exc,
            ),
            remote,
        )
        return "UNKNOWN"

    if verified.get("name") == new_name:
        differences = _protected_differences(
            remote,
            verified,
        )

        if differences:
            _set_status(
                db,
                op_id,
                "UNKNOWN",
                "Nama berubah, tetapi atribut "
                "selain nama juga berubah. "
                "Periksa secara manual.",
                remote,
                verified,
            )
            return "UNKNOWN"

        _set_status(
            db,
            op_id,
            "COMPLETED",
            None,
            remote,
            verified,
        )
        return "COMPLETED"

    _set_status(
        db,
        op_id,
        "FAILED" if verified.get("name") == old_name else "UNKNOWN",
        (
            "Verifikasi setelah POST menunjukkan nama lama masih tersimpan"
            if verified.get("name") == old_name
            else "Hasil pembacaan ulang tidak sesuai perubahan; periksa manual"
        ),
        remote,
        verified,
    )

    return "FAILED" if verified.get("name") == old_name else "UNKNOWN"


def reconcile(db, client, op_id):
    setup(db)

    with db.connect() as con:
        operation = con.execute(
            """
            SELECT
                product_id,
                old_name,
                new_name,
                status
            FROM cleaner_change_operations
            WHERE id = ?
            """,
            (op_id,),
        ).fetchone()

    if not operation or operation[3] != "UNKNOWN":
        raise UnsafeUpdate(
            "Hanya operasi UNKNOWN "
            "yang dapat direkonsiliasi"
        )

    remote = _detail(
        client.product_detail(operation[0])
    )

    if remote.get("name") == operation[2]:
        with db.connect() as con:
            stored = con.execute(
                """
                SELECT before_json
                FROM cleaner_change_operations
                WHERE id = ?
                """,
                (op_id,),
            ).fetchone()

        before = (
            json.loads(stored[0])
            if stored and stored[0]
            else {}
        )

        if (
            before
            and _protected_differences(
                before,
                remote,
            )
        ):
            status = "STALE"
        else:
            status = "COMPLETED"

    elif remote.get("name") == operation[1]:
        status = "FAILED"

    else:
        status = "STALE"

    if status == "FAILED":
        error = "Rekonsiliasi: nama lama masih tersimpan di Olsera"
    elif status == "STALE":
        error = "Rekonsiliasi: nama di Olsera berbeda dari sebelum/sesudah yang diharapkan"
    else:
        error = None

    _set_status(
        db,
        op_id,
        status,
        error,
        None,
        remote,
    )

    return status
