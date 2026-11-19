"""Smart Category Discovery: extracts real categories and catalog distributions from SQLite."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

CATCH_ALL_NAMES = {
    "others",
    "other",
    "lain-lain",
    "lainnya",
    "general",
    "umum",
    "unassigned",
    "uncategorized",
    "tanpa kategori",
    "default",
    "none",
}


@dataclass
class CategoryMeta:
    category_id: int
    name: str
    product_count: int
    percentage: float
    is_catch_all: bool = False
    is_low_sample: bool = False
    sample_product_names: List[str] = field(default_factory=list)


@dataclass
class DiscoverySummary:
    total_products: int
    categorized_count: int
    uncategorized_count: int
    inconsistent_count: int
    categories: Dict[int, CategoryMeta]
    valid_category_ids: Set[int]
    name_to_id: Dict[str, int]
    uncategorized_product_ids: List[int]
    inconsistent_product_ids: List[int]


def discover_categories(con) -> DiscoverySummary:
    """Discover real categories from products_current table in SQLite."""
    total = con.execute("SELECT COUNT(*) FROM products_current").fetchone()[0]
    if total == 0:
        return DiscoverySummary(
            total_products=0,
            categorized_count=0,
            uncategorized_count=0,
            inconsistent_count=0,
            categories={},
            valid_category_ids=set(),
            name_to_id={},
            uncategorized_product_ids=[],
            inconsistent_product_ids=[],
        )

    rows = con.execute(
        """
        SELECT olsera_product_id, name, category_id, category_name, klasifikasi
        FROM products_current
        ORDER BY olsera_product_id
        """
    ).fetchall()

    raw_counts: Dict[Tuple[Optional[int], Optional[str]], int] = {}
    id_samples: Dict[int, List[str]] = {}
    id_to_names: Dict[int, Set[str]] = {}
    uncategorized_ids: List[int] = []
    inconsistent_ids: List[int] = []

    for pid, name, cat_id, cat_name, _ in rows:
        clean_name = (cat_name or "").strip()
        is_empty_cat = cat_id is None or cat_id == 0 or not clean_name

        if is_empty_cat:
            uncategorized_ids.append(pid)
            continue

        raw_counts[(cat_id, clean_name)] = raw_counts.get((cat_id, clean_name), 0) + 1

        if cat_id not in id_to_names:
            id_to_names[cat_id] = set()
            id_samples[cat_id] = []
        id_to_names[cat_id].add(clean_name)
        if len(id_samples[cat_id]) < 5 and name:
            id_samples[cat_id].append(name)

    # Check for category_id inconsistency (single ID associated with multiple disparate names)
    for cat_id, names in id_to_names.items():
        if len(names) > 1:
            for pid, _, p_cat_id, _, _ in rows:
                if p_cat_id == cat_id:
                    inconsistent_ids.append(pid)

    # Aggregate canonical category entries by category_id
    categories: Dict[int, CategoryMeta] = {}
    name_to_id: Dict[str, int] = {}
    valid_ids: Set[int] = set()

    for cat_id, names in id_to_names.items():
        # Pick most frequent name as canonical
        best_name = max(names, key=lambda n: raw_counts.get((cat_id, n), 0))
        cat_count = sum(raw_counts.get((cat_id, n), 0) for n in names)
        pct = (cat_count / total) * 100.0 if total > 0 else 0.0
        is_catch = best_name.lower().strip() in CATCH_ALL_NAMES
        is_low = cat_count < 5

        meta = CategoryMeta(
            category_id=cat_id,
            name=best_name,
            product_count=cat_count,
            percentage=round(pct, 2),
            is_catch_all=is_catch,
            is_low_sample=is_low,
            sample_product_names=id_samples.get(cat_id, []),
        )
        categories[cat_id] = meta
        name_to_id[best_name.upper()] = cat_id
        valid_ids.add(cat_id)

    categorized_count = sum(m.product_count for m in categories.values())

    return DiscoverySummary(
        total_products=total,
        categorized_count=categorized_count,
        uncategorized_count=len(uncategorized_ids),
        inconsistent_count=len(inconsistent_ids),
        categories=categories,
        valid_category_ids=valid_ids,
        name_to_id=name_to_id,
        uncategorized_product_ids=uncategorized_ids,
        inconsistent_product_ids=inconsistent_ids,
    )
