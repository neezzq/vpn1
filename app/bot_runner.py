from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os

from dotenv import load_dotenv
from sqlalchemy import select

from .config import load_settings
from .db import make_engine, make_session_factory, session_scope
from .models import Base, Key, KeyStatus
from .crud import bill_daily
from .bot_app import build_bot
from .migrate import ensure_schema
from .three_xui import ThreeXUI, ThreeXUIConfig



def _make_xui(settings):
    if not (settings.xui_base_url and settings.xui_username and settings.xui_password and settings.xui_inbound_id):
        return None
    return ThreeXUI(ThreeXUIConfig(
        base_url=settings.xui_base_url,
        username=settings.xui_username,
        password=settings.xui_password,
        verify_tls=settings.xui_verify_tls,
    ))



def _xui_missing_error(exc: Exception) -> bool:
    text = str(exc).lower()
    needles = ["not found", "record not found", "unable to find", "client not found", "failed to find"]
    return any(n in text for n in needles)


async def _billing_loop(SessionLocal, settings, xui: ThreeXUI | None):
    """Bills once per day at 00:05 server time."""
    while True:
        now = dt.datetime.now()
        tomorrow = (now + dt.timedelta(days=1)).date()
        next_run = dt.datetime.combine(tomorrow, dt.time(hour=0, minute=5, second=0))
        delay = (next_run - now).total_seconds()
        await asyncio.sleep(max(5, delay))
        try:
            with session_scope(SessionLocal) as session:
                stats = bill_daily(
                    session,
                    settings.price_per_key_per_day_cents,
                    insufficient_action=settings.billing_insufficient_action,
                )
            logging.info("Billing done: %s", stats)

            if xui and isinstance(stats, dict) and isinstance(stats.get("actions"), dict):
                actions = stats["actions"]

                for item in actions.get("pause", []) or []:
                    inbound_id = item.get("xui_inbound_id")
                    client_id = item.get("xui_client_id")
                    client_json = item.get("xui_client_json")
                    if not (inbound_id and client_id and client_json):
                        continue
                    try:
                        import json

                        client_obj = json.loads(client_json)
                        client_obj["enable"] = False
                        await asyncio.to_thread(xui.update_client, int(inbound_id), str(client_id), client_obj)
                    except Exception:
                        logging.exception("Failed to pause client in 3x-ui: inbound=%s uuid=%s", inbound_id, client_id)

                for item in actions.get("revoke", []) or []:
                    inbound_id = item.get("xui_inbound_id")
                    client_id = item.get("xui_client_id")
                    if not (inbound_id and client_id):
                        continue
                    try:
                        await asyncio.to_thread(xui.delete_client, int(inbound_id), str(client_id))
                    except Exception:
                        logging.exception("Failed to delete client in 3x-ui: inbound=%s uuid=%s", inbound_id, client_id)
        except Exception:
            logging.exception("Billing failed")


async def _xui_sync_loop(SessionLocal, xui: ThreeXUI | None):
    if not xui:
        return
    while True:
        await asyncio.sleep(300)
        try:
            with session_scope(SessionLocal) as session:
                keys = list(
                    session.scalars(
                        select(Key).where(
                            Key.status.in_([KeyStatus.active, KeyStatus.paused]),
                            Key.xui_inbound_id.is_not(None),
                            Key.xui_client_id.is_not(None),
                        )
                    )
                )
                if not keys:
                    continue

                remote_by_inbound: dict[int, set[str] | None] = {}
                for inbound_id in sorted({int(k.xui_inbound_id) for k in keys if k.xui_inbound_id}):
                    try:
                        remote_by_inbound[inbound_id] = await asyncio.to_thread(xui.list_client_ids, inbound_id)
                    except Exception as exc:
                        if _xui_missing_error(exc):
                            remote_by_inbound[inbound_id] = set()
                        else:
                            remote_by_inbound[inbound_id] = None
                            logging.exception("3x-ui sync failed for inbound=%s", inbound_id)

                changed = 0
                for key in keys:
                    remote_ids = remote_by_inbound.get(int(key.xui_inbound_id)) if key.xui_inbound_id else None
                    if remote_ids is None:
                        continue
                    if str(key.xui_client_id) not in remote_ids:
                        key.status = KeyStatus.revoked
                        session.add(key)
                        changed += 1
                if changed:
                    logging.info("3x-ui sync revoked %s locally deleted keys", changed)
        except Exception:
            logging.exception("3x-ui reverse sync failed")


async def main():
    load_dotenv()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

    settings = load_settings()
    engine = make_engine(settings)
    SessionLocal = make_session_factory(engine)

    if settings.database_url.startswith("sqlite"):
        os.makedirs("./data", exist_ok=True)

    Base.metadata.create_all(bind=engine)
    ensure_schema(engine)

    bot, dp = build_bot(settings, SessionLocal)
    xui = _make_xui(settings)

    asyncio.create_task(_billing_loop(SessionLocal, settings, xui))
    asyncio.create_task(_xui_sync_loop(SessionLocal, xui))

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
