"""Strictly validates observed list response (data, links, meta, status, error)."""
from dataclasses import dataclass

class ResponseFormatError(ValueError):
    pass

@dataclass(frozen=True)
class ProductPage:
    items: list
    page: int
    last_page: int
    total: int
    per_page: int

def parse_page(payload, requested_page):
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ResponseFormatError("'data' bukan daftar")
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        raise ResponseFormatError("'meta' tidak tersedia")
    try:
        current = int(meta["current_page"])
        last = int(meta["last_page"])
        total = int(meta["total"])
        size = int(meta["per_page"])
    except (KeyError, TypeError, ValueError) as e:
        raise ResponseFormatError("Metadata pagination tidak valid") from e
    if current != requested_page or current < 1 or last < 1 or size < 1 or total < 0:
        raise ResponseFormatError("Metadata pagination tidak konsisten")
    if current > last or len(payload["data"]) > size:
        raise ResponseFormatError("Halaman atau panjang data tidak valid")
    if current < last and len(payload["data"]) == 0:
        raise ResponseFormatError("Halaman kosong sebelum halaman terakhir")
    for item in payload["data"]:
        if not isinstance(item, dict) or item.get("id") is None or not str(item["id"]).isdigit():
            raise ResponseFormatError("Ada produk tanpa ID valid")
    return ProductPage(payload["data"], current, last, total, size)
