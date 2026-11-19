"""Main dashboard entrypoint with stateless cloud mode, ZIP backup/recovery, and RBAC."""

from datetime import datetime, timezone
import json
import streamlit as st
from olsera_cleaner.auth import enforce_authentication, render_user_sidebar
from olsera_cleaner.config import Settings
from olsera_cleaner.database.repository import Database
from olsera_cleaner.api.client import OlseraClient
from olsera_cleaner.recovery import CloudBackupEngine, SecurityZipError
from olsera_cleaner.sync.manager import SyncManager
from olsera_cleaner.recovery.backup import BackupManager
from olsera_cleaner.reporting import CsvExporter
from olsera_cleaner.ui.audit_page import render as render_audit
from olsera_cleaner.ui.category_audit_page import render_category_audit
from olsera_cleaner.ui.classification_audit_page import render_classification_audit
from olsera_cleaner.ui.advanced_pages import ai_page, sales_page, update_page


def _render_sync_tab(user, cfg, manager, backup, exporter) -> None:
    """Render synchronization tab with user-triggered execution and status."""
    st.subheader("Sinkronisasi Olsera (Read-Only)")
    st.write("Produk disinkronkan ke staging dan baru dipublikasikan ketika seluruh halaman valid.")
    options = {
        "Produk (semua)": "products",
        "Collection": "collections",
        "Product Group": "product_groups",
    }
    selected = st.selectbox("Sumber data", list(options))

    if not user.can_sync:
        st.warning(f"Akses Terbatas: Peran Anda ({user.role}) berstatus read-only dan tidak diizinkan memicu sinkronisasi.")
        return

    if not st.button("Jalankan sinkronisasi (read-only)", type="primary"):
        return

    if not cfg.app_id or not cfg.secret_key:
        st.error("Isi OLSERA_APP_ID dan OLSERA_SECRET_KEY di konfigurasi / secrets terlebih dahulu.")
        return

    bar = st.progress(0)
    label = st.empty()

    def on_progress(page, last, count):
        bar.progress(min(page / max(last, 1), 1.0))
        label.write(f"Halaman {page}/{last} • {count} record pada halaman ini")

    try:
        result = manager.sync(options[selected], progress=on_progress)
        st.success(f"Berhasil: {result['total']} data, {result['pages']} halaman. Run #{result['run_id']}")
        if options[selected] == "products" and not cfg.is_postgres():
            path = backup.create("initial")
            st.info(f"Backup snapshot tersimpan: {path}")
            baseline, created = exporter.export_baseline()
            st.info(f"CSV BEFORE baseline: {baseline} ({'baru dibuat' if created else 'sudah ada, tidak ditimpa'})")
        st.rerun()
    except Exception as exc:
        st.error(f"Sinkronisasi gagal: {type(exc).__name__}: {exc}")
        st.warning("Data sinkronisasi sebelumnya tetap dipertahankan.")


