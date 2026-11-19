"""Discovers real Olsera classifications and taxonomy relationships from SQLite."""

from dataclasses import dataclass, field
from typing import Dict, List, Set

CATCHALL_KLASIFIKASI_NAMES = {
    "lainlain",
    "lain-lain",
    "lainnya",
    "closing",
    "kebutuhan",
    "umum",
}


@dataclass
class KlasifikasiMeta:
    klasifikasi_id: int
    name: str
    product_group_id: int
    product_count: int
    percentage: float
    is_catch_all: bool = False
    is_low_sample: bool = False
    sample_product_names: List[str] = field(default_factory=list)


@dataclass
class KlasifikasiDiscoverySummary:
    total_products: int
    klasifikasi_count: int
    klasifikasi_map: Dict[int, KlasifikasiMeta]
    name_to_id: Dict[str, int]
    valid_klasifikasi_ids: Set[int]
    catch_all_ids: Set[int]
    low_sample_ids: Set[int]
    product_group_mismatch_count: int


def discover_klasifikasi(con) -> KlasifikasiDiscoverySummary:
    """Discover all 61 active classifications and evaluate product_group_id relations."""
    total = con.execute("SELECT COUNT(*) FROM products_current").fetchone()[0]
    if total == 0:
        return KlasifikasiDiscoverySummary(
            total_products=0,
            klasifikasi_count=0,
            klasifikasi_map={},
            name_to_id={},
            valid_klasifikasi_ids=set(),
            catch_all_ids=set(),
            low_sample_ids=set(),
            product_group_mismatch_count=0,
        )

    rows = con.execute(
        """
        SELECT klasifikasi_id, klasifikasi, product_group_id, COUNT(*)
        FROM products_current
        WHERE klasifikasi_id IS NOT NULL AND klasifikasi IS NOT NULL
        GROUP BY klasifikasi_id, klasifikasi, product_group_id
        ORDER BY COUNT(*) DESC
        """
    ).fetchall()

    mismatches = con.execute(
        """
        SELECT COUNT(*) FROM products_current
        WHERE klasifikasi_id != product_group_id OR product_group_id IS NULL
        """
    ).fetchone()[0]

    klasifikasi_map: Dict[int, KlasifikasiMeta] = {}
    name_to_id: Dict[str, int] = {}
    valid_ids: Set[int] = set()
    catch_all_ids: Set[int] = set()
    low_sample_ids: Set[int] = set()

    for kid, name, pg_id, count in rows:
        kid = int(kid)
        pg_id = int(pg_id) if pg_id is not None else kid
        clean_name = str(name).strip()
        pct = (count / total) * 100.0 if total > 0 else 0.0
        is_catch = clean_name.lower() in CATCHALL_KLASIFIKASI_NAMES
        is_low = count < 10

        # Sample products
        sample_rows = con.execute(
            "SELECT name FROM products_current WHERE klasifikasi_id = ? LIMIT 5",
            (kid,),
        ).fetchall()
        samples = [r[0] for r in sample_rows]

        meta = KlasifikasiMeta(
            klasifikasi_id=kid,
            name=clean_name,
            product_group_id=pg_id,
            product_count=count,
            percentage=round(pct, 2),
            is_catch_all=is_catch,
            is_low_sample=is_low,
            sample_product_names=samples,
        )
        klasifikasi_map[kid] = meta
        name_to_id[clean_name.upper()] = kid
        valid_ids.add(kid)
        if is_catch:
            catch_all_ids.add(kid)
        if is_low:
            low_sample_ids.add(kid)

    return KlasifikasiDiscoverySummary(
        total_products=total,
        klasifikasi_count=len(klasifikasi_map),
        klasifikasi_map=klasifikasi_map,
        name_to_id=name_to_id,
        valid_klasifikasi_ids=valid_ids,
        catch_all_ids=catch_all_ids,
        low_sample_ids=low_sample_ids,
        product_group_mismatch_count=mismatches,
    )
