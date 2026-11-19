"""Stage 2 audit UI: local inspection and proposal review; no Olsera writes."""
import csv
import io
import streamlit as st
from olsera_cleaner.audit.store import setup, run_audit, recent, review


def render(db):
    st.subheader("Audit kualitas produk — Tahap 2")
    st.info("Seluruh pemeriksaan membaca SQLite lokal. Persetujuan hanya disimpan secara lokal; tidak mengubah Olsera.")
    st.caption("Audit: format nama uppercase/satuan, singkatan ambigu, duplikat nama, SKU, barcode. Temuan duplikat adalah kandidat, bukan bukti produk yang sama.")
    setup(db)
    if st.button("Jalankan audit lokal", type="primary", key="audit_run"):
        try:
            with st.spinner("Menganalisis snapshot terakhir..."):
                result=run_audit(db)
            st.success(f"Audit #{result['run_id']} selesai: {result['products']} produk, {result['findings']} temuan.")
        except Exception as exc:
            st.error(f"Audit gagal: {type(exc).__name__}: {exc}")
    summary, findings=recent(db)
    if not summary:
        st.caption("Belum ada audit. Jalankan audit lokal setelah sinkronisasi produk selesai.")
        return
    st.write(f"Audit #{summary['run_id']} • Sinkronisasi sumber #{summary['source_sync_run_id']} • {summary['products']} produk • {summary['findings']} temuan")
    if summary['findings'] > 500:
        st.warning("Tabel hanya menampilkan 500 temuan pertama. Unduhan CSV memuat seluruh temuan audit.")
    kinds=sorted({r['kind'] for r in findings})
    selected=st.selectbox("Filter temuan",["Semua"]+kinds,key="audit_filter")
    shown=[r for r in findings if selected=="Semua" or r['kind']==selected]
    st.dataframe(shown,use_container_width=True,hide_index=True)
    with db.connect() as con:
        export_rows=con.execute("""SELECT product_id,kind,severity,old_name,suggested_name,related_ids,reason
             FROM cleaner_audit_findings WHERE audit_run_id=? ORDER BY id""",(summary['run_id'],)).fetchall()
    fields=("product_id","kind","severity","old_name","suggested_name","related_ids","reason")
    out=io.StringIO(newline="")
    writer=csv.writer(out)
    writer.writerow(fields)
    def safe(v):
        s=str(v if v is not None else "")
        return "'"+s if s.lstrip().startswith(("=","+","-","@","\t","\r","\n")) else s
    for record in export_rows:
        writer.writerow([safe(v) for v in record])
    st.download_button("Unduh hasil audit CSV",out.getvalue().encode("utf-8-sig"),
                       file_name=f"olsera_audit_run{summary['run_id']}.csv",mime="text/csv")
    st.divider()
    st.subheader("Persetujuan usulan format nama (lokal)")
    st.caption("Hanya temuan NAME_FORMAT. Nama dengan singkatan ambigu harus dikaji terpisah; tidak dikirim ke API.")
    eligible=[r for r in findings if r['kind']=="NAME_FORMAT" and not any(x['kind']=="ABBREVIATION_AMBIGUOUS" and x['product_id']==r['product_id'] for x in findings)]
    if not eligible:
        st.caption("Tidak ada usulan format sederhana pada 500 baris yang sedang ditampilkan.")
        return
    opts={f"#{r['product_id']} — {r['old_name'][:65]} → {r['suggested_name'][:65]}":r for r in eligible}
    choice=st.selectbox("Pilih usulan",list(opts),key="audit_choice")
    item=opts[choice]
    st.write({"Sebelum":item['old_name'],"Saran":item['suggested_name'],"Status":item['decision']})
    c1,c2=st.columns(2)
    if c1.button("Setujui usulan lokal",key="audit_approve"):
        review(db,item['id'],"APPROVED")
        st.success("Disetujui di SQLite; belum ada perubahan di Olsera.")
        st.rerun()
    if c2.button("Tolak usulan",key="audit_reject"):
        review(db,item['id'],"REJECTED")
        st.success("Ditolak di SQLite.")
        st.rerun()
