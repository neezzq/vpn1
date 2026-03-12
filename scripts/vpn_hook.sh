#!/usr/bin/env bash
set -euo pipefail

# Example hook script.
# Called as: vpn_hook.sh --action pause|resume|revoke --key <key_name> [--uri <config_uri>]
# Integrate here with 3x-ui / x-ui / marzban / xray API to really disable users on server.

ACTION=""
KEY=""
URI=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --action) ACTION="$2"; shift 2;;
    --key) KEY="$2"; shift 2;;
    --uri) URI="$2"; shift 2;;
    *) shift;;
  esac
done

echo "Hook called: action=$ACTION key=$KEY" >&2
exit 0
