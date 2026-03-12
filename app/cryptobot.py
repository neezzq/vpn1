from __future__ import annotations

import hashlib
import hmac
import httpx
import json
from typing import Any


class CryptoBotError(RuntimeError):
    pass


class CryptoBotClient:
    def __init__(self, token: str, base_url: str = "https://pay.crypt.bot/api"):
        self.token = token
        normalized = (base_url or "https://pay.crypt.bot/api").rstrip("/")
        if not normalized.endswith("/api"):
            normalized = f"{normalized}/api"
        self.base_url = normalized

    @property
    def headers(self) -> dict[str, str]:
        return {"Crypto-Pay-API-Token": self.token}

    async def create_invoice(
        self,
        amount_rub: int,
        payload: dict[str, Any],
        description: str,
        accepted_assets: str = "USDT,TON,BTC,ETH,LTC",
        expires_in: int = 1800,
    ) -> dict[str, Any]:
        data = {
            "currency_type": "fiat",
            "fiat": "RUB",
            "amount": str(amount_rub),
            "accepted_assets": accepted_assets,
            "description": description,
            "payload": json.dumps(payload, ensure_ascii=False),
            "expires_in": expires_in,
            "allow_comments": False,
            "allow_anonymous": True,
        }
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(f"{self.base_url}/createInvoice", headers=self.headers, json=data)
            r.raise_for_status()
            body = r.json()
        if not body.get("ok"):
            raise CryptoBotError(str(body))
        return body["result"]

    async def get_invoice(self, invoice_id: int) -> dict[str, Any] | None:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(
                f"{self.base_url}/getInvoices",
                headers=self.headers,
                params={"invoice_ids": invoice_id},
            )
            r.raise_for_status()
            body = r.json()
        if not body.get("ok"):
            raise CryptoBotError(str(body))
        items = body.get("result", {}).get("items", [])
        return items[0] if items else None


def verify_webhook_signature(token: str, raw_body: bytes, signature: str | None) -> bool:
    if not signature:
        return False
    secret = hashlib.sha256(token.encode()).digest()
    calc = hmac.new(secret, raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(calc, signature)
