
import datetime as dt
import json

import streamlit as st

from olsera_cleaner.advanced.schema import setup
from olsera_cleaner.advanced.ai import (
    analyze_one,
    recent,
)
from olsera_cleaner.advanced.sales import (
    sync_sales,
    sample,
)
from olsera_cleaner.advanced.updates import (
    prepare,
    operations,
    execute_one,
    reconcile,
)


def _has_audit(con):
    return con.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'cleaner_audit_runs'
        """
    ).fetchone() is not None


def ai_page(db):
    setup(db)

    st.subheader(
        "Ollama AI — rekomendasi nama (lokal)"
    )

    st.info(
        "AI hanya memberikan usulan. "
        "AI tidak memiliki akses langsung "
        "untuk mengubah produk Olsera."
    )

    model = st.text_input(
        "Model Ollama",
        value="llama3:8b",
    )

    online_lookup = st.checkbox(
        "Cari barcode di Open Food Facts",
        value=False,
    )

    st.caption(
        "Barcode akan dikirim ke layanan publik "
        "jika pencarian online diaktifkan."
    )

    with db.connect() as con:
        if _has_audit(con):
            candidates = con.execute(
                """
                SELECT DISTINCT
                    f.product_id,
                    p.name,
                    p.sku,
                    p.barcode
                FROM cleaner_audit_findings f
                JOIN products_current p
                    ON p.olsera_product_id = f.product_id
                WHERE f.audit_run_id = (
                    SELECT MAX(id)
                    FROM cleaner_audit_runs
                )
                AND f.kind IN (
                    'ABBREVIATION_AMBIGUOUS',
                    'EMPTY_NAME'
                )
                ORDER BY f.product_id
                LIMIT 200
                """
            ).fetchall()
        else:
            candidates = []

    ids = [row[0] for row in candidates]

    labels = {
        row[0]: (
            f"{row[1]} | SKU: {row[2] or '-'} "
            f"| ID: {row[0]}"
        )
        for row in candidates
    }

    if not ids:
        st.caption(
            "Belum ada kandidat ambigu. "
            "Jalankan Audit Produk terlebih dahulu."
        )

    chosen = (
        st.selectbox(
            "Produk yang ingin dianalisis",
            ids,
            format_func=lambda product_id: labels.get(
                product_id,
                str(product_id),
            ),
        )
        if ids
        else None
    )

    if chosen and st.button(
        "Analisis produk terpilih"
    ):
        try:
            result = analyze_one(
                db,
                chosen,
                model=model,
                online_lookup=online_lookup,
                force=True,
            )
            st.write(result)
        except Exception as exc:
            st.error(str(exc))

    batch = st.selectbox(
        "Jumlah kandidat per batch",
        [5, 10, 25],
        index=0,
    )

    if st.button(
        "Jalankan batch AI",
        disabled=not ids,
    ):
        progress = st.progress(0)
        results = []

        selected_ids = ids[:batch]

        for index, product_id in enumerate(
            selected_ids
        ):
            try:
                result = analyze_one(
                    db,
                    product_id,
                    model=model,
                    online_lookup=online_lookup,
                )

                results.append({
                    "id": product_id,
                    **result,
                })

            except Exception as exc:
                results.append({
                    "id": product_id,
                    "error": str(exc),
                })

            progress.progress(
                (index + 1) / len(selected_ids)
            )

        st.dataframe(
            results,
            use_container_width=True,
        )

    st.dataframe(
        recent(db, 100),
        use_container_width=True,
        hide_index=True,
    )

def sales_page(db, client):
    from olsera_cleaner.advanced.sales import (
        sync_product_sales,
        latest_product_sales
    )

    st.subheader("Audit Penjualan Produk Olsera")
    st.caption(
        "Sumber: report/productsalesbysku "
        "(laporan penjualan berdasarkan SKU)"
    )

    st.info(
        "Data diambil dari laporan penjualan Olsera. "
        "Proses hanya membaca API dan menyimpan "
        "hasilnya ke SQLite lokal."
    )

    col1, col2 = st.columns(2)

    with col1:
        start = st.date_input(
            "Awal periode",
            value=dt.date.today()
            - dt.timedelta(days=30),
            key="product_sales_start"
        )

    with col2:
        end = st.date_input(
            "Akhir periode",
            value=dt.date.today(),
            key="product_sales_end"
        )

    page_size = st.selectbox(
        "Jumlah data per halaman",
        [15, 30, 50, 100],
        index=2
    )

    max_pages = st.number_input(
        "Batas halaman",
        min_value=1,
        max_value=1000,
        value=100
    )

    if st.button(
        "Sinkronisasi Penjualan",
        type="primary"
    ):
        try:
            with st.spinner(
                "Mengambil laporan penjualan Olsera..."
            ):
                result = sync_product_sales(
                    db,
                    client,
                    start,
                    end,
                    page_size=int(page_size),
                    max_pages=int(max_pages)
                )

            st.success(
                "Sinkronisasi selesai: "
                f"{result['total']} baris laporan "
                f"dari {result['pages']} halaman."
            )

        except Exception as exc:
            st.error(
                "Sinkronisasi gagal: "
                f"{type(exc).__name__}: {exc}"
            )

    summary, items = latest_product_sales(db)

    if summary:
        st.divider()
        st.subheader("Hasil Sinkronisasi Terakhir")

        c1, c2, c3 = st.columns(3)

        c1.metric(
            "Baris Laporan",
            summary["total"]
        )

        c2.metric(
            "Halaman",
            summary["pages"]
        )

        c3.metric(
            "Run ID",
            summary["run_id"]
        )

        st.write(
            "Periode:",
            summary["start_date"],
            "sampai",
            summary["end_date"]
        )

        st.subheader("Contoh Data Penjualan")

        if items:
            st.dataframe(
                items,
                use_container_width=True,
                hide_index=True
            )

            with st.expander(
                "Lihat struktur JSON asli"
            ):
                st.json(items[0])

        else:
            st.info(
                "Tidak ada baris laporan "
                "pada periode tersebut."
            )

    st.warning(
        "Penghitungan produk tidak terjual "
        "belum diaktifkan sampai field SKU, "
        "jumlah penjualan, dan cakupan laporan "
        "dikonfirmasi dari response JSON asli."
    )


def update_page(db, client, cfg):
    setup(db)

    st.subheader(
        "Controlled Batch Update — "
        "Pengujian Satu Produk"
    )

    st.warning(
        "LIVE UPDATE bersifat eksperimental. "
        "Gunakan hanya produk khusus pengujian "
        "yang tidak sedang digunakan untuk "
        "transaksi operasional."
    )

    st.info(
        "Update satu produk memerlukan "
        "persetujuan terlebih dahulu. "
        "Aplikasi tidak pernah mengubah file .env."
    )

    if not cfg.updates_enabled:
        st.warning(
            "LIVE UPDATE tetap terkunci. Untuk membuka pengujian satu produk, "
            "operator harus mengatur OLSERA_ENABLE_UPDATES=YES lalu restart aplikasi."
        )

    with db.connect() as con:
        if _has_audit(con):
            approved_count = con.execute(
                """
                SELECT COUNT(*)
                FROM cleaner_audit_findings f
                JOIN cleaner_name_reviews r
                    ON r.finding_id = f.id
                WHERE r.decision = 'APPROVED'
                  AND f.audit_run_id = (
                      SELECT MAX(id)
                      FROM cleaner_audit_runs
                  )
                  AND f.kind = 'NAME_FORMAT'
                """
            ).fetchone()[0]
        else:
            approved_count = 0

    st.write(
        "Persetujuan format nama "
        "pada audit terbaru:",
        approved_count,
    )

    if approved_count == 0:
        st.caption(
            "Buka tab Audit Produk, jalankan audit, "
            "kemudian setujui minimal satu perubahan "
            "NAME_FORMAT untuk produk khusus uji. "
            "Rekomendasi AI belum otomatis "
            "masuk antrean persetujuan."
        )

    if st.button(
        "Buat draft batch dari nama yang disetujui"
    ):
        try:
            batch_id, count = prepare(db)

            if batch_id:
                st.success(
                    f"Draft batch #{batch_id} "
                    f"berisi {count} produk. "
                    "Belum dikirim ke Olsera."
                )
            else:
                st.info(
                    "Belum ada perubahan NAME_FORMAT "
                    "yang memenuhi syarat."
                )

        except Exception as exc:
            st.error(str(exc))

    with db.connect() as con:
        batches = con.execute(
            """
            SELECT id, status, created_at
            FROM cleaner_change_batches
            ORDER BY id DESC
            LIMIT 20
            """
        ).fetchall()

    if not batches:
        return

    batch_id = st.selectbox(
        "Batch",
        [row[0] for row in batches],
        format_func=lambda value: (
            f"Batch #{value}"
        ),
    )

    ops = operations(db, batch_id)

    st.dataframe(
        ops,
        use_container_width=True,
        hide_index=True,
    )

    if not ops:
        return

    operation_id = st.selectbox(
        "Pilih operasi UJI SATU PRODUK",
        [row["id"] for row in ops],
        format_func=lambda value: (
            f"Operasi #{value}"
        ),
    )

    selected = next(
        row
        for row in ops
        if row["id"] == operation_id
    )

    st.write(
        "Product ID:",
        selected["product_id"],
    )

    st.write(
        "BEFORE:",
        selected["old_name"],
    )

    st.write(
        "AFTER:",
        selected["new_name"],
    )

    st.write(
        "Status:",
        selected["status"],
    )

    if st.button(
        "Rekonsiliasi operasi UNKNOWN",
        disabled=selected["status"] != "UNKNOWN",
    ):
        try:
            status = reconcile(
                db,
                client,
                operation_id,
            )

            st.info(
                "Hasil rekonsiliasi: " + status
            )
            st.rerun()

        except Exception as exc:
            st.error(str(exc))

    st.warning(
        "PERHATIAN: Tombol berikut benar-benar "
        "mengirim perubahan nama ke API Olsera. "
        "Jangan gunakan produk operasional. "
        "POST tidak diulang secara otomatis."
    )

    verified_test = st.checkbox(
        "Saya memastikan produk ini KHUSUS UJI "
        "dan aman diubah",
        value=False,
        key="live_test_checkbox",
    )

    typed_id = st.text_input(
        "Ketik ID produk sebagai konfirmasi",
        value="",
        key="live_test_product_id",
    )

    confirmation = st.text_input(
        "Ketik: UPDATE PRODUK UJI",
        value="",
        key="live_test_confirmation",
    )

    ready = (
        cfg.updates_enabled
        and selected["status"] == "READY"
        and verified_test
        and typed_id.strip()
            == str(selected["product_id"])
        and confirmation.strip()
            == "UPDATE PRODUK UJI"
    )

    if st.button(
        "KIRIM LIVE: 1 PRODUK UJI",
        disabled=not ready,
        type="primary",
    ):
        try:
            outcome = execute_one(
                db,
                client,
                operation_id,
                cfg.backup_dir,
                allow_live=cfg.updates_enabled,
            )

            if outcome == "COMPLETED":
                st.success(
                    "Perubahan nama berhasil diverifikasi."
                )

            elif outcome == "UNKNOWN":
                st.error(
                    "Status UNKNOWN. Jangan ulangi POST. "
                    "Periksa produk dan gunakan rekonsiliasi."
                )

            else:
                st.warning(
                    "Status operasi: " + outcome
                )

            st.rerun()

        except Exception as exc:
            st.error(str(exc))

    st.info(
        "Update massal tetap dikunci. "
        "Tidak tersedia fungsi DELETE produk."
    )
