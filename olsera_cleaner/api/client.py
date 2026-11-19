
"""Olsera API client: reads, authentication and single-product test update."""

import json
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class OlseraApiError(RuntimeError):
    pass


class OlseraUpdateRejectedError(OlseraApiError):
    """The API definitely rejected the update request before applying it."""


class OlseraUpdateUncertainError(OlseraApiError):
    """A POST may have reached Olsera, but its final result is not known."""


class OlseraClient:
    def __init__(self, settings, session=None):
        self.settings = settings
        self.session = session or requests.Session()

        if session is None:
            retry = Retry(
                total=3,
                connect=3,
                read=2,
                backoff_factor=0.7,
                status_forcelist=(500, 502, 503, 504),
                allowed_methods=frozenset(["GET"]),
                respect_retry_after_header=True,
            )
            self.session.mount(
                "https://",
                HTTPAdapter(max_retries=retry),
            )

        self._access_token = None
        self._refresh_token = None
        self._expire_at = 0.0

        self._auth_lock = threading.Lock()
        self._request_lock = threading.Lock()
        self._next_request_at = 0.0
        self._min_interval_seconds = 3.0

    def _post_token(self, params):
        form = {
            key: (None, value)
            for key, value in params.items()
        }

        response = self.session.post(
            self.settings.base_url + "/id/token",
            headers={"Accept": "application/json"},
            files=form,
            timeout=self.settings.timeout,
        )

        if not response.ok:
            raise OlseraApiError(
                f"Token request gagal: HTTP {response.status_code}. "
                "Periksa APP_ID, SECRET_KEY, izin Open API, "
                "dan status kredensial."
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise OlseraApiError(
                "Respons token bukan JSON"
            ) from exc

        if (
            not isinstance(payload, dict)
            or not payload.get("access_token")
        ):
            raise OlseraApiError(
                "Respons token tidak memiliki access_token"
            )

        self._access_token = payload["access_token"]
        self._refresh_token = (
            payload.get("refresh_token")
            or self._refresh_token
        )

        try:
            seconds = max(
                30,
                float(payload.get("expires_in", 3600)),
            )
        except (TypeError, ValueError):
            seconds = 300

        self._expire_at = (
            time.monotonic() + max(0, seconds - 60)
        )

    def _ensure_auth(self, force=False):
        with self._auth_lock:
            if (
                not force
                and self._access_token
                and time.monotonic() < self._expire_at
            ):
                return

            if self._refresh_token:
                try:
                    self._post_token({
                        "refresh_token": self._refresh_token,
                        "grant_type": "refresh_token",
                    })
                    return
                except (
                    requests.RequestException,
                    OlseraApiError,
                ):
                    pass

            if (
                not self.settings.app_id
                or not self.settings.secret_key
            ):
                raise OlseraApiError(
                    "OLSERA_APP_ID dan OLSERA_SECRET_KEY "
                    "belum diisi"
                )

            self._post_token({
                "app_id": self.settings.app_id,
                "secret_key": self.settings.secret_key,
                "grant_type": "secret_key",
            })

    def _pace_request(self):
        with self._request_lock:
            delay = max(
                0.0,
                self._next_request_at - time.monotonic(),
            )

            if delay:
                time.sleep(delay)

            self._next_request_at = (
                time.monotonic()
                + self._min_interval_seconds
            )

    @staticmethod
    def _retry_after_seconds(value):
        if not value:
            return None

        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            try:
                until = parsedate_to_datetime(value)

                if until.tzinfo is None:
                    until = until.replace(
                        tzinfo=timezone.utc
                    )

                return max(
                    0.0,
                    (
                        until - datetime.now(timezone.utc)
                    ).total_seconds(),
                )
            except (
                TypeError,
                ValueError,
                OverflowError,
            ):
                return None

    def get(self, path, params=None):
        if (
            not path.startswith("/")
            or path.startswith("//")
            or "://" in path
        ):
            raise ValueError("Path API tidak valid")

        self._ensure_auth()

        refresh_attempts = 0
        rate_limit_attempts = 0

        while True:
            self._pace_request()

            token = self._access_token
            authorization = (
                token
                if token.lower().startswith("bearer ")
                else "Bearer " + token
            )

            response = self.session.get(
                self.settings.base_url + path,
                params=params,
                headers={
                    "Accept": "application/json",
                    "Authorization": authorization,
                },
                timeout=self.settings.timeout,
            )

            if (
                response.status_code == 401
                and refresh_attempts == 0
            ):
                refresh_attempts += 1
                self._ensure_auth(force=True)
                continue

            if response.status_code == 429:
                if rate_limit_attempts >= 5:
                    raise OlseraApiError(
                        f"GET {path}: HTTP 429 terus berulang. "
                        "Batas API mungkin berbasis kuota."
                    )

                indicated = self._retry_after_seconds(
                    response.headers.get("Retry-After")
                )

                wait = (
                    indicated
                    if indicated is not None
                    else min(
                        180.0,
                        15.0 * (2 ** rate_limit_attempts),
                    )
                )

                time.sleep(
                    max(1.0, min(wait, 600.0))
                )

                rate_limit_attempts += 1
                continue

            if not response.ok:
                raise OlseraApiError(
                    f"GET {path} gagal: "
                    f"HTTP {response.status_code}"
                )

            try:
                payload = response.json()
            except ValueError as exc:
                raise OlseraApiError(
                    f"GET {path}: respons bukan JSON"
                ) from exc

            if (
                not isinstance(payload, dict)
                or payload.get("error")
                not in (None, 0, "0")
            ):
                raise OlseraApiError(
                    f"GET {path}: bentuk respons "
                    "tidak dikenali atau API melaporkan error"
                )

            return payload

    def product_list(self, page=1, per_page=50):
        return self.get(
            "/en/product",
            {
                "page": page,
                "per_page": per_page,
            },
        )

    def product_detail(self, product_id):
        return self.get(
            "/en/product/detail",
            {"id": int(product_id)},
        )

    def collections(self, page=1, per_page=50):
        return self.get(
            "/en/collection",
            {
                "page": page,
                "per_page": per_page,
            },
        )

    def product_groups(self, page=1, per_page=50):
        return self.get(
            "/en/productgroup",
            {
                "page": page,
                "per_page": per_page,
            },
        )

    def update_product_name(
        self,
        product_id,
        new_name,
        sku=None,
        store_id=None,
    ):
        """Update one name using the tested multipart/form-data request.

        POST is deliberately never retried. A rejected response is distinct
        from a transport/server failure, because the latter must be reconciled
        with a GET instead of being assumed to have failed.
        """
        # Hard lock: Cloud environments are strictly read-only. No live mutations permitted.
        if getattr(self.settings, "is_cloud", False):
            raise PermissionError(
                "MUTASI DITOLAK: API client memblokir seluruh mutasi LIVE pada lingkungan cloud staging (Strict Read-Only)."
            )

        self._ensure_auth()

        name = str(new_name).strip()
        if not name or len(name) > 250:
            raise ValueError("Nama produk tidak valid")

        # Read the current SKU and store from Olsera immediately before POST.
        # The optional values are supplied by the controlled-update preflight
        # and guard against a changed product between its two reads.
        detail = self.product_detail(product_id)
        product = detail.get("data", {})
        if isinstance(product, dict) and isinstance(product.get("product"), dict):
            product = product["product"]
        if not isinstance(product, dict):
            raise OlseraApiError("Struktur detail produk tidak dikenali")

        current_sku = product.get("sku")
        current_store_id = product.get("store_id")
        if sku is not None and str(sku) != str(current_sku):
            raise OlseraApiError("SKU berbeda dari data terbaru Olsera")
        if store_id is not None and str(store_id) != str(current_store_id):
            raise OlseraApiError("Store ID berbeda dari data terbaru Olsera")
        if not current_sku or not current_store_id:
            raise OlseraApiError("SKU atau Store ID tidak tersedia")

        token = str(self._access_token)
        authorization = (
            token if token.lower().startswith("bearer ") else "Bearer " + token
        )
        form_data = {
            "to_allstore_id": (None, "0"),
            "to_store_id": (None, str(current_store_id)),
            "product[name]": (None, name),
            "product[sku]": (None, str(current_sku)),
        }

        try:
            response = self.session.post(
                self.settings.base_url + "/en/product/update",
                params={"id": int(product_id)},
                headers={
                    "Accept": "application/json",
                    "Authorization": authorization,
                },
                files=form_data,
                timeout=self.settings.timeout,
            )
        except requests.RequestException as exc:
            raise OlseraUpdateUncertainError(
                "POST update tidak mendapatkan respons; status perubahan belum pasti"
            ) from exc

        if not response.ok:
            error_type = (
                OlseraUpdateRejectedError
                if 400 <= response.status_code < 500
                else OlseraUpdateUncertainError
            )
            certainty = (
                "ditolak oleh API"
                if error_type is OlseraUpdateRejectedError
                else "belum pasti diterapkan"
            )
            raise error_type(
                f"Update produk: HTTP {response.status_code}; {certainty}."
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise OlseraUpdateUncertainError(
                "Respons update bukan JSON; status perubahan belum pasti."
            ) from exc

        if not isinstance(payload, dict):
            raise OlseraUpdateUncertainError(
                "Respons update bukan objek JSON; status perubahan belum pasti."
            )
        if payload.get("error") not in (None, 0, "0"):
            raise OlseraUpdateRejectedError(
                "API melaporkan error pada update produk."
            )

        # A success response is not final proof. updates.py always GETs the
        # product afterwards and records its before/after snapshots.
        return payload