def _render_backup_tab(db, user, cfg, backup, exporter) -> None:
    """Render backup, granular export, and secure ZIP recovery tab with access control."""
    st.subheader("Penyimpanan & Cadangan Data")

    if cfg.is_postgres():
        st.info("☁️ **PostgreSQL / Supabase Persistence Aktif:** Data tersimpan secara persisten di database cloud.")
    elif cfg.is_cloud:
        st.warning(
            "☁️ **Mode Stateless Cloud (SQLite Sementara):** Data di dalam container bersifat sementara dan dapat ter-reset "
            "saat container berhenti. Selalu unduh **Backup ZIP Lengkap** setelah selesai melakukan audit atau approval."
        )
    else:
        st.caption("Backup lokal menggunakan SQLite Backup API + integrity check di Windows.")
        st.code(str(cfg.backup_dir))

    if not user.can_export:
        st.warning("Akses Ditolak: Peran Anda tidak memiliki hak unduh cadangan atau ekspor CSV.")
        return

    # Section A: Full ZIP Backup
    st.divider()
    st.subheader("1. Unduh Cadangan Lengkap (Full Backup ZIP)")
    st.caption("Berisi manifest, checksum SHA-256, seluruh snapshot produk, temuan audit, riwayat persetujuan, dan ground truth.")

    with db.connect() as con:
        engine = CloudBackupEngine(con, expected_store_id=cfg.expected_store_id)
        zip_bytes, manifest = engine.create_full_backup_zip()

    store_tag = manifest.get("store_id") or "store"
    time_tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    zip_filename = f"olsera_backup_{store_tag}_{time_tag}.zip"

    st.download_button(
        label="📦 Unduh Full Backup ZIP Sekarang",
        data=zip_bytes,
        file_name=zip_filename,
        mime="application/zip",
        type="primary",
        use_container_width=True,
    )

    # Section B: Granular JSON Exports
    st.divider()
    st.subheader("2. Unduh Hasil Spesifik (Granular Exports)")
    c1, c2, c3 = st.columns(3)

    with db.connect() as con:
        engine = CloudBackupEngine(con, expected_store_id=cfg.expected_store_id)
        class_findings = engine.get_classification_findings_data()
        cat_findings = engine.get_category_findings_data()
        approvals = engine.get_approvals_data()

    with c1:
        st.download_button(
            "Temuan Klasifikasi (JSON)",
            data=json.dumps(class_findings, ensure_ascii=False, indent=2),
            file_name=f"classification_findings_{time_tag}.json",
            mime="application/json",
            use_container_width=True,
        )
    with c2:
        st.download_button(
            "Temuan Kategori (JSON)",
            data=json.dumps(cat_findings, ensure_ascii=False, indent=2),
            file_name=f"category_findings_{time_tag}.json",
            mime="application/json",
            use_container_width=True,
        )
    with c3:
        st.download_button(
            "Persetujuan Operator (JSON)",
            data=json.dumps(approvals, ensure_ascii=False, indent=2),
            file_name=f"operator_approvals_{time_tag}.json",
            mime="application/json",
            use_container_width=True,
        )

    # Section C: Secure Restore from ZIP
    st.divider()
    st.subheader("3. Pemulihan Data dari Cadangan ZIP")
    st.caption("Pulihkan produk, hasil audit, dan persetujuan dari file ZIP yang diunduh sebelumnya. Dilengkapi proteksi integritas checksum dan anti-path traversal.")

    if not user.can_audit:
        st.warning("Peran Anda hanya berhak mengunduh cadangan, tidak dapat memulihkan database.")
    else:
        uploaded_zip = st.file_uploader("Pilih file cadangan (.zip)", type=["zip"], key="backup_zip_uploader")
        if uploaded_zip is not None:
            if st.button("Mulai Pemulihan Data", type="secondary", use_container_width=True):
                try:
                    bytes_data = uploaded_zip.getvalue()
                    with db.connect() as con:
                        restore_engine = CloudBackupEngine(con, expected_store_id=cfg.expected_store_id)
                        summary = restore_engine.restore_from_backup_zip(bytes_data)

                    st.success(
                        f"Pemulihan Berhasil! Store ID: {summary.get('store_id')} • "
                        f"Produk: {summary['restored_counts'].get('products', 0):,} • "
                        f"Persetujuan: {summary['restored_counts'].get('approvals', 0):,} • "
                        f"Ground Truth: {summary['restored_counts'].get('ground_truth', 0):,}"
                    )
                    st.rerun()
                except SecurityZipError as sec_err:
                    st.error(f"Pemberhentian Keamanan: {sec_err}")
                except Exception as exc:
                    st.error(f"Gagal memulihkan cadangan: {type(exc).__name__}: {exc}")

    # Section D: CSV Baseline
    st.divider()
    st.subheader("4. Ekspor CSV BEFORE / CURRENT")
    col_csv1, col_csv2 = st.columns(2)
    with col_csv1:
        if st.button("Buat CSV baseline BEFORE", use_container_width=True):
            try:
                path, created = exporter.export_baseline()
                st.success(f"{path} ({'dibuat' if created else 'sudah ada, tidak ditimpa'})")
            except Exception as exc:
                st.error(str(exc))
    with col_csv2:
        if st.button("Export CURRENT dan COMPARISON", use_container_width=True):
            try:
                result = exporter.export_current_and_diff()
                st.success(f"CURRENT: {result['current']}")
                st.success(f"COMPARISON: {result['comparison']} ({result['changed_count']} perbedaan)")
            except Exception as exc:
                st.error(str(exc))


