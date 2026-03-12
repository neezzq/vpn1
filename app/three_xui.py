from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx


class ThreeXUIError(RuntimeError):
    pass


@dataclass
class ThreeXUIConfig:
    base_url: str
    username: str
    password: str
    verify_tls: bool = True
    timeout_sec: float = 10.0


class ThreeXUI:
    """Tiny 3X-UI API client (session-cookie auth)."""

    def __init__(self, cfg: ThreeXUIConfig):
        self.cfg = cfg
        self._client = httpx.Client(
            base_url=cfg.base_url.rstrip("/"),
            timeout=cfg.timeout_sec,
            verify=cfg.verify_tls,
            follow_redirects=True,
            headers={"Accept": "application/json"},
        )
        self._authed = False
        self._last_login_ts = 0.0

    def close(self) -> None:
        self._client.close()

    def login(self) -> None:
        payload = {"username": self.cfg.username, "password": self.cfg.password}
        r = self._client.post("/login", json=payload)
        if r.status_code >= 400:
            r = self._client.post("/login", data=payload)
        if r.status_code >= 400:
            raise ThreeXUIError(f"3x-ui login failed: {r.status_code} {r.text[:200]}")
        self._authed = True
        self._last_login_ts = time.time()

    def _request(self, method: str, path: str, *, retry_on_401: bool = True, **kwargs) -> httpx.Response:
        if not self._authed:
            self.login()

        r = self._client.request(method, path, **kwargs)
        if r.status_code in (401, 403) and retry_on_401:
            self.login()
            r = self._client.request(method, path, **kwargs)
        return r

    @staticmethod
    def _expect_success(resp: httpx.Response) -> None:
        if resp.status_code >= 400:
            raise ThreeXUIError(f"3x-ui request failed: {resp.status_code} {resp.text[:300]}")
        try:
            data = resp.json()
            if isinstance(data, dict) and data.get("success") is False:
                raise ThreeXUIError(f"3x-ui request not successful: {data.get('msg') or data}")
        except ValueError:
            return

    @staticmethod
    def _extract_obj(resp: httpx.Response) -> Any:
        ThreeXUI._expect_success(resp)
        try:
            data = resp.json()
        except ValueError:
            return None
        if isinstance(data, dict) and "obj" in data:
            return data.get("obj")
        return data

    @staticmethod
    def _parse_settings(settings_raw: Any) -> dict[str, Any]:
        if isinstance(settings_raw, dict):
            return settings_raw
        if isinstance(settings_raw, str) and settings_raw.strip():
            try:
                parsed = json.loads(settings_raw)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                return {}
        return {}

    def add_client(self, inbound_id: int, client_obj: dict[str, Any]) -> None:
        settings = json.dumps({"clients": [client_obj]}, ensure_ascii=False)
        files = {
            "id": (None, str(inbound_id)),
            "settings": (None, settings),
        }
        r = self._request("POST", "/panel/api/inbounds/addClient", files=files)
        self._expect_success(r)

    def update_client(self, inbound_id: int, client_uuid: str, client_obj: dict[str, Any]) -> None:
        settings = json.dumps({"clients": [client_obj]}, ensure_ascii=False)
        body = {"id": inbound_id, "settings": settings}
        r = self._request("POST", f"/panel/api/inbounds/updateClient/{client_uuid}", json=body)
        self._expect_success(r)

    def delete_client(self, inbound_id: int, client_uuid: str) -> None:
        r = self._request("POST", f"/panel/api/inbounds/{inbound_id}/delClient/{client_uuid}")
        self._expect_success(r)

    def get_inbound(self, inbound_id: int) -> dict[str, Any] | None:
        r = self._request("GET", f"/panel/api/inbounds/get/{inbound_id}")
        obj = self._extract_obj(r)
        return obj if isinstance(obj, dict) else None

    def list_client_ids(self, inbound_id: int) -> set[str]:
        inbound = self.get_inbound(inbound_id)
        if not inbound:
            return set()
        settings = self._parse_settings(inbound.get("settings"))
        clients = settings.get("clients") or []
        result: set[str] = set()
        for client in clients:
            if not isinstance(client, dict):
                continue
            client_id = client.get("id") or client.get("password") or client.get("email")
            if client_id:
                result.add(str(client_id))
        return result
