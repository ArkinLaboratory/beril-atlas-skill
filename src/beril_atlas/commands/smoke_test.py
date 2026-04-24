"""`beril-atlas smoke-test` — verify credentials + structured JSON parsing.

Loads LLMConfig from .env, instantiates the provider client, makes one small
call asking for structured JSON output, and classifies the result.

Not cached. Every invocation makes a fresh provider call.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Optional

from beril_atlas import discovery


# Error class taxonomy — matches CONFIGURE.md spec
ERR_AUTH = "auth"
ERR_NOT_FOUND = "not_found"
ERR_RATE_LIMIT = "rate_limit"
ERR_NETWORK = "network"
ERR_MALFORMED_JSON = "malformed_json"
ERR_CONFIG = "config"


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "smoke-test",
        help="Run a small structured-output call against the configured provider.",
        description=(
            "Verify credentials and structured JSON parsing end-to-end. Does "
            "not modify .env. Exit 0 on success, 2 on failure."
        ),
    )
    p.add_argument("--beril-root", help="Explicit BERIL_ROOT.")
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON result.",
    )
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace) -> int:
    # Resolve BERIL_ROOT + load config
    try:
        beril_root = discovery.find_beril_root(explicit=args.beril_root)
    except discovery.BerilRootNotFound as e:
        result = _failure(ERR_CONFIG, f"BERIL_ROOT not found: {e}", None, None, 0)
        _emit(args, result)
        return 2

    env_path = discovery.get_env_path(beril_root)
    try:
        from beril_atlas.engine import llm_config, llm_client
        cfg = llm_config.load_atlas_config(env_path=env_path)
    except Exception as e:
        result = _failure(ERR_CONFIG, str(e), None, None, 0)
        _emit(args, result)
        return 2

    # Build client
    try:
        client = llm_client.build_client(cfg)
    except Exception as e:
        result = _failure(ERR_CONFIG, f"client build: {e}",
                          cfg.provider, cfg.default_model, 0)
        _emit(args, result)
        return 2

    # Make the structured-factual call
    prompt = (
        "Respond with JSON in exactly this shape:\n"
        '{"capital_of_france": "<string>", "year": <integer>}\n'
        "Do not include any text outside the JSON object."
    )
    start = time.time()
    try:
        chat_resp = client.chat(
            messages=[{"role": "user", "content": prompt}],
            model=cfg.default_model,
            max_tokens=40,
            temperature=0.0,
            response_format="json",
        )
    except Exception as e:
        latency_ms = int((time.time() - start) * 1000)
        err_class = _classify_exception(e)
        result = _failure(err_class, str(e), cfg.provider, cfg.default_model, latency_ms)
        _emit(args, result)
        return 2

    latency_ms = int((time.time() - start) * 1000)

    # Shape-check: extract JSON from response content (may be raw JSON or
    # fenced in ```json blocks depending on provider — cf. CBORG + Claude).
    raw_content = chat_resp.content if hasattr(chat_resp, "content") else str(chat_resp)
    payload = _extract_json(raw_content)
    if payload is None:
        result = _failure(
            ERR_MALFORMED_JSON,
            f"response did not parse as JSON: {raw_content[:200]!r}",
            cfg.provider, cfg.default_model, latency_ms,
        )
        _emit(args, result)
        return 2

    capital = payload.get("capital_of_france")
    year = payload.get("year")
    if not isinstance(capital, str) or not capital.strip():
        result = _failure(
            ERR_MALFORMED_JSON,
            f"'capital_of_france' missing or not a non-empty string: "
            f"got {capital!r}",
            cfg.provider, cfg.default_model, latency_ms,
        )
        _emit(args, result)
        return 2
    if not isinstance(year, int):
        result = _failure(
            ERR_MALFORMED_JSON,
            f"'year' missing or not an integer: got {year!r}",
            cfg.provider, cfg.default_model, latency_ms,
        )
        _emit(args, result)
        return 2

    # Success
    result = {
        "success": True,
        "provider": cfg.provider,
        "model": cfg.default_model,
        "latency_ms": latency_ms,
        "error_class": None,
        "error_message": None,
    }
    _emit(args, result)
    return 0


def _extract_json(content: str) -> Optional[dict]:
    """Extract a JSON object from response content.

    Handles three cases:
      1. Raw JSON: `{"key": "value"}`
      2. Fenced in markdown: ```json\n{...}\n```
      3. Fenced without language tag: ```\n{...}\n```
    Returns None on parse failure.

    Per memory reference_jsonmode_cborg_claude: CBORG honors response_format
    for OpenAI but Claude-through-CBORG returns fenced JSON even with
    json_mode set, so we must tolerate both.
    """
    if not content:
        return None
    import re as _re
    stripped = content.strip()
    # Try raw first
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    # Try fenced (```json...``` or ```...```)
    fenced = _re.search(r"```(?:json)?\s*\n?(.*?)\n?```", stripped, _re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except json.JSONDecodeError:
            pass
    return None


def _failure(err_class: str, message: str, provider: Optional[str],
             model: Optional[str], latency_ms: int) -> dict:
    return {
        "success": False,
        "provider": provider,
        "model": model,
        "latency_ms": latency_ms,
        "error_class": err_class,
        "error_message": message,
    }


def _classify_exception(e: Exception) -> str:
    """Best-effort classification of provider exceptions into our taxonomy."""
    msg = str(e).lower()
    # Engine-layer exceptions first
    try:
        from beril_atlas.engine.llm_client import LLMRateLimitError
        if isinstance(e, LLMRateLimitError):
            return ERR_RATE_LIMIT
    except Exception:
        pass
    # HTTP-ish signals
    if "401" in msg or "unauthorized" in msg or "invalid api" in msg or "auth" in msg:
        return ERR_AUTH
    if "404" in msg or "not found" in msg:
        return ERR_NOT_FOUND
    if "429" in msg or "rate limit" in msg or "quota" in msg:
        return ERR_RATE_LIMIT
    if "timeout" in msg or "connection" in msg or "dns" in msg or "network" in msg:
        return ERR_NETWORK
    return ERR_NETWORK  # default — least specific, most actionable


def _emit(args: argparse.Namespace, result: dict) -> None:
    if args.json:
        print(json.dumps(result, indent=2))
        return
    if result["success"]:
        print(f"Smoke test passed.")
        print(f"  Provider: {result['provider']}")
        print(f"  Model:    {result['model']}")
        print(f"  Latency:  {result['latency_ms']} ms")
    else:
        print(f"Smoke test FAILED.")
        print(f"  Provider: {result['provider'] or '(unknown)'}")
        print(f"  Model:    {result['model'] or '(unknown)'}")
        print(f"  Class:    {result['error_class']}")
        print(f"  Message:  {result['error_message']}")
