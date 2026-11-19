"""AI Taxonomy Manager & Controlled Reclassification V3 UI: model management, open-set discovery, and dynamic audit."""

import json
import math
from typing import Any, List
import streamlit as st

from olsera_cleaner.classification.deduplication import detect_taxonomy_redundancies
from olsera_cleaner.classification.engine import run_classification_audit
from olsera_cleaner.classification.evaluator import (
    evaluate_klasifikasi_model,
    get_klasifikasi_ground_truth_dataset,
    save_klasifikasi_ground_truth,
    seed_representative_klasifikasi_ground_truth,
)
from olsera_cleaner.classification.model_registry import ModelRegistry
from olsera_cleaner.classification.monitoring import generate_monitoring_report
from olsera_cleaner.classification.registry import TaxonomyRegistry
from olsera_cleaner.classification.schema import setup as setup_classification_schema
from olsera_cleaner.classification.store import (
    bulk_review_classification_findings,
    export_classification_audit_csv,
    get_recent_classification_audit,
    review_classification_finding,
    save_classification_audit,
)
from olsera_cleaner.classification.verifier import KlasifikasiAIVerifier


def render_classification_audit(db):
    """Render AI Taxonomy Manager & Controlled Reclassification V3 tab in Streamlit."""
    setup_classification_schema(db)
    st.subheader("AI Taxonomy Manager & Controlled Reclassification V3")
    st.info(
        "Mesin klasifikasi adaptif dan bebas hardcoding: taksonomi dibaca dinamis saat runtime, "
        "mampu mendeteksi variasi penamaan tanpa daftar statis, dan mendukung penemuan kelas baru (Open-Set). "
        "Seluruh keputusan operator tersimpan secara lokal dan TIDAK mengubah data produk live di Olsera."
    )

    registry = TaxonomyRegistry()
    model_reg = ModelRegistry(registry)

    # Section 1: AI Classification Model Management & Lifecycle
    with st.expander("AI Classification Model Management & Lifecycle", expanded=True):
        with db.connect() as con:
            current_snapshot = registry.discover_live_taxonomy(con)
            report = generate_monitoring_report(con, registry, current_snapshot)

        sm1, sm2, sm3, sm4 = st.columns(4)
        sm1.metric("Klasifikasi Aktif", f"{report.active_classes_count}")
        sm2.metric("Produk Terindeks", f"{report.total_products:,}")
        sm3.metric("Label Terverifikasi (GT)", f"{report.verified_ground_truth_count}")
        status_label = "STALE (Perlu Refresh)" if report.is_stale else "READY"
        sm4.metric("Status Model", status_label)

        vm1, vm2, vm3, vm4 = st.columns(4)
        vm1.metric("Versi Model Aktif", report.active_model_version)
        vm2.metric("Versi Taksonomi", report.active_taxonomy_version)
        vm3.metric("Tingkat Unknown (Open-Set)", f"{report.unknown_rate * 100:.2f}%")
        vm4.metric("Drift Distribusi", f"{report.distribution_shift_score:.3f}")

        # Model management buttons
        b1, b2, b3, b4 = st.columns(4)
        with b1:
            if st.button("Refresh Taksonomi Dinamis", key="btn_refresh_tax"):
                with db.connect() as con:
                    snap = registry.discover_live_taxonomy(con)
                    registry.persist_version(con, snap)
                st.success(f"Taksonomi diperbarui: versi {snap.version_hash} ({snap.classifications_count} kelas).")
                st.rerun()

        with b2:
            if st.button("Rebuild Model Index", key="btn_rebuild_index"):
                with db.connect() as con:
                    snap = registry.discover_live_taxonomy(con)
                    _, c_info = model_reg.train_candidate_model(con, snap)
                st.info(f"Model kandidat {c_info.model_version} berhasil dibangun. Jalankan evaluasi untuk aktivasi.")
                st.rerun()

        with b3:
            if st.button("Evaluasi & Validasi Model", type="primary", key="btn_eval_candidate"):
                with db.connect() as con:
                    snap = registry.discover_live_taxonomy(con)
                    active_info = model_reg.get_active_model_info(con)
                    if not active_info:
                        _, active_info = model_reg.train_candidate_model(con, snap)
                    ok, msg, _ = model_reg.evaluate_and_promote(con, active_info, test_ratio=0.35)
                if ok:
                    st.success(msg)
                else:
                    st.warning(msg)
                st.rerun()

        with b4:
            if st.button("Lihat Riwayat Versi Model", key="btn_hist_models"):
                with db.connect() as con:
                    model_rows = con.execute(
                        """
                        SELECT model_version, taxonomy_version, status, trained_on_items, macro_f1, accuracy, created_at
                        FROM cleaner_classification_models ORDER BY id DESC LIMIT 10
                        """
                    ).fetchall()
                st.session_state["show_model_history"] = [
                    {
                        "Model Version": r[0],
                        "Taxonomy Version": r[1],
                        "Status": r[2],
                        "Trained Items": r[3],
                        "Macro F1": f"{r[4]:.2f}",
                        "Accuracy": f"{r[5]:.2f}",
                        "Created At": r[6],
                    }
                    for r in model_rows
                ]

        if st.session_state.get("show_model_history"):
            st.markdown("##### Riwayat Versi Model")
            st.dataframe(st.session_state["show_model_history"], use_container_width=True, hide_index=True)

        # Open-Set Proposed New Classes
        with db.connect() as con:
            pending_proposals = con.execute(
                """
                SELECT id, proposed_name, item_count, sample_product_names_json, top_tokens_json, confidence_score
                FROM cleaner_classification_new_class_proposals
                WHERE status = 'PENDING'
                ORDER BY item_count DESC
                """
            ).fetchall()

        if pending_proposals:
            st.divider()
            st.markdown("##### Usulan Klasifikasi Baru (Open-Set Discovery)")
            st.caption("Kelompok produk dengan karakteristik teks yang tidak cocok dengan kelas eksisting:")
            for prop in pending_proposals:
                p_id, p_name, p_cnt, p_samples_raw, p_tokens_raw, p_conf = prop
                p_samples = json.loads(p_samples_raw or "[]")
                st.warning(
                    f"**Usulan Kelas: `{p_name}`** ({p_cnt} produk, confidence: {p_conf:.2f})\n\n"
                    f"- Contoh produk: {', '.join(p_samples[:5])}\n"
                    f"- *Catatan:* Pembuatan kelas baru memerlukan persetujuan operator resmi di Olsera."
                )

    # Section 2: Configuration & Execution of Audit
    with st.expander("Konfigurasi & Eksekusi Audit Klasifikasi Adaptif", expanded=False):
        c1, c2, c3 = st.columns([2, 2, 1])
        with c1:
            use_ollama = st.checkbox("Gunakan verifikasi Ollama AI untuk kasus ambigu", value=False, key="kla_use_ollama")
        with c2:
            model_name = st.text_input("Model Ollama", value="llama3:8b", disabled=not use_ollama, key="kla_model_name")
        with c3:
            max_ai_items = st.number_input("Batas verifikasi AI", min_value=5, max_value=100, value=25, step=5, disabled=not use_ollama, key="kla_max_ai")

        if st.button("Jalankan Audit Klasifikasi Adaptif Sekarang", type="primary", key="btn_run_classification_audit"):
            progress_bar = st.progress(0)
            status_text = st.empty()

            def _on_progress(pct, total, message):
                progress_bar.progress(min(pct / max(total, 1), 1.0))
                status_text.caption(message)

            verifier = None
            if use_ollama:
                try:
                    verifier = KlasifikasiAIVerifier(model=model_name.strip())
                except Exception as e:
                    st.warning(f"Gagal menginisialisasi Ollama ({e}). Audit dilanjutkan dengan metode adaptif.")

            try:
                with db.connect() as con:
                    snapshot, findings, redundancies = run_classification_audit(
                        con,
                        verifier=verifier,
                        progress_callback=_on_progress,
                        max_ai_verifications=int(max_ai_items),
                    )
                run_id = save_classification_audit(db, snapshot, findings, redundancies)
                st.success(
                    f"Audit Klasifikasi #{run_id} selesai! Ditemukan {snapshot.classifications_count} klasifikasi aktif, "
                    f"{len(findings)} temuan salah klasifikasi / kelas baru, dan {len(redundancies)} klaster redundansi dinamis."
                )
                st.rerun()
            except Exception as exc:
                st.error(f"Audit klasifikasi gagal: {type(exc).__name__}: {exc}")

    run_summary, findings = get_recent_classification_audit(db, limit=5000)

    if not run_summary:
        st.caption("Belum ada riwayat audit klasifikasi. Klik 'Jalankan Audit Klasifikasi Adaptif Sekarang' di atas.")
        with db.connect() as con:
            live_snapshot = registry.discover_live_taxonomy(con)
            live_redundancies = detect_taxonomy_redundancies(con, live_snapshot)
        if live_snapshot.total_products > 0:
            st.divider()
            st.write(f"Distribusi {live_snapshot.classifications_count} Klasifikasi Aktif di SQLite ({live_snapshot.total_products:,} produk terdaftar):")
            kla_table = [
                {
                    "ID": kid,
                    "Nama Klasifikasi": m.name,
                    "Jumlah Produk": f"{m.product_count:,}",
                    "Persentase": f"{m.percentage:.2f}%",
                    "Status": "Catch-All / Umum" if m.is_catch_all else ("Sedikit Sampel (<10)" if m.is_low_sample else "Spesifik"),
                }
                for kid, m in sorted(live_snapshot.klasifikasi_map.items(), key=lambda x: x[1].product_count, reverse=True)
            ]
            st.dataframe(kla_table, use_container_width=True, hide_index=True)

            if live_redundancies:
                st.divider()
                st.subheader("Audit Tumpang Tindih & Redundansi Taksonomi (Algoritmik Dinamis)")
                for r in live_redundancies:
                    st.warning(
                        f"**{r.cluster_name}**\n\n"
                        f"- Klaster Utama (Kanonikal): `{r.canonical_name}` (ID: {r.canonical_id}) — {r.canonical_count} produk\n"
                        f"- Klaster Redundan: `{r.redundant_name}` (ID: {r.redundant_id}) — {r.redundant_count} produk\n"
                        f"- **Rekomendasi:** {r.recommendation}"
                    )
        return

    st.divider()
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Total Produk Dianalisis", f"{run_summary['items_seen']:,}")
    m2.metric("Klasifikasi Terdeteksi", f"{run_summary['classifications_found']:,}")
    m3.metric("Temuan Salah Klasifikasi", f"{run_summary['findings_count']:,}")
    m4.metric("Klaster Redundansi Dinamis", f"{run_summary['redundancy_count']:,}")

    approved_count = sum(1 for f in findings if f["decision"] == "APPROVED")
    pending_count = sum(1 for f in findings if f["decision"] == "PENDING")
    m5.metric("Status Review", f"{approved_count} Disetujui / {pending_count} Pending")

    st.caption(
        f"Audit #{run_summary['id']} • Sinkronisasi Sumber #{run_summary['source_sync_run_id']} • "
        f"Waktu: {run_summary['started_at']} • Versi: {run_summary['method_version']}"
    )

    with st.expander("Audit Tumpang Tindih & Redundansi Taksonomi (Dideteksi Otomatis)", expanded=True):
        st.caption(
            "Deteksi redundansi taksonomi menggunakan normalisasi spasi, kemiripan fonetik, dan tumpang tindih kosakata produk. "
            "PENTING: Sistem TIDAK menggabungkan klasifikasi secara otomatis demi menjaga integritas data Olsera."
        )
        with db.connect() as con:
            current_snap = registry.discover_live_taxonomy(con)
            redundancies = detect_taxonomy_redundancies(con, current_snap)
        if redundancies:
            for r in redundancies:
                st.markdown(
                    f"#### {r.cluster_name}\n"
                    f"- **Kanonikal:** `{r.canonical_name}` (ID: {r.canonical_id}) — **{r.canonical_count:,}** produk\n"
                    f"- **Redundan/Varian:** `{r.redundant_name}` (ID: {r.redundant_id}) — **{r.redundant_count:,}** produk\n"
                    f"- **Tipe Redundansi:** `{r.redundancy_type}` (Skor Kemiripan: {r.similarity_score:.2f})\n"
                    f"- **Rekomendasi:** {r.recommendation}\n"
                    f"- *Catatan:* Perubahan master taksonomi memerlukan persetujuan eksplisit operator."
                )
                st.write("---")
        else:
            st.info("Tidak terdeteksi tumpang tindih taksonomi kritis.")

    st.divider()
    st.subheader("Daftar Temuan Salah Klasifikasi & Open-Set Produk")

    f1, f2, f3, f4, f5, f6 = st.columns([3, 2, 2, 2, 2, 2])
    with f1:
        query_search = st.text_input("Cari Nama / SKU / Barcode", key="kla_search").strip().lower()
    with f2:
        anomaly_types = ["Semua"] + sorted({f["anomaly_type"] for f in findings})
        selected_anomaly = st.selectbox("Tipe Anomali", anomaly_types, key="kla_filter_anomaly")
    with f3:
        source_klasifikasi = ["Semua"] + sorted({f["current_klasifikasi"] for f in findings})
        selected_source = st.selectbox("Klasifikasi Asal", source_klasifikasi, key="kla_filter_source")
    with f4:
        target_klasifikasi = ["Semua"] + sorted({f["suggested_klasifikasi"] for f in findings})
        selected_target = st.selectbox("Rekomendasi", target_klasifikasi, key="kla_filter_target")
    with f5:
        selected_conf = st.selectbox("Tingkat Confidence", ["Semua", "HIGH", "MEDIUM", "LOW"], key="kla_filter_conf")
    with f6:
        selected_status = st.selectbox("Status Review", ["Semua", "PENDING", "APPROVED", "REJECTED", "DEFERRED", "CORRECTED"], key="kla_filter_status")

    filtered = findings
    if query_search:
        filtered = [
            f for f in filtered
            if query_search in (f["product_name"] or "").lower()
            or query_search in (f["sku"] or "").lower()
            or query_search in (f["barcode"] or "").lower()
        ]
    if selected_anomaly != "Semua":
        filtered = [f for f in filtered if f["anomaly_type"] == selected_anomaly]
    if selected_source != "Semua":
        filtered = [f for f in filtered if f["current_klasifikasi"] == selected_source]
    if selected_target != "Semua":
        filtered = [f for f in filtered if f["suggested_klasifikasi"] == selected_target]
    if selected_conf != "Semua":
        filtered = [f for f in filtered if f["confidence_level"] == selected_conf]
    if selected_status != "Semua":
        filtered = [f for f in filtered if f["decision"] == selected_status]

    st.write(f"Menampilkan **{len(filtered):,}** dari total {len(findings):,} temuan audit.")

    if not filtered:
        st.info("Tidak ada temuan yang cocok dengan kriteria filter saat ini.")
        return

    p_col1, p_col2 = st.columns([2, 4])
    with p_col1:
        page_size = st.selectbox("Baris per halaman", [25, 50, 100], index=0, key="kla_page_size")
    total_pages = max(1, math.ceil(len(filtered) / page_size))
    with p_col2:
        page_num = st.number_input("Halaman", min_value=1, max_value=total_pages, value=1, step=1, key="kla_page_num")

    start_idx = (page_num - 1) * page_size
    end_idx = min(start_idx + page_size, len(filtered))
    page_items = filtered[start_idx:end_idx]

    display_table = [
        {
            "ID Temuan": item["id"],
            "ID Produk": item["product_id"],
            "Nama Produk": item["product_name"],
            "SKU": item["sku"] or "-",
            "Klasifikasi Asal": item["current_klasifikasi"],
            "Rekomendasi": item["suggested_klasifikasi"],
            "Tipe Anomali": item["anomaly_type"],
            "Skor": f"{item['confidence_score']:.2f}",
            "Confidence": item["confidence_level"],
            "Metode": item["classification_method"],
            "Keputusan": item["decision"],
        }
        for item in page_items
    ]
    st.dataframe(display_table, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Detail Temuan & Keputusan Operator (Lokal)")

    item_options = {
        f"#{it['product_id']} — {it['product_name'][:55]} | {it['current_klasifikasi']} → {it['suggested_klasifikasi']} ({it['confidence_level']})": it
        for it in page_items
    }
    selected_label = st.selectbox("Pilih produk untuk ditinjau", list(item_options.keys()), key="kla_select_item")
    curr_item = item_options[selected_label]

    if curr_item.get("source_changed"):
        st.warning("PERINGATAN: Data produk di SQLite telah berubah sejak audit ini dibuat. Tinjau kembali sebelum menyetujui.")

    d_col1, d_col2 = st.columns(2)
    with d_col1:
        st.markdown(f"**Nama Produk:** {curr_item['product_name']}")
        st.markdown(f"**SKU:** `{curr_item['sku'] or '-'}` • **Barcode:** `{curr_item['barcode'] or '-'}`")
        st.markdown(f"**Klasifikasi Saat Ini:** `{curr_item['current_klasifikasi']}` (ID: {curr_item['current_klasifikasi_id']})")
        st.markdown(f"**Klasifikasi Rekomendasi:** `{curr_item['suggested_klasifikasi']}` (ID: {curr_item['suggested_klasifikasi_id']})")
        st.markdown(f"**Tipe Anomali:** `{curr_item['anomaly_type']}`")
        st.markdown(f"**Confidence:** `{curr_item['confidence_level']}` ({curr_item['confidence_score']:.2f}) • **Metode:** `{curr_item['classification_method']}`")

    with d_col2:
        st.markdown(f"**Alasan Rekomendasi:**\n> {curr_item['reason']}")
        if curr_item.get("comparison_product_name"):
            st.markdown(
                f"**Produk Pembanding Terdekat:** '{curr_item['comparison_product_name']}' "
                f"(Kemiripan: {curr_item['comparison_similarity']:.2f})"
            )
        st.markdown(f"**Keputusan Saat Ini:** `{curr_item['decision']}`")

    note_input = st.text_input("Catatan Operator (opsional)", value=curr_item.get("note") or "", key="kla_review_note")

    b_col1, b_col2, b_col3 = st.columns(3)
    with b_col1:
        if st.button("Setujui Rekomendasi", type="primary", key=f"kla_approve_{curr_item['id']}"):
            review_classification_finding(db, curr_item["id"], "APPROVED", note=note_input)
            st.success(f"Rekomendasi klasifikasi produk #{curr_item['product_id']} disetujui di SQLite & Ground Truth.")
            st.rerun()
    with b_col2:
        if st.button("Tolak Rekomendasi", key=f"kla_reject_{curr_item['id']}"):
            review_classification_finding(db, curr_item["id"], "REJECTED", note=note_input)
            st.info(f"Rekomendasi klasifikasi produk #{curr_item['product_id']} ditolak di SQLite & dicatat di umpan balik negatif.")
            st.rerun()
    with b_col3:
        if st.button("Tunda / Review Nanti", key=f"kla_defer_{curr_item['id']}"):
            review_classification_finding(db, curr_item["id"], "DEFERRED", note=note_input)
            st.warning(f"Rekomendasi klasifikasi produk #{curr_item['product_id']} ditunda.")
            st.rerun()

    st.divider()
    with st.expander("Persetujuan Massal Terkontrol (Lokal)", expanded=False):
        st.caption(
            "Persetujuan massal hanya berlaku secara lokal di database SQLite. "
            "Untuk keamanan, persetujuan massal dibatasi maksimal 50 produk per aksi."
        )
        eligible_bulk = [f for f in filtered if f["decision"] == "PENDING" and f["confidence_level"] == "HIGH"]
        st.write(f"Terdapat **{len(eligible_bulk)}** produk dengan confidence HIGH dan status PENDING dalam filter saat ini.")

        if eligible_bulk:
            batch_limit = st.slider("Jumlah maksimal per batch", min_value=5, max_value=50, value=min(25, len(eligible_bulk)), step=5, key="kla_bulk_limit")
            confirm_bulk = st.checkbox("Saya telah memeriksa temuan ini dan mengonfirmasi persetujuan lokal", key="kla_bulk_confirm")
            if st.button("Setujui Massal (Lokal)", type="secondary", disabled=not confirm_bulk, key="kla_btn_bulk_approve"):
                ids_to_approve = [item["id"] for item in eligible_bulk[:batch_limit]]
                count = bulk_review_classification_findings(db, ids_to_approve, "APPROVED", note="Batch approval operator lokal", max_batch=batch_limit)
                st.success(f"Berhasil menyetujui {count} rekomendasi klasifikasi di SQLite.")
                st.rerun()
        else:
            st.caption("Tidak ada produk HIGH confidence berstatus PENDING untuk persetujuan massal.")

    st.divider()
    st.subheader("Ekspor Laporan Audit Klasifikasi")
    csv_content = export_classification_audit_csv(findings)
    st.download_button(
        label="Unduh Seluruh Hasil Audit Klasifikasi (CSV)",
        data=csv_content.encode("utf-8-sig"),
        file_name=f"olsera_classification_audit_run_{run_summary['id']}.csv",
        mime="text/csv",
        key="btn_download_kla_csv",
    )

    st.divider()
    st.subheader("Evaluasi Kualitas Model Multi-Klasifikasi & Ground Truth")
    st.caption(
        "Akurasi, precision macro, recall macro, dan confusion matrix dihitung terhadap ground truth "
        "menggunakan Holdout Test (produk uji dikeluarkan dari korpus training untuk mencegah data leakage)."
    )

    with db.connect() as con:
        gt_dataset = get_klasifikasi_ground_truth_dataset(con)

    g_col1, g_col2 = st.columns([2, 3])
    with g_col1:
        st.metric("Label Ground Truth Tersimpan", f"{len(gt_dataset)} produk")
        if len(gt_dataset) < 40:
            if st.button("Muat Sampel Representatif Terverifikasi (Seed)", key="btn_seed_kla_gt"):
                with db.connect() as con:
                    count = seed_representative_klasifikasi_ground_truth(con)
                st.success(f"Berhasil memuat {count} sampel representatif ground-truth.")
                st.rerun()

    with g_col2:
        if st.button("Jalankan Evaluasi Model (Holdout Test Bebas Leakage)", type="primary", key="btn_eval_kla_gt"):
            with db.connect() as con:
                metrics, msg = evaluate_klasifikasi_model(con, test_ratio=0.35)
            if not metrics:
                st.warning(msg)
            else:
                st.session_state["classification_eval_metrics"] = metrics

    if "classification_eval_metrics" in st.session_state:
        m: Any = st.session_state["classification_eval_metrics"]
        st.write("---")
        st.markdown("#### Hasil Evaluasi Multi-Class Terhadap Ground Truth (Holdout Test)")
        em1, em2, em3, em4 = st.columns(4)
        em1.metric("Akurasi Keseluruhan", f"{m.accuracy * 100:.1f}%")
        em2.metric("Precision (Macro)", f"{m.precision_macro * 100:.1f}%")
        em3.metric("Recall (Macro)", f"{m.recall_macro * 100:.1f}%")
        em4.metric("F1-Score (Macro)", f"{m.f1_macro * 100:.1f}%")

        st.markdown("#### Kualitas Rekomendasi Berdasarkan Tingkat Kepercayaan")
        c_h, c_m, c_l, _ = st.columns(4)
        c_h.metric("Precision Rekomendasi HIGH", f"{m.precision_high_confidence * 100:.1f}%")
        c_m.metric("Precision Rekomendasi MEDIUM", f"{m.precision_medium_confidence * 100:.1f}%")
        c_l.metric("Precision Rekomendasi LOW", f"{m.precision_low_confidence * 100:.1f}%")

        with st.expander("Confusion Matrix & Metrik Per Klasifikasi", expanded=False):
            st.markdown("##### Metrik Per Klasifikasi")
            class_table = [
                {
                    "Klasifikasi": c_name,
                    "Precision": f"{c_stats['precision'] * 100:.1f}%",
                    "Recall": f"{c_stats['recall'] * 100:.1f}%",
                    "F1-Score": f"{c_stats['f1'] * 100:.1f}%",
                }
                for c_name, c_stats in sorted(m.class_metrics.items())
            ]
            st.dataframe(class_table, use_container_width=True, hide_index=True)

            st.markdown("##### Confusion Matrix")
            st.write(m.confusion_matrix)

        if m.false_positives:
            with st.expander(f"Analisis Kesalahan Klasifikasi ({len(m.false_positives)} produk)", expanded=False):
                st.dataframe(m.false_positives, use_container_width=True, hide_index=True)

    with st.expander("Workflow Pelabelan Ground Truth Manual (Operator)", expanded=False):
        st.caption("Tambahkan atau perbaiki ground-truth klasifikasi produk Olsera.")
        with db.connect() as con:
            sample_prods = con.execute("SELECT olsera_product_id, name, klasifikasi, klasifikasi_id FROM products_current LIMIT 100").fetchall()
            all_klasifikasi = con.execute("SELECT DISTINCT klasifikasi_id, klasifikasi FROM products_current ORDER BY klasifikasi").fetchall()

        prod_map = {f"#{p[0]} — {p[1]} (Saat ini: {p[2]})": p for p in sample_prods}
        chosen_prod_key = st.selectbox("Pilih Produk", list(prod_map.keys()), key="kla_gt_prod_select")
        chosen_p = prod_map[chosen_prod_key]

        target_map = {f"{k[1]} (ID: {k[0]})": k for k in all_klasifikasi}
        chosen_kla_key = st.selectbox("Klasifikasi Sebenarnya (Ground Truth)", list(target_map.keys()), key="kla_gt_target_select")
        chosen_k = target_map[chosen_kla_key]

        gt_notes = st.text_input("Catatan Operator", value="Diverifikasi operator", key="kla_gt_notes_input")

        if st.button("Simpan Label Ground Truth Klasifikasi", key="btn_save_kla_gt"):
            with db.connect() as con:
                save_klasifikasi_ground_truth(
                    con,
                    product_id=chosen_p[0],
                    true_klasifikasi_id=chosen_k[0],
                    true_klasifikasi=chosen_k[1],
                    annotated_by="operator",
                    notes=gt_notes,
                )
            st.success(f"Label ground truth klasifikasi untuk produk #{chosen_p[0]} ({chosen_k[1]}) tersimpan.")
            st.rerun()
