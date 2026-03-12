from __future__ import annotations

import os
import shlex
import subprocess
from typing import Optional


def run_hook(cmd_template: str | None, action: str, key_name: str, config_uri: str) -> None:
    """
    Optional hook, called when key status changes.
    cmd_template example:
      /usr/local/bin/vpn_hook.sh --action {action} --key {key_name} --uri "{config_uri}"
    """
    if not cmd_template:
        return
    cmd = cmd_template.format(action=action, key_name=key_name, config_uri=config_uri)
    try:
        subprocess.run(shlex.split(cmd), check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        # do not crash MVP
        return
