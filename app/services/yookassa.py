from __future__ import annotations

import uuid
from typing import Any

import httpx

from app.config import Settings

YOOKASSA_API = "https://api.yookassa.ru/v3"


class YooKassaError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class YooKassaClient:
    """Асинхронный клиент ЮKassa (httpx). Идемпотентность через Idempotence-Key."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> YooKassaClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("YooKassaClient не инициализирован")
        return self._client

    def _auth(self) -> tuple[str, str]:
        return self.settings.yookassa_shop_id, self.settings.yookassa_secret_key

    async def create_payment(
        self,
        *,
        amount_value: str,
        description: str,
        return_url: str,
        metadata: dict[str, str],
        customer_email: str,
        idempotence_key: str | None = None,
    ) -> dict[str, Any]:
        key = idempotence_key or str(uuid.uuid4())
        body: dict[str, Any] = {
            "amount": {"value": amount_value, "currency": "RUB"},
            "capture": self.settings.yookassa_capture,
            "confirmation": {"type": "redirect", "return_url": return_url},
            "description": description[:128],
            "metadata": metadata,
        }
        # Чек 54-ФЗ — если в кабинете включена фискализация
        if self.settings.yookassa_vat_code is not None:
            item: dict[str, Any] = {
                "description": description[:128],
                "quantity": "1.00",
                "amount": {"value": amount_value, "currency": "RUB"},
                "vat_code": self.settings.yookassa_vat_code,
                "payment_mode": "full_payment",
                "payment_subject": "service",
            }
            receipt: dict[str, Any] = {
                "customer": {"email": customer_email},
                "items": [item],
            }
            if self.settings.yookassa_tax_system_code is not None:
                receipt["tax_system_code"] = self.settings.yookassa_tax_system_code
            body["receipt"] = receipt

        return await self._request("POST", "/payments", json=body, idempotence_key=key)

    async def get_payment(self, payment_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/payments/{payment_id}")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        idempotence_key: str | None = None,
    ) -> dict[str, Any]:
        if not self.settings.is_yookassa_configured:
            raise YooKassaError("ЮKassa не настроена: укажите YOOKASSA_SHOP_ID и YOOKASSA_SECRET_KEY")

        headers = {"Content-Type": "application/json"}
        if idempotence_key:
            headers["Idempotence-Key"] = idempotence_key

        response = await self.client.request(
            method,
            f"{YOOKASSA_API}{path}",
            auth=self._auth(),
            headers=headers,
            json=json,
        )
        try:
            data = response.json()
        except Exception:
            data = {"raw": response.text}

        if response.status_code >= 400:
            raise YooKassaError(
                f"ЮKassa ошибка {response.status_code}: {data}",
                status_code=response.status_code,
                payload=data,
            )
        return data


# IP-сети ЮKassa для входящих уведомлений (документация)
YOOKASSA_WEBHOOK_NETWORKS = (
    "185.71.76.0/27",
    "185.71.77.0/27",
    "77.75.153.0/25",
    "77.75.156.11",
    "77.75.154.128/25",
    "2a02:5180::/32",
)