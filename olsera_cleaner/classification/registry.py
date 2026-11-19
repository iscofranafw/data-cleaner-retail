"""Dynamic Taxonomy Registry & Versioning for Olsera Classifications."""

import hashlib
import json
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Set

DEFAULT_GENERIC_NAMES = {"lainlain", "lain-lain", "lainnya", "closing", "kebutuhan", "umum", "unclassified"}


def normalize_taxonomy_name(name: str) -> str:
    """Normalize classification name by trimming, lowercasing, and compressing spaces."""
    cleaned = re.sub(r"[^\w\s]", " ", (name or "").lower())
    return " ".join(cleaned.split())


@dataclass
class DynamicKlasifikasi:
    klasifikasi_id: int
    name: str
    normalized_name: str
    product_group_id: int
    product_count: int
    percentage: float
    is_catch_all: bool = False
    is_low_sample: bool = False
    sample_product_names: List[str] = field(default_factory=list)


@dataclass
class TaxonomyDiff:
    added_ids: Set[int] = field(default_factory=set)
    removed_ids: Set[int] = field(default_factory=set)
    renamed: Dict[int, Dict[str, str]] = field(default_factory=dict)
    distribution_shift_score: float = 0.0
    has_changes: bool = False


@dataclass
class TaxonomySnapshot:
    version_hash: str
    total_products: int
    classifications_count: int
    klasifikasi_map: Dict[int, DynamicKlasifikasi]
    name_to_id: Dict[str, int]
    norm_to_ids: Dict[str, List[int]]
    valid_ids: Set[int]
    catch_all_ids: Set[int]
    low_sample_ids: Set[int]
    product_group_mismatch_count: int

    @property
    def klasifikasi_count(self) -> int:
        return self.classifications_count


