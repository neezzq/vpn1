from __future__ import annotations

import httpx
from typing import Any, Mapping


class PlategaError(RuntimeError):
    pass


class PlategaClient:
    def __init__(self, merchant_id: str, secret: str, base_url: str = "https://app.platega.io"):
        self.merchant_id = merchant_id
        self.secret = secret
        self.base_url = (base_url or "https://app.platega.io").rstrip("/")

    @property
    def headers(self) -> dict[str, str]:
        return {
            "X-MerchantId": self.merchant_id,
            "X-Secret": self.secret,
            "Content-Type": "application/json",
        }

    async def create_transaction(
        self,
        amount_rub: int,
        description: str,
        payload: str,
        return_url: str,
        failed_url: str,
        payment_method: int = 2,
        currency: str = "RUB",
    ) -> dict[str, Any]:
        data = {
            "paymentMethod": payment_method,
            "paymentDetails": {
                "amount": int(amount_rub),
                "currency": currency,
            },
            "description": description,
            "return": return_url,
            "failedUrl": failed_url,
            "payload": payload,
        }
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(f"{self.base_url}/transaction/process", headers=self.headers, json=data)
            r.raise_for_status()
            body = r.json()
        if not isinstance(body, dict) or not body.get("transactionId"):
            raise PlategaError(f"Unexpected create transaction response: {body}")
        return body

    async def get_transaction(self, transaction_id: str) -> dict[str, Any]:
        transaction_id = str(transaction_id).strip()
        if not transaction_id:
            raise PlategaError("transaction_id is required")
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(f"{self.base_url}/transaction/{transaction_id}", headers=self.headers)
            r.raise_for_status()
            body = r.json()
        if not isinstance(body, dict):
            raise PlategaError(f"Unexpected transaction response: {body}")
        return body


def verify_callback_headers(expected_merchant_id: str, expected_secret: str, headers: Mapping[str, str]) -> bool:
    merchant_id = str(headers.get("X-MerchantId") or headers.get("x-merchantid") or "").strip()
    secret = str(headers.get("X-Secret") or headers.get("x-secret") or "").strip()
    return bool(merchant_id) and bool(secret) and merchant_id == expected_merchant_id and secret == expected_secret
