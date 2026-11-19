"""Sales endpoint exploratory and fail-closed: no unsold claims if details unverified."""
import datetime as dt
import json
from .schema import setup
from olsera_cleaner.api.adapter import parse_page

def sync_sales(db,client,start_date,end_date,page_size=30,max_pages=100,progress=None):
    setup(db)
    start=dt.date.fromisoformat(str(start_date));end=dt.date.fromisoformat(str(end_date))
    if start>end or end>dt.date.today():
        raise ValueError('Rentang tanggal tidak valid')
    with db.connect() as con:
        run_id=con.execute('INSERT INTO cleaner_sales_runs(start_date,end_date) VALUES (?,?)',(str(start),str(end))).lastrowid
    try:
        total=None
        pages=0
        for page in range(1,max_pages+1):
            payload=client.get('/en/transaction/inextrans',{'start_trans_date':str(start),'end_trans_date':str(end),'per_page':page_size,'page':page})
            parsed=parse_page(payload,page)
            items=parsed.items
            expected=parsed.total
            if total is None: total=expected
            if expected!=total: raise ValueError('Jumlah transaksi berubah selama sinkronisasi')
            with db.connect() as con:
                for item in items:
                    if not isinstance(item,dict) or item.get('id') is None: raise ValueError('ID transaksi tidak tersedia')
                    con.execute('INSERT INTO cleaner_sales_raw(run_id,transaction_id,payload_json) VALUES (?,?,?)',(run_id,str(item['id']),json.dumps(item,ensure_ascii=False)))
            pages=page
            if progress: progress(page,parsed.last_page)
            if page>=parsed.last_page: break
        else:
            raise ValueError('Batas halaman tercapai; data belum lengkap')
        with db.connect() as con:
            actual=con.execute('SELECT COUNT(*) FROM cleaner_sales_raw WHERE run_id=?',(run_id,)).fetchone()[0]
            if actual!=total: raise ValueError(f'Jumlah transaksi berbeda: {actual}/{total}')
            con.execute("UPDATE cleaner_sales_runs SET status='COMPLETED', total=?,pages=?,finished_at=CURRENT_TIMESTAMP WHERE id=?",(total,pages,run_id))
        return {'run_id':run_id,'total':total,'pages':pages}
    except Exception as exc:
        with db.connect() as con:
            con.execute("UPDATE cleaner_sales_runs SET status='FAILED',error_summary=?,finished_at=CURRENT_TIMESTAMP WHERE id=?",(type(exc).__name__,run_id))
        raise

def sample(db,run_id=None):
    setup(db)
    with db.connect() as con:
        if run_id is None:
            r=con.execute("SELECT id,start_date,end_date,total FROM cleaner_sales_runs WHERE status='COMPLETED' ORDER BY id DESC LIMIT 1").fetchone()
        else:
            r=con.execute("SELECT id,start_date,end_date,total FROM cleaner_sales_runs WHERE id=? AND status='COMPLETED'",(run_id,)).fetchone()
        if not r:return None,[]
        rows=con.execute('SELECT payload_json FROM cleaner_sales_raw WHERE run_id=? LIMIT 3',(r[0],)).fetchall()
        return dict(zip(('id','start_date','end_date','total'),r)),[json.loads(row[0]) for row in rows]


# ===== PRODUCT SALES BY SKU - OLSERA =====
# Tambahkan di bagian paling bawah sales.py

import json as _json
import datetime as _dt