def main():
    st.set_page_config(
        page_title="Olsera Data Cleaner — Enterprise Stateless Cloud",
        page_icon="🧹",
        layout="wide",
    )

    try:
        cfg = Settings.from_env()
    except Exception as exc:
        st.error(f"Gagal memuat konfigurasi: {type(exc).__name__}: {exc}")
        st.stop()

    user = enforce_authentication(cfg)
    render_user_sidebar(user)

    st.title("Olsera Data Cleaner")
    st.caption("AI Taxonomy Manager & Controlled Reclassification — Stateless Cloud Edition")

    if cfg.is_stateless_cloud:
        st.info("☁️ **Stateless Cloud Mode Aktif (Tanpa Supabase):** Menggunakan penyimpanan sementara. Unduh Backup ZIP sebelum menutup sesi.")
    elif cfg.is_cloud:
        st.info("☁️ **Deployment Cloud Aktif:** Terhubung ke private container (READ-ONLY).")
    else:
        st.info("💻 **Mode Lokal Windows:** SQLite persisten aktif. Fitur update LIVE default NONAKTIF.")

    try:
        db = Database(cfg.db_path, database_url=cfg.database_url)
    except Exception as exc:
        st.error(f"Koneksi database gagal: {type(exc).__name__}: {exc}")
        st.stop()

    if "client" not in st.session_state:
        st.session_state.client = OlseraClient(cfg)
    client = st.session_state.client
    manager = SyncManager(client, db, cfg)
    backup = BackupManager(db, cfg.backup_dir)
    exporter = CsvExporter(db, cfg.data_dir / "reports" / "csv")

    metrics = db.metrics()
    prod_count = metrics["products"]

    # Status indicator banner
    if prod_count == 0:
        st.warning("⚠️ **Status: BELUM DISINKRONISASI (0 Produk tersimpan).** Silakan tarik data baru dari Olsera atau pulihkan dari file backup ZIP.")
    else:
        st.success(f"✅ **Status: DATA SIAP DIANALISIS ({prod_count:,} Produk tersimpan).**")

    a, b, c = st.columns(3)
    a.metric("Produk tersimpan", f"{prod_count:,}")
    b.metric("Barcode kosong", f"{metrics['missing_barcode']:,}")
    c.metric("Brand ID kosong", f"{metrics['missing_brand']:,}")

    tab_sync, tab_products, tab_audit, tab_category, tab_classification, tab_ai, tab_sales, tab_updates, tab_history, tab_backup, tab_settings = st.tabs(
        [
            "Sinkronisasi",
            "Produk",
            "Audit Produk",
            "Audit Kategori",
            "Audit Klasifikasi",
            "AI Assistant",
            "Transaksi",
            "Update Terkontrol",
            "Riwayat",
            "Cadangan & Pemulihan",
            "Konfigurasi",
        ]
    )

    with tab_sync:
        _render_sync_tab(user, cfg, manager, backup, exporter)

    with tab_products:
        st.subheader("Contoh produk tersimpan")
        st.caption("Maksimum 100 produk pertama berdasarkan nama. Tidak melakukan panggilan API.")
        rows = db.sample_products(100)
        st.dataframe(
            [dict(zip(["ID", "Nama", "SKU", "Barcode", "Klasifikasi", "Harga POS", "Modified"], row)) for row in rows],
            use_container_width=True,
            hide_index=True,
        )

    with tab_audit:
        render_audit(db)

    with tab_category:
        render_category_audit(db)

    with tab_classification:
        render_classification_audit(db)

    with tab_ai:
        ai_page(db)

    with tab_sales:
        sales_page(db, client)

    with tab_updates:
        if cfg.is_cloud and not cfg.allow_cloud_live_updates:
            st.warning("🔒 Operasi update LIVE dinonaktifkan pada lingkungan Cloud demi perlindungan integritas data toko.")
        elif not user.can_mutate:
            st.warning(f"Akses Ditolak: Fitur update memerlukan peran Administrator. Peran Anda saat ini: {user.role}.")
        else:
            update_page(db, client, cfg)

    with tab_history:
        st.subheader("Riwayat sinkronisasi")
        st.dataframe(
            [dict(zip(["Run", "Sumber", "Status", "Mulai (UTC)", "Selesai (UTC)", "Jumlah", "Error"], row)) for row in metrics["runs"]],
            use_container_width=True,
            hide_index=True,
        )

    with tab_backup:
        _render_backup_tab(db, user, cfg, backup, exporter)

    with tab_settings:
        st.subheader("Konfigurasi Sistem (Nilai Rahasia Dimask)")
        st.json(cfg.safe_summary())
        st.caption(f"Sesi Aktif: {user.email} (Peran: {user.role})")
