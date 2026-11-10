"""Current ticker/issuer identity validation using Massive's ticker overview.

The Form 4 ticker array can contain historical aliases, warrants and multiple
share classes. Verify the current listed common share's CIK before ANY buy.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .auto_signals import SYMBOL, NoRedirect


def ticker_reference(symbol, *, token=None, request=None):
    if not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol):
        raise ValueError("Unsafe symbol for ticker reference")
    if token is None:
        token = os.getenv("MASSIVE_API_KEY", "").strip()
    if not token:
        raise ValueError("Missing MASSIVE_API_KEY for issuer identity validation")
    url = "https://api.massive.com/v3/reference/tickers/" + symbol
    if request is not None:
        payload = request(url, token)
    else:
        req = urllib.request.Request(url, method="GET", headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/json",
            "User-Agent": "massive-insider-paper-automator/0.5",
        })
        try:
            with urllib.request.build_opener(NoRedirect()).open(req, timeout=30) as res:
                raw = res.read(500_000 + 1)
        except urllib.error.HTTPError as e:
            raise RuntimeError("Massive ticker reference HTTP " + str(e.code)) from None
        except urllib.error.URLError:
            raise RuntimeError("Massive ticker reference request failed") from None
        if len(raw) > 500_000:
            raise ValueError("Ticker reference response too large")
        payload = json.loads(raw)
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), dict):
        raise ValueError("Malformed Massive ticker reference")
    result = payload["results"]
    if result.get("ticker") != symbol:
        raise ValueError("Ticker reference symbol disagreement")
    return result


def verified_current_common_stock(candidate, details):
    if not isinstance(candidate, dict) or not isinstance(details, dict):
        return False
    cik = str(details.get("cik") or "").zfill(10)
    return (
        details.get("ticker") == candidate.get("symbol")
        and details.get("active") is True
        and details.get("type") == "CS"
        and details.get("market") == "stocks"
        and details.get("locale") == "us"
        and cik == candidate.get("issuer_cik")
        and details.get("primary_exchange") in {"XNYS", "XNAS", "XASE"}
    )
