"""
GPT through the OpenAI API. Every call is logged in the llm_calls table,
so you always know how many tokens (= how much money) the agent used.

If OPENAI_API_KEY is empty, ask_json() returns None and the agent
keeps working without GPT (only with its free rules).
"""

import json
import re

import config
import database as db

_client = None
_disabled = False
run_tokens = {"in": 0, "out": 0}          # tokens used in THIS run (budget: config.GPT_TOKEN_BUDGET_PER_RUN)
_effort_ok = {"value": True}              # does the model accept reasoning_effort?


def available() -> bool:
    return bool(config.OPENAI_API_KEY) and not _disabled


def _get_client():
    global _client
    if _client is None:
        from openai import OpenAI
        _client = OpenAI(api_key=config.OPENAI_API_KEY)
    return _client


def ask_json(purpose: str, system: str, user: str, cheap: bool = True):
    """Ask GPT and get back a dict (JSON). Returns None if GPT is off or fails."""
    global _disabled
    if not available():
        return None
    if run_tokens["in"] + run_tokens["out"] >= config.GPT_TOKEN_BUDGET_PER_RUN:
        print(f"  GPT budget for this run used up ({config.GPT_TOKEN_BUDGET_PER_RUN:,} tokens, see config.py). "
              f"GPT switched off; run again later to continue.")
        _disabled = True
        return None
    model = config.OPENAI_MODEL_CHEAP if cheap else config.OPENAI_MODEL
    kwargs = dict(model=model,
                  messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                  response_format={"type": "json_object"})
    if config.GPT_REASONING_EFFORT and _effort_ok["value"]:
        kwargs["reasoning_effort"] = config.GPT_REASONING_EFFORT     # less "thinking" = fewer tokens
    try:
        try:
            resp = _get_client().chat.completions.create(**kwargs)
        except Exception as e:
            if "reasoning" not in str(e).lower() or "reasoning_effort" not in kwargs:
                raise
            _effort_ok["value"] = False           # this model doesn't support it -> never send it again
            kwargs.pop("reasoning_effort")
            resp = _get_client().chat.completions.create(**kwargs)
    except Exception as e:
        msg = str(e)
        print(f"  GPT error ({purpose}): {msg[:200]}")
        if any(w in msg.lower() for w in ("api key", "401", "model_not_found", "does not exist", "quota")):
            print("  -> GPT switched off for this run. Check OPENAI_API_KEY / OPENAI_MODEL_CHEAP in .env")
            _disabled = True
        return None

    usage = getattr(resp, "usage", None)
    tin, tout = getattr(usage, "prompt_tokens", 0) or 0, getattr(usage, "completion_tokens", 0) or 0
    run_tokens["in"] += tin
    run_tokens["out"] += tout
    with db.connect() as conn:
        db.log_llm_call(conn, purpose, model, tin, tout)
    try:
        return json.loads(resp.choices[0].message.content or "{}")
    except (json.JSONDecodeError, IndexError, AttributeError):
        return None


_search_mode = {"value": None}   # which web search method works with this account


def _parse_json(text: str):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def web_search_json(purpose: str, prompt: str):
    """
    Let GPT search the web and answer in JSON. Used only for small, targeted lookups
    (e.g. the official website of a company), never for collecting data in bulk.
    Tries: 1) Responses API + web_search tool, 2) older tool name, 3) a "-search" chat model.
    """
    global _disabled
    if not available():
        return None
    if run_tokens["in"] + run_tokens["out"] >= config.GPT_TOKEN_BUDGET_PER_RUN:
        return None
    client = _get_client()
    attempts = [
        ("responses", config.OPENAI_MODEL, "web_search"),
        ("responses", config.OPENAI_MODEL, "web_search_preview"),
        ("chat", config.OPENAI_SEARCH_MODEL, None),
    ]
    if _search_mode["value"]:
        if _search_mode["value"][0] == "off":
            return None
        attempts = [_search_mode["value"]]
    for mode in attempts:
        kind, model, tool = mode
        try:
            if kind == "responses":
                resp = client.responses.create(model=model, tools=[{"type": tool}], input=prompt)
                text = getattr(resp, "output_text", "") or ""
                usage = getattr(resp, "usage", None)
                tin, tout = getattr(usage, "input_tokens", 0) or 0, getattr(usage, "output_tokens", 0) or 0
            else:
                resp = client.chat.completions.create(model=model, messages=[{"role": "user", "content": prompt}])
                text = resp.choices[0].message.content or ""
                usage = getattr(resp, "usage", None)
                tin, tout = getattr(usage, "prompt_tokens", 0) or 0, getattr(usage, "completion_tokens", 0) or 0
        except Exception as e:
            if "401" in str(e) or "api key" in str(e).lower():
                _disabled = True
                return None
            continue
        _search_mode["value"] = mode
        run_tokens["in"] += tin
        run_tokens["out"] += tout
        with db.connect() as conn:
            db.log_llm_call(conn, purpose, f"{model} + web search", tin, tout)
        return _parse_json(text)
    print("  GPT web search not available with this account/model (set OPENAI_SEARCH_MODEL in .env)")
    _search_mode["value"] = ("off", None, None)
    return None


def usage_summary() -> str:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT model, COUNT(*) n, SUM(input_tokens) i, SUM(output_tokens) o FROM llm_calls GROUP BY model"
        ).fetchall()
    if not rows:
        return "GPT: not used yet."
    this_run = (f"This run: {run_tokens['in']:,} in / {run_tokens['out']:,} out tokens. "
                if run_tokens["in"] or run_tokens["out"] else "")
    return this_run + "Total GPT: " + "; ".join(f"{r['model']} {r['n']} calls, {r['i'] or 0} in / {r['o'] or 0} out tokens"
                               for r in rows)