def sync_product_sales(
    db, client, start_date, end_date,
    page_size=50, max_pages=100
):
    start = _dt.date.fromisoformat(str(start_date))
    end = _dt.date.fromisoformat(str(end_date))

    if start > end or end > _dt.date.today():
        raise ValueError("Periode tanggal tidak valid")

    page_size = int(page_size)
    max_pages = int(max_pages)

    if not 1 <= page_size <= 200:
        raise ValueError("per_page harus antara 1-200")

    if not 1 <= max_pages <= 1000:
        raise ValueError("max_pages harus antara 1-1000")

    # Tabel baru; tidak mengubah database produk lama.
    with db.connect() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS cleaner_product_sales_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'RUNNING',
                total INTEGER DEFAULT 0,
                pages INTEGER DEFAULT 0,
                error_summary TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS cleaner_product_sales_rows (
                run_id INTEGER NOT NULL,
                row_number INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (run_id, row_number),
                FOREIGN KEY (run_id)
                    REFERENCES cleaner_product_sales_runs(id)
            );
        """)

        run_id = con.execute(
            """
            INSERT INTO cleaner_product_sales_runs
            (start_date, end_date)
            VALUES (?, ?)
            """,
            (str(start), str(end))
        ).lastrowid

    try:
        total = None
        last_page = None
        rows_saved = 0
        pages_saved = 0

        for page in range(1, max_pages + 1):
            payload = client.get(
                "/en/report/productsalesbysku",
                {
                    "from": str(start),
                    "to": str(end),
                    "per_page": page_size,
                    "page": page
                }
            )

            if not isinstance(payload, dict):
                raise ValueError(
                    "Response penjualan bukan objek JSON"
                )

            # Kita belum memiliki response asli endpoint ini.
            # Jangan menebak struktur data secara diam-diam.
            items = payload.get("data")
            meta = payload.get("meta")

            if not isinstance(items, list):
                raise ValueError(
                    "Struktur data penjualan belum dikenal. "
                    "Field data bukan array. "
                    "Perlu contoh response JSON asli."
                )

            if not isinstance(meta, dict):
                raise ValueError(
                    "Metadata pagination belum dikenal. "
                    "Perlu contoh response JSON asli."
                )

            try:
                current_page = int(meta["current_page"])
                current_last = int(meta["last_page"])
                current_total = int(meta["total"])
            except (KeyError, TypeError, ValueError):
                raise ValueError(
                    "Format meta pagination tidak dikenali"
                )

            if current_page != page:
                raise ValueError(
                    "Nomor halaman response tidak sesuai"
                )

            if current_last < page or current_total < 0:
                raise ValueError(
                    "Metadata pagination tidak valid"
                )

            if total is None:
                total = current_total
                last_page = current_last
            elif total != current_total or last_page != current_last:
                raise ValueError(
                    "Jumlah atau halaman laporan berubah "
                    "selama sinkronisasi"
                )

            if page < last_page and not items:
                raise ValueError(
                    "Halaman kosong sebelum halaman terakhir"
                )

            with db.connect() as con:
                for item in items:
                    if not isinstance(item, dict):
                        raise ValueError(
                            "Item laporan bukan objek JSON"
                        )

                    rows_saved += 1

                    con.execute(
                        """
                        INSERT INTO cleaner_product_sales_rows
                        (run_id, row_number, payload_json)
                        VALUES (?, ?, ?)
                        """,
                        (
                            run_id,
                            rows_saved,
                            _json.dumps(
                                item, ensure_ascii=False
                            )
                        )
                    )

            pages_saved = page

            if page >= last_page:
                break
        else:
            raise ValueError(
                "Batas halaman tercapai. "
                "Sinkronisasi belum lengkap."
            )

        if rows_saved != total:
            raise ValueError(
                f"Jumlah data tidak sesuai: "
                f"{rows_saved}/{total}"
            )

        with db.connect() as con:
            con.execute(
                """
                UPDATE cleaner_product_sales_runs
                SET status='COMPLETED',
                    total=?,
                    pages=?
                WHERE id=?
                """,
                (rows_saved, pages_saved, run_id)
            )

        return {
            "run_id": run_id,
            "total": rows_saved,
            "pages": pages_saved
        }

    except Exception as exc:
        with db.connect() as con:
            con.execute(
                """
                UPDATE cleaner_product_sales_runs
                SET status='FAILED',
                    error_summary=?
                WHERE id=?
                """,
                (type(exc).__name__, run_id)
            )
        raise


def latest_product_sales(db, limit=10):
    with db.connect() as con:
        table_exists = con.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type='table'
            AND name='cleaner_product_sales_runs'
            """
        ).fetchone()

        if not table_exists:
            return None, []

        run = con.execute(
            """
            SELECT id, start_date, end_date, total, pages
            FROM cleaner_product_sales_runs
            WHERE status='COMPLETED'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

        if not run:
            return None, []

        rows = con.execute(
            """
            SELECT payload_json
            FROM cleaner_product_sales_rows
            WHERE run_id=?
            ORDER BY row_number
            LIMIT ?
            """,
            (run[0], int(limit))
        ).fetchall()

    summary = {
        "run_id": run[0],
        "start_date": run[1],
        "end_date": run[2],
        "total": run[3],
        "pages": run[4]
    }

    return summary, [
        _json.loads(row[0]) for row in rows
    ]