class TaxonomyRegistry:
    """Discovers, tracks, and versions Olsera classification taxonomy dynamically."""

    def __init__(self, generic_names: Optional[Set[str]] = None):
        self.generic_names = generic_names or DEFAULT_GENERIC_NAMES
        self.active_snapshot: Optional[TaxonomySnapshot] = None

    def discover_live_taxonomy(self, con) -> TaxonomySnapshot:
        """Read all active classifications dynamically from SQLite products_current."""
        total_row = con.execute("SELECT COUNT(*) FROM products_current").fetchone()
        total = total_row[0] if total_row else 0

        if total == 0:
            empty_hash = hashlib.sha256(b"empty_taxonomy").hexdigest()[:12]
            snapshot = TaxonomySnapshot(
                version_hash=empty_hash,
                total_products=0,
                classifications_count=0,
                klasifikasi_map={},
                name_to_id={},
                norm_to_ids={},
                valid_ids=set(),
                catch_all_ids=set(),
                low_sample_ids=set(),
                product_group_mismatch_count=0,
            )
            self.active_snapshot = snapshot
            return snapshot

        rows = con.execute(
            """
            SELECT klasifikasi_id, klasifikasi, product_group_id, COUNT(*)
            FROM products_current
            WHERE klasifikasi_id IS NOT NULL AND klasifikasi IS NOT NULL
            GROUP BY klasifikasi_id, klasifikasi, product_group_id
            ORDER BY COUNT(*) DESC, klasifikasi_id ASC
            """
        ).fetchall()

        mismatch_row = con.execute(
            """
            SELECT COUNT(*) FROM products_current
            WHERE klasifikasi_id != product_group_id OR product_group_id IS NULL
            """
        ).fetchone()
        mismatches = mismatch_row[0] if mismatch_row else 0

        klasifikasi_map: Dict[int, DynamicKlasifikasi] = {}
        name_to_id: Dict[str, int] = {}
        norm_to_ids: Dict[str, List[int]] = {}
        valid_ids: Set[int] = set()
        catch_all_ids: Set[int] = set()
        low_sample_ids: Set[int] = set()

        for kid, raw_name, pg_id, count in rows:
            kid = int(kid)
            pg_id = int(pg_id) if pg_id is not None else kid
            name = str(raw_name).strip()
            norm_name = normalize_taxonomy_name(name)
            pct = (count / total) * 100.0 if total > 0 else 0.0

            is_catch = norm_name in self.generic_names or any(g in norm_name.split() for g in self.generic_names)
            is_low = count < 10

            sample_rows = con.execute(
                "SELECT name FROM products_current WHERE klasifikasi_id = ? LIMIT 5",
                (kid,),
            ).fetchall()
            samples = [r[0] for r in sample_rows]

            dk = DynamicKlasifikasi(
                klasifikasi_id=kid,
                name=name,
                normalized_name=norm_name,
                product_group_id=pg_id,
                product_count=count,
                percentage=round(pct, 2),
                is_catch_all=is_catch,
                is_low_sample=is_low,
                sample_product_names=samples,
            )
            klasifikasi_map[kid] = dk
            name_to_id[name.upper()] = kid
            norm_to_ids.setdefault(norm_name, []).append(kid)
            valid_ids.add(kid)
            if is_catch:
                catch_all_ids.add(kid)
            if is_low:
                low_sample_ids.add(kid)

        summary_payload = {
            "total_products": total,
            "classes": sorted([{"id": k, "name": v.name, "cnt": v.product_count} for k, v in klasifikasi_map.items()], key=lambda x: x["id"]),
        }
        version_hash = hashlib.sha256(json.dumps(summary_payload, sort_keys=True).encode("utf-8")).hexdigest()[:12]

        snapshot = TaxonomySnapshot(
            version_hash=version_hash,
            total_products=total,
            classifications_count=len(klasifikasi_map),
            klasifikasi_map=klasifikasi_map,
            name_to_id=name_to_id,
            norm_to_ids=norm_to_ids,
            valid_ids=valid_ids,
            catch_all_ids=catch_all_ids,
            low_sample_ids=low_sample_ids,
            product_group_mismatch_count=mismatches,
        )
        self.active_snapshot = snapshot
        return snapshot

    def persist_version(self, con, snapshot: TaxonomySnapshot) -> None:
        """Store versioned taxonomy state in cleaner_taxonomy_versions."""
        try:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS cleaner_taxonomy_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    version_hash TEXT NOT NULL UNIQUE,
                    classifications_count INTEGER NOT NULL,
                    products_count INTEGER NOT NULL,
                    taxonomy_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            payload_dict = {
                "version_hash": snapshot.version_hash,
                "total_products": snapshot.total_products,
                "classifications_count": snapshot.classifications_count,
                "classes": [asdict(v) for v in snapshot.klasifikasi_map.values()],
            }
            con.execute(
                """
                INSERT INTO cleaner_taxonomy_versions
                    (version_hash, classifications_count, products_count, taxonomy_json, created_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(version_hash) DO NOTHING
                """,
                (
                    snapshot.version_hash,
                    snapshot.classifications_count,
                    snapshot.total_products,
                    json.dumps(payload_dict),
                ),
            )
        except (sqlite3.OperationalError, sqlite3.DatabaseError):
            return

    def diff_snapshots(self, old_snap: TaxonomySnapshot, new_snap: TaxonomySnapshot) -> TaxonomyDiff:
        """Compare two taxonomy snapshots to detect new, deleted, or shifted classifications."""
        old_ids = old_snap.valid_ids
        new_ids = new_snap.valid_ids

        added = new_ids - old_ids
        removed = old_ids - new_ids

        renamed = {}
        for kid in old_ids & new_ids:
            old_name = old_snap.klasifikasi_map[kid].name
            new_name = new_snap.klasifikasi_map[kid].name
            if old_name != new_name:
                renamed[kid] = {"old": old_name, "new": new_name}

        # Calculate Total Variation Distance (TVD) of product distribution
        tvd = 0.0
        all_ids = old_ids | new_ids
        for kid in all_ids:
            p_old = (old_snap.klasifikasi_map[kid].product_count / old_snap.total_products) if kid in old_snap.klasifikasi_map and old_snap.total_products > 0 else 0.0
            p_new = (new_snap.klasifikasi_map[kid].product_count / new_snap.total_products) if kid in new_snap.klasifikasi_map and new_snap.total_products > 0 else 0.0
            tvd += abs(p_old - p_new)
        tvd = round(0.5 * tvd, 4)

        has_changes = bool(added or removed or renamed or tvd > 0.05)
        return TaxonomyDiff(
            added_ids=added,
            removed_ids=removed,
            renamed=renamed,
            distribution_shift_score=tvd,
            has_changes=has_changes,
        )
