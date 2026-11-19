"""Smart Category Audit Streamlit UI: dashboard, filterable paginated findings, detail view, approval workflow, and CSV export."""

import math
from typing import Any, Dict, List
import streamlit as st

from olsera_cleaner.category.evaluator import (
    evaluate_model_on_ground_truth,
    get_ground_truth_dataset,
    save_ground_truth,
    seed_representative_ground_truth,
)
from olsera_cleaner.category.discovery import discover_categories
from olsera_cleaner.category.engine import run_category_audit
from olsera_cleaner.category.store import (
    bulk_review_category_findings,
    export_category_audit_csv,
    get_recent_category_audit,
    review_category_finding,
    save_category_audit,
    setup,
)
from olsera_cleaner.category.verifier import CategoryAIVerifier


def render_category_audit(db):
    """Render the Smart Category Audit tab in Streamlit."""
    setup(db)
    st.subheader("Smart Category Audit & Klasifikasi AI")
    st.info(
        "Pemeriksaan kualitas kategori membaca snapshot lokal SQLite. "
        "Seluruh keputusan persetujuan tersimpan secara lokal dan TIDAK mengubah produk live di Olsera."
    )

    # Audit Execution Section
    with st.expander("Konfigurasi & Eksekusi Audit Kategori", expanded=False):
        c1, c2, c3 = st.columns([2, 2, 1])
        with c1:
            use_ollama = st.checkbox("Gunakan verifikasi Ollama AI untuk kasus ambigu", value=False)
        with c2:
            model_name = st.text_input("Model Ollama", value="llama3:8b", disabled=not use_ollama)
        with c3:
            max_ai_items = st.number_input("Batas verifikasi AI", min_value=5, max_value=100, value=25, step=5, disabled=not use_ollama)

        if st.button("Jalankan Audit Kategori Sekarang", type="primary", key="btn_run_category_audit"):
            progress_bar = st.progress(0)
            status_text = st.empty()

            def _on_progress(pct, total, message):
                progress_bar.progress(min(pct / max(total, 1), 1.0))
                status_text.caption(message)

            verifier = None
            if use_ollama:
                try:
                    verifier = CategoryAIVerifier(model=model_name.strip())
                except Exception as e:
                    st.warning(f"Gagal menginisialisasi Ollama ({e}). Audit dilanjutkan dengan metode statistik.")

            try:
                with db.connect() as con:
                    discovery, findings = run_category_audit(
                        con,
                        verifier=verifier,
                        progress_callback=_on_progress,
                        max_ai_verifications=int(max_ai_items),
                    )
                run_id = save_category_audit(db, discovery, findings)
                st.success(
                    f"Audit Kategori #{run_id} selesai! Ditemukan {len(discovery.categories)} kategori, "
                    f"{len(findings)} produk terindikasi salah/perlu kategori."
                )
                st.rerun()
            except Exception as exc:
                st.error(f"Audit kategori gagal: {type(exc).__name__}: {exc}")

    # Fetch recent audit run
    run_summary, findings = get_recent_category_audit(db, limit=5000)

    if not run_summary:
        st.caption("Belum ada riwayat audit kategori. Klik 'Jalankan Audit Kategori Sekarang' di atas.")
        # Show real-time discovered categories from SQLite even before running an audit
        with db.connect() as con:
            live_discovery = discover_categories(con)
        if live_discovery.total_products > 0:
            st.divider()
            st.write(f"Distribusi Kategori Saat Ini ({live_discovery.total_products:,} produk terdaftar):")
            cat_table = [
                {
                    "ID": cid,
                    "Nama Kategori": m.name,
                    "Jumlah Produk": f"{m.product_count:,}",
                    "Persentase": f"{m.percentage:.1f}%",
                    "Status": "Catch-All / Umum" if m.is_catch_all else ("Sedikit Sampel" if m.is_low_sample else "Spesifik"),
                }
                for cid, m in live_discovery.categories.items()
            ]
            st.dataframe(cat_table, use_container_width=True, hide_index=True)
        return

    # Section 1: Dashboard Metrics
    st.divider()
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Produk Dianalisis", f"{run_summary['items_seen']:,}")
    m2.metric("Kategori Valid Terdeteksi", f"{run_summary['categories_found']:,}")
    m3.metric("Temuan Salah/Perlu Kategori", f"{run_summary['findings_count']:,}")

    approved_count = sum(1 for f in findings if f["decision"] == "APPROVED")
    pending_count = sum(1 for f in findings if f["decision"] == "PENDING")
    m4.metric("Status Review", f"{approved_count} Disetujui / {pending_count} Pending")

    st.caption(
        f"Audit #{run_summary['id']} • Sinkronisasi Sumber #{run_summary['source_sync_run_id']} • "
        f"Waktu: {run_summary['started_at']} • Metode: {run_summary['method_version']}"
    )

    # Section 2: Filters and Search
    st.divider()
    st.subheader("Daftar Temuan Audit Kategori")

    f1, f2, f3, f4, f5 = st.columns([3, 2, 2, 2, 2])
    with f1:
        query_search = st.text_input("Cari Nama / SKU / Barcode", key="cat_search").strip().lower()
    with f2:
        source_cats = ["Semua"] + sorted({f["current_category_name"] for f in findings})
        selected_source = st.selectbox("Kategori Asal", source_cats, key="cat_filter_source")
    with f3:
        target_cats = ["Semua"] + sorted({f["suggested_category_name"] for f in findings})
        selected_target = st.selectbox("Kategori Rekomendasi", target_cats, key="cat_filter_target")
    with f4:
        selected_conf = st.selectbox("Tingkat Confidence", ["Semua", "HIGH", "MEDIUM", "LOW"], key="cat_filter_conf")
    with f5:
        selected_status = st.selectbox("Status Review", ["Semua", "PENDING", "APPROVED", "REJECTED", "DEFERRED"], key="cat_filter_status")

    # Apply filters
    filtered = findings
    if query_search:
        filtered = [
            f for f in filtered
            if query_search in (f["product_name"] or "").lower()
            or query_search in (f["sku"] or "").lower()
            or query_search in (f["barcode"] or "").lower()
        ]
    if selected_source != "Semua":
        filtered = [f for f in filtered if f["current_category_name"] == selected_source]
    if selected_target != "Semua":
        filtered = [f for f in filtered if f["suggested_category_name"] == selected_target]
    if selected_conf != "Semua":
        filtered = [f for f in filtered if f["confidence_level"] == selected_conf]
    if selected_status != "Semua":
        filtered = [f for f in filtered if f["decision"] == selected_status]

    st.write(f"Menampilkan **{len(filtered):,}** dari total {len(findings):,} temuan audit.")

    if not filtered:
        st.info("Tidak ada temuan yang cocok dengan kriteria filter saat ini.")
        return

    # Section 3: Pagination
    p_col1, p_col2 = st.columns([2, 4])
    with p_col1:
        page_size = st.selectbox("Baris per halaman", [25, 50, 100], index=0, key="cat_page_size")
    total_pages = max(1, math.ceil(len(filtered) / page_size))
    with p_col2:
        page_num = st.number_input("Halaman", min_value=1, max_value=total_pages, value=1, step=1, key="cat_page_num")

    start_idx = (page_num - 1) * page_size
    end_idx = min(start_idx + page_size, len(filtered))
    page_items = filtered[start_idx:end_idx]

    display_table = [
        {
            "ID Temuan": item["id"],
            "ID Produk": item["product_id"],
            "Nama Produk": item["product_name"],
            "SKU": item["sku"] or "-",
            "Kategori Asal": item["current_category_name"],
            "Rekomendasi": item["suggested_category_name"],
            "Skor": f"{item['confidence_score']:.2f}",
            "Confidence": item["confidence_level"],
            "Metode": item["classification_method"],
            "Keputusan": item["decision"],
        }
        for item in page_items
    ]
    st.dataframe(display_table, use_container_width=True, hide_index=True)

    # Section 4: Detail View & Single Approval
    st.divider()
    st.subheader("Detail Temuan & Keputusan Operator (Lokal)")

    item_options = {
        f"#{it['product_id']} — {it['product_name'][:55]} | {it['current_category_name']} → {it['suggested_category_name']} ({it['confidence_level']})": it
        for it in page_items
    }
    selected_label = st.selectbox("Pilih produk untuk ditinjau", list(item_options.keys()), key="cat_select_item")
    curr_item = item_options[selected_label]

    if curr_item.get("source_changed"):
        st.warning("PERINGATAN: Data produk di SQLite telah berubah sejak audit ini dibuat. Tinjau kembali sebelum menyetujui.")

    d_col1, d_col2 = st.columns(2)
    with d_col1:
        st.markdown(f"**Nama Produk:** {curr_item['product_name']}")
        st.markdown(f"**SKU:** `{curr_item['sku'] or '-'}` • **Barcode:** `{curr_item['barcode'] or '-'}`")
        st.markdown(f"**Kategori Saat Ini:** `{curr_item['current_category_name']}` (ID: {curr_item['current_category_id']})")
        st.markdown(f"**Kategori Rekomendasi:** `{curr_item['suggested_category_name']}` (ID: {curr_item['suggested_category_id']})")
        st.markdown(f"**Confidence:** `{curr_item['confidence_level']}` ({curr_item['confidence_score']:.2f}) • **Metode:** `{curr_item['classification_method']}`")

    with d_col2:
        st.markdown(f"**Alasan Rekomendasi:**\n> {curr_item['reason']}")
        if curr_item.get("comparison_product_name"):
            st.markdown(
                f"**Produk Pembanding:** '{curr_item['comparison_product_name']}' "
                f"(Kemiripan: {curr_item['comparison_similarity']:.2f})"
            )
        st.markdown(f"**Keputusan Saat Ini:** `{curr_item['decision']}`")

    note_input = st.text_input("Catatan Operator (opsional)", value=curr_item.get("note") or "", key="cat_review_note")

    b_col1, b_col2, b_col3 = st.columns(3)
    with b_col1:
        if st.button("Setujui Rekomendasi", type="primary", key=f"cat_approve_{curr_item['id']}"):
            review_category_finding(db, curr_item["id"], "APPROVED", note=note_input)
            st.success(f"Rekomendasi untuk produk #{curr_item['product_id']} disetujui di SQLite.")
            st.rerun()
    with b_col2:
        if st.button("Tolak Rekomendasi", key=f"cat_reject_{curr_item['id']}"):
            review_category_finding(db, curr_item["id"], "REJECTED", note=note_input)
            st.info(f"Rekomendasi untuk produk #{curr_item['product_id']} ditolak di SQLite.")
            st.rerun()
    with b_col3:
        if st.button("Tunda / Review Nanti", key=f"cat_defer_{curr_item['id']}"):
            review_category_finding(db, curr_item["id"], "DEFERRED", note=note_input)
            st.warning(f"Rekomendasi untuk produk #{curr_item['product_id']} ditunda.")
            st.rerun()

    # Section 5: Bulk Approval
    st.divider()
    with st.expander("Persetujuan Massal Terkontrol (Lokal)", expanded=False):
        st.caption(
            "Persetujuan massal hanya berlaku secara lokal di database SQLite. "
            "Untuk keamanan, persetujuan massal dibatasi maksimal 50 produk per aksi."
        )
        eligible_bulk = [f for f in filtered if f["decision"] == "PENDING" and f["confidence_level"] == "HIGH"]
        st.write(f"Terdapat **{len(eligible_bulk)}** produk dengan confidence HIGH dan status PENDING dalam filter saat ini.")

        if eligible_bulk:
            batch_limit = st.slider("Jumlah maksimal per batch", min_value=5, max_value=50, value=min(25, len(eligible_bulk)), step=5)
            confirm_bulk = st.checkbox("Saya telah memeriksa temuan ini dan mengonfirmasi persetujuan lokal", key="cat_bulk_confirm")
            if st.button("Setujui Massal (Lokal)", type="secondary", disabled=not confirm_bulk, key="cat_btn_bulk_approve"):
                ids_to_approve = [item["id"] for item in eligible_bulk[:batch_limit]]
                count = bulk_review_category_findings(db, ids_to_approve, "APPROVED", note="Batch approval operator lokal", max_batch=batch_limit)
                st.success(f"Berhasil menyetujui {count} rekomendasi di SQLite.")
                st.rerun()
        else:
            st.caption("Tidak ada produk HIGH confidence berstatus PENDING untuk persetujuan massal.")

    # Section 6: CSV Export
    st.divider()
    st.subheader("Ekspor Laporan Audit Kategori")
    csv_content = export_category_audit_csv(findings)
    st.download_button(
        label="Unduh Seluruh Hasil Audit (CSV)",
        data=csv_content.encode("utf-8-sig"),
        file_name=f"olsera_category_audit_run_{run_summary['id']}.csv",
        mime="text/csv",
        key="btn_download_cat_csv",
    )

    # Section 7: Evaluasi Kualitas Klasifikasi & Ground Truth (Prioritas 2)
    st.divider()
    st.subheader("Evaluasi Kualitas Klasifikasi & Ground Truth")
    st.caption(
        "Akurasi, precision, dan recall dihitung secara objektif terhadap dataset label terverifikasi (ground truth) "
        "dengan pemisahan data evaluasi (tanpa data leakage). Skor TF-IDF bukan klaim akurasi."
    )

    with db.connect() as con:
        gt_dataset = get_ground_truth_dataset(con)

    g_col1, g_col2 = st.columns([2, 3])
    with g_col1:
        st.metric("Label Ground Truth Tersimpan", f"{len(gt_dataset)} produk")
        if len(gt_dataset) < 50:
            if st.button("Muat Sampel Representatif Terverifikasi (Seed)", key="btn_seed_gt"):
                with db.connect() as con:
                    count = seed_representative_ground_truth(con)
                st.success(f"Berhasil memuat {count} sampel representatif ground-truth.")
                st.rerun()

    with g_col2:
        if st.button("Jalankan Evaluasi Akurasi (Bebas Leakage)", type="primary", key="btn_eval_gt"):
            with db.connect() as con:
                metrics, msg = evaluate_model_on_ground_truth(con, test_ratio=0.5)
            if not metrics:
                st.warning(msg)
            else:
                st.session_state["category_eval_metrics"] = metrics

    if "category_eval_metrics" in st.session_state:
        m: Any = st.session_state["category_eval_metrics"]
        st.write("---")
        st.markdown("#### Hasil Evaluasi Akurasi Terhadap Ground Truth (Holdout Test)")
        em1, em2, em3, em4 = st.columns(4)
        em1.metric("Akurasi Keseluruhan", f"{m.accuracy * 100:.1f}%")
        em2.metric("Precision (Macro)", f"{m.precision_macro * 100:.1f}%")
        em3.metric("Recall (Macro)", f"{m.recall_macro * 100:.1f}%")
        em4.metric("F1-Score (Macro)", f"{m.f1_macro * 100:.1f}%")

        st.markdown("#### Kualitas Rekomendasi Berdasarkan Tingkat Kepercayaan")
        c_h, c_m, c_l, c_o = st.columns(4)
        c_h.metric("Precision Rekomendasi HIGH", f"{m.precision_high_confidence * 100:.1f}%")
        c_m.metric("Precision Rekomendasi MEDIUM", f"{m.precision_medium_confidence * 100:.1f}%")
        c_l.metric("Precision Rekomendasi LOW", f"{m.precision_low_confidence * 100:.1f}%")
        c_o.metric("Akurasi Subset 'Others'", f"{m.others_subset_precision * 100:.1f}%")

        st.markdown("#### Confusion Matrix (Matriks Kontingensi)")
        st.write(m.confusion_matrix)

        if m.false_positives:
            with st.expander(f"Analisis False Positive ({len(m.false_positives)} produk)", expanded=False):
                st.caption("Produk yang keliru diprediksi sebagai 'Food' padahal sebenarnya 'Others' (Non-Food):")
                st.dataframe(m.false_positives, use_container_width=True, hide_index=True)

        if m.false_negatives:
            with st.expander(f"Analisis False Negative ({len(m.false_negatives)} produk)", expanded=False):
                st.caption("Produk yang sebenarnya 'Food' tetapi tidak terdeteksi:")
                st.dataframe(m.false_negatives, use_container_width=True, hide_index=True)

    with st.expander("Workflow Pelabelan Ground Truth Manual (Operator)", expanded=False):
        st.caption("Tambahkan label ground truth untuk memperkaya dataset evaluasi model.")
        with db.connect() as con:
            sample_prods = con.execute("SELECT olsera_product_id, name, category_name, klasifikasi FROM products_current LIMIT 100").fetchall()
        prod_map = {f"#{p[0]} — {p[1]} (Saat ini: {p[2]}, Klasifikasi: {p[3]})": p for p in sample_prods}
        chosen_prod_key = st.selectbox("Pilih Produk", list(prod_map.keys()), key="gt_prod_select")
        chosen_p = prod_map[chosen_prod_key]

        target_cats = {"Food": 2901, "Others": 9901}
        chosen_true_cat = st.selectbox("Kategori Ground Truth Sebenarnya", list(target_cats.keys()), key="gt_cat_select")
        gt_notes = st.text_input("Catatan Verifikasi", value="Diverifikasi operator", key="gt_notes_input")

        if st.button("Simpan Label Ground Truth", key="btn_save_gt"):
            with db.connect() as con:
                save_ground_truth(
                    con,
                    product_id=chosen_p[0],
                    true_category_id=target_cats[chosen_true_cat],
                    true_category_name=chosen_true_cat,
                    annotated_by="operator",
                    notes=gt_notes,
                )
            st.success(f"Label ground truth untuk produk #{chosen_p[0]} tersimpan.")
            st.rerun()
