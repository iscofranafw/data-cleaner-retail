"""Layer C: Ollama AI verification for ambiguous product classifications with SQLite caching."""

import hashlib
import json
import re
import sqlite3
from typing import Any, Dict, List
import requests

_ALLOWED_MODEL = re.compile(r"^[A-Za-z0-9_.:\-]{1,120}$")


class KlasifikasiAIVerifier:
    """Verifies ambiguous classification assignments using local Ollama LLM."""

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

        self.total_queries = 0
        self.cache_hits = 0
        self.cache_misses = 0
        self.fallback_count = 0

    def get_telemetry(self) -> Dict[str, Any]:
        """Return runtime AI audit metrics."""
        return {
            "total_queries": self.total_queries,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "fallback_count": self.fallback_count,
            "model": self.model,
        }

    @staticmethod
    def _compute_hash(product_dict: Dict[str, Any], candidate_classes: List[Dict[str, Any]]) -> str:
        """Create fingerprint for caching AI classification responses."""
        payload = {
            "name": product_dict.get("name"),
            "current_klasifikasi": product_dict.get("klasifikasi"),
            "brand": product_dict.get("brand_name"),
            "uom": product_dict.get("uom"),
            "candidates": sorted([c["id"] for c in candidate_classes]),
            "prompt_version": "v1_multiclass_audit",
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    def _get_cached_result(self, con, pid: int, source_hash: str) -> Dict[str, Any]:
        """Read previous verification outcome from SQLite cache if available."""
        try:
            cached = con.execute(
                """
                SELECT suggested_klasifikasi_id, suggested_klasifikasi, confidence, reasoning, status
                FROM cleaner_classification_ai_cache
                WHERE product_id = ? AND source_hash = ? AND model = ?
                """,
                (pid, source_hash, self.model),
            ).fetchone()
            if cached and cached[4] == "COMPLETED":
                return {
                    "suggested_klasifikasi_id": cached[0],
                    "suggested_klasifikasi": cached[1],
                    "confidence": cached[2],
                    "reasoning": cached[3],
                    "status": "CACHED",
                }
        except (sqlite3.OperationalError, sqlite3.DatabaseError):
            return {}
        return {}

    def _persist_cache(
        self,
        con,
        pid: int,
        source_hash: str,
        result_data: Dict[str, Any],
    ) -> None:
        """Save verification outcome to SQLite cache."""
        try:
            con.execute(
                """
                INSERT INTO cleaner_classification_ai_cache
                    (product_id, source_hash, model, suggested_klasifikasi_id, suggested_klasifikasi, confidence, reasoning, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'COMPLETED')
                ON CONFLICT(product_id, source_hash, model) DO UPDATE SET
                    suggested_klasifikasi_id = excluded.suggested_klasifikasi_id,
                    suggested_klasifikasi = excluded.suggested_klasifikasi,
                    confidence = excluded.confidence,
                    reasoning = excluded.reasoning,
                    status = 'COMPLETED'
                """,
                (
                    pid,
                    source_hash,
                    self.model,
                    result_data["suggested_klasifikasi_id"],
                    result_data["suggested_klasifikasi"],
                    result_data["confidence"],
                    result_data["reasoning"],
                ),
            )
        except (sqlite3.OperationalError, sqlite3.DatabaseError):
            return

    def _build_prompt(self, product: Dict[str, Any], candidate_classes: List[Dict[str, Any]]) -> str:
        """Build strict JSON verification prompt with anti-bias negative constraints."""
        choices_text = "\n".join([f"- ID {c['id']}: {c['name']}" for c in candidate_classes])
        context_info = {
            "product_name": product.get("name"),
            "current_klasifikasi": product.get("klasifikasi"),
            "brand": product.get("brand_name"),
            "uom": product.get("uom"),
            "sku": product.get("sku"),
            "barcode": product.get("barcode"),
        }
        return (
            "Anda adalah auditor taksonomi retail POS ahli.\n"
            "Tugas: Tentukan klasifikasi yang paling akurat dan tepat untuk produk berikut HANYA dari opsi valid berikut:\n"
            f"{choices_text}\n\n"
            f"Data Produk:\n{json.dumps(context_info, ensure_ascii=False, indent=2)}\n\n"
            "Pedoman Penilaian:\n"
            "1. Nilai 'suggested_klasifikasi_id' WAJIB merupakan salah satu ID dari daftar opsi di atas.\n"
            "2. Bedakan dengan cermat klasifikasi spesifik (contoh: sabun cuci piring vs deterjen vs sabun mandi).\n"
            "3. Jika bukti tidak cukup kuat untuk mengubah klasifikasi, pertahankan klasifikasi saat ini.\n"
            "4. Berikan alasan ringkas dalam bahasa Indonesia.\n\n"
            "Keluaran WAJIB format JSON murni tanpa markdown tambahan:\n"
            "{\n"
            '  "suggested_klasifikasi_id": <int>,\n'
            '  "suggested_klasifikasi": "<string>",\n'
            '  "confidence": "HIGH" | "MEDIUM" | "LOW",\n'
            '  "reasoning": "<penjelasan ringkas maksimal 150 karakter>"\n'
            "}"
        )

    def verify_one(
        self,
        con,
        product: Dict[str, Any],
        candidate_classes: List[Dict[str, Any]],
        force: bool = False,
    ) -> Dict[str, Any]:
        """Verify an individual product classification against candidate classes."""
        self.total_queries += 1
        pid = int(product["olsera_product_id"])
        source_hash = self._compute_hash(product, candidate_classes)

        if not force:
            cached = self._get_cached_result(con, pid, source_hash)
            if cached:
                self.cache_hits += 1
                return cached

        self.cache_misses += 1
        valid_map = {c["id"]: c["name"] for c in candidate_classes}
        prompt = self._build_prompt(product, candidate_classes)

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
            data = json.loads(r.json().get("response", ""))

            sug_id = int(data.get("suggested_klasifikasi_id"))
            conf = str(data.get("confidence", "LOW")).upper()
            if conf not in ("HIGH", "MEDIUM", "LOW"):
                conf = "LOW"
            reason = str(data.get("reasoning", "")).strip()[:500]

            if sug_id not in valid_map:
                raise ValueError(f"AI menghasilkan ID {sug_id} yang tidak ada di daftar kandidat valid")

            sug_name = valid_map[sug_id]
            res_dict = {
                "suggested_klasifikasi_id": sug_id,
                "suggested_klasifikasi": sug_name,
                "confidence": conf,
                "reasoning": reason,
                "status": "COMPLETED",
            }
            self._persist_cache(con, pid, source_hash, res_dict)
            return res_dict

        except Exception as exc:
            self.fallback_count += 1
            return {
                "suggested_klasifikasi_id": None,
                "suggested_klasifikasi": None,
                "confidence": "LOW",
                "reasoning": f"Verifikasi AI tidak tersedia ({type(exc).__name__}), menggunakan metode deterministik.",
                "status": "FALLBACK",
            }
