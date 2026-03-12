from __future__ import annotations

import secrets
import uuid
from urllib.parse import quote


def gen_uuid() -> str:
    return str(uuid.uuid4())


def apply_template(template: str, **kwargs) -> str:
    """
    template example:
      vless://{uuid}@{host}:{port}?type=ws&security=tls&sni={sni}&path={path}#{name}
    """
    return template.format(**kwargs)


def make_v2raytun_deeplink(config_or_sub_url: str) -> str:
    """
    v2RayTun deep link format:
      v2raytun://import/{configuration}
      v2raytun://import/{subscription_link}
    We URL-encode to keep it safe as a path segment.
    """
    return "v2raytun://import/" + quote(config_or_sub_url, safe="")
