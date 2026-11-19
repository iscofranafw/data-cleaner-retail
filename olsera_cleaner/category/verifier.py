"""Layer C: Ollama AI verification for ambiguous category recommendations with SQLite caching."""

import hashlib
import json
import re
from typing import Any, Dict, List, Optional
import requests

_ALLOWED_MODEL = re.compile(r"^[A-Za-z0-9_.:\-]{1,120}$")


class CategoryAIVerifier:
    """Verifies ambiguous category assignments using local Ollama LLM with bias guardrails and telemetry."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        model: str = "llama3:8b",
        timeout: int = 30,
    ):
        clean_url = base_url.rstrip("/")
        if clean_url not in ("http://127.0.0.1:11434", "http://localhost:11434"):
            raise ValueError("Ollama hanya boleh diakses melalui localhost")
        if not _ALLOWED_MODEL.fullmatch(model):
            raise ValueError(f"Nama model Ollama '{model}' tidak valid")
        self.base_url = clean_url
        self.model = model
        self.timeout = timeout

        # Telemetry metrics
        self.total_queries = 0
        self.cache_hits = 0
        self.cache_misses = 0
        self.agreed_with_stat = 0
        self.overruled_stat = 0
        self.fallback_count = 0

    def get_telemetry(self) -> Dict[str, Any]:
        """Return runtime AI audit metrics."""
        return {
            "total_queries": self.total_queries,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "agreed_with_statistical": self.agreed_with_stat,
            "overruled_statistical": self.overruled_stat,
            "fallback_count": self.fallback_count,
            "model": self.model,
        }

    @staticmethod
    def _compute_hash(product_dict: Dict[str, Any], valid_categories: List[Dict[str, Any]]) -> str:
        """Create fingerprint for caching AI category responses."""
        payload = {
            "name": product_dict.get("name"),
            "klasifikasi": product_dict.get("klasifikasi"),
            "brand": product_dict.get("brand_name"),
            "uom": product_dict.get("uom"),
            "valid_cats": sorted([c["id"] for c in valid_categories]),
            "prompt_version": "v2_antislop_unbiased",
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    def verify_one(
        self,
        con,
        product: Dict[str, Any],
        valid_categories: List[Dict[str, Any]],
        layer_b_result: Optional[Dict[str, Any]] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        """Verify an individual product category against valid categories with anti-bias protection."""
        self.total_queries += 1
        pid = int(product["olsera_product_id"])
        source_hash = self._compute_hash(product, valid_categories)

        # Check SQLite cache first if table exists
        if not force:
            try:
                cached = con.execute(
                    """
                    SELECT suggested_category_id, suggested_category_name, confidence, reasoning, status
                    FROM cleaner_category_ai_cache
                    WHERE product_id = ? AND source_hash = ? AND model = ?
                    """,
                    (pid, source_hash, self.model),
                ).fetchone()
                if cached and cached[4] == "COMPLETED":
                    self.cache_hits += 1
                    sug_id = cached[0]
                    if layer_b_result:
                        stat_id = layer_b_result.get("predicted_category_id")
                        if stat_id and sug_id == stat_id:
                            self.agreed_with_stat += 1
                        else:
                            self.overruled_stat += 1
                    return {
                        "suggested_category_id": cached[0],
                        "suggested_category_name": cached[1],
                        "confidence": cached[2],
                        "reasoning": cached[3],
                        "status": "CACHED",
                    }
            except (sqlite3.OperationalError, sqlite3.DatabaseError):
                # SQLite cache table might not exist in uninitialized test connections
                pass

        self.cache_misses += 1
        valid_map = {c["id"]: c["name"] for c in valid_categories}
        cat_choices = "\n".join([f"- ID {c['id']}: {c['name']}" for c in valid_categories])

        context_info = {
            "product_name": product.get("name"),
            "current_category": product.get("category_name"),
            "klasifikasi": product.get("klasifikasi"),
            "brand": product.get("brand_name"),
            "sku": product.get("sku"),
            "barcode": product.get("barcode"),
        }

        # Clear anti-bias instructions: do not anchor on dirty historical data
        prompt = (
            "Anda adalah auditor taksonomi retail POS yang sangat teliti dan objektif.\n"
            "Tugas: Tentukan kategori yang BENAR-BENAR TEPAT untuk produk ini HANYA dari daftar kategori valid berikut:\n"
            f"{cat_choices}\n\n"
            f"Informasi Produk:\n{json.dumps(context_info, ensure_ascii=False, indent=2)}\n\n"
            "Pedoman Penilaian Kritis:\n"
            "1. Nilai 'suggested_category_id' WAJIB merupakan salah satu ID kategori valid di atas.\n"
            "2. Jangan bias oleh kemiripan kata acak (misal sabun wangi strawberry/susu adalah sabun mandi, BUKAN makanan).\n"
            "3. PENTING: Sabun cuci piring (Sunlight, Mama Lemon), deterjen, pembersih lantai, sabun mandi, sampo, "
            "pasta gigi, kosmetik, obat-obatan, perlengkapan mandi, pembalut, pampers, rokok, dan peralatan dapur/rumah "
            "adalah barang NON-MAKANAN. Jika pilihan kategori yang tersedia hanya 'Food' dan 'Others', barang non-makanan "
            "tersebut WAJIB tetap dikategorikan ke 'Others' (JANGAN dimasukkan ke 'Food').\n"
            "4. Produk yang tergolong makanan, minuman, biskuit, chiki, mie instant, bumbu masak, beras, minyak goreng, "
            "atau roti barulah dikategorikan ke 'Food'.\n"
            "5. Jika ragu atau tidak yakin, pilih kategori saat ini produk ('Others').\n\n"
            "Keluaran WAJIB format JSON murni tanpa markdown tambahan:\n"
            "{\n"
            '  "suggested_category_id": <int>,\n'
            '  "suggested_category_name": "<string>",\n'
            '  "confidence": "HIGH" | "MEDIUM" | "LOW",\n'
            '  "reasoning": "<penjelasan ringkas maksimal 150 karakter>"\n'
            "}"
        )

        try:
            r = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0.0},
                },
                timeout=self.timeout,
            )
            r.raise_for_status()
            raw_text = r.json().get("response", "")
            data = json.loads(raw_text)

            sug_id = int(data.get("suggested_category_id"))
            conf = str(data.get("confidence", "LOW")).upper()
            if conf not in ("HIGH", "MEDIUM", "LOW"):
                conf = "LOW"
            reason = str(data.get("reasoning", "")).strip()[:500]

            # Validate that returned category ID is strictly within valid categories
            if sug_id not in valid_map:
                raise ValueError(f"AI menghasilkan category_id {sug_id} yang tidak ada di daftar valid")

            sug_name = valid_map[sug_id]

            # Cache the successful result in SQLite if writable
            try:
                con.execute(
                    """
                    INSERT INTO cleaner_category_ai_cache
                        (product_id, source_hash, model, suggested_category_id, suggested_category_name, confidence, reasoning, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'COMPLETED')
                    ON CONFLICT(product_id, source_hash, model) DO UPDATE SET
                        suggested_category_id = excluded.suggested_category_id,
                        suggested_category_name = excluded.suggested_category_name,
                        confidence = excluded.confidence,
                        reasoning = excluded.reasoning,
                        status = 'COMPLETED'
                    """,
                    (pid, source_hash, self.model, sug_id, sug_name, conf, reason),
                )
            except (sqlite3.OperationalError, sqlite3.DatabaseError):
                # SQLite cache table might not exist in uninitialized test connections
                pass

            if layer_b_result:
                stat_id = layer_b_result.get("predicted_category_id")
                if stat_id and sug_id == stat_id:
                    self.agreed_with_stat += 1
                else:
                    self.overruled_stat += 1

            return {
                "suggested_category_id": sug_id,
                "suggested_category_name": sug_name,
                "confidence": conf,
                "reasoning": reason,
                "status": "COMPLETED",
            }

        except Exception as exc:
            self.fallback_count += 1
            return {
                "suggested_category_id": None,
                "suggested_category_name": None,
                "confidence": "LOW",
                "reasoning": f"Verifikasi AI tidak tersedia ({type(exc).__name__}), menggunakan metode deterministik.",
                "status": "FALLBACK",
            }
