"""The AI model: Groq's gpt-oss-120b through its OpenAI-compatible API, answering in JSON.

    .env:  GROQ_API_KEY=gsk_...              required
           GROQ_MODEL=openai/gpt-oss-120b    optional (default)
           GROQ_FALLBACK_MODEL=openai/gpt-oss-20b   tried once if the main model stalls or is busy ("none" = off)

    llm = GroqJSON()
    data, info = llm.json(system, user, schema)     # data = parsed dict; info = model, seconds, tokens

The model is asked for JSON matching `schema` (Groq's structured output). If the account or model doesn't
support that, it falls back to plain JSON mode with the schema in the instructions. Pydantic checks the result
afterwards either way (docsift/extract.py), so a slightly-off answer is caught, not trusted.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path

log = logging.getLogger("docsift")
ROOT = Path(__file__).resolve().parent.parent

DEFAULT_MODEL = "openai/gpt-oss-120b"
DEFAULT_FALLBACK = "openai/gpt-oss-20b"
BASE_URL = "https://api.groq.com/openai/v1"

# gpt-oss writes typographic spaces and hyphens ("9:00 AM", "out‑of‑network"): plain ones compare
# cleanly with the document text.
_ODD = str.maketrans({" ": " ", " ": " ", " ": " ", " ": " ", " ": " ",
                      "‐": "-", "‑": "-", "‒": "-", "–": "-", "−": "-"})


class LLMError(RuntimeError):
    """A message a person can act on ("check GROQ_API_KEY in .env")."""


def load_dotenv(path: Path | None = None) -> None:
    path = path or ROOT / ".env"          # looked up when called, so tests can point ROOT elsewhere
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def clean(value):
    """Normalise typography in every string of a parsed answer."""
    if isinstance(value, str):
        return value.translate(_ODD).strip()
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    return value


def parse_json(text: str) -> dict:
    """The JSON object in a reply, even if the model wrapped it in ```json fences or a sentence."""
    text = (text or "").strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object in the reply")
    return json.loads(m.group(0))


class GroqJSON:
    NAME = "Groq"

    def __init__(self, model: str | None = None, client=None):
        load_dotenv()
        self.model = model or os.environ.get("GROQ_MODEL") or DEFAULT_MODEL
        fb = os.environ.get("GROQ_FALLBACK_MODEL", DEFAULT_FALLBACK)
        self.fallback = None if not fb or fb.lower() == "none" or fb == self.model else fb
        self._client = client
        self.schema_mode = True            # turned off for good if the API rejects json_schema

    @property
    def client(self):
        if self._client is None:
            if not os.environ.get("GROQ_API_KEY"):
                raise LLMError("GROQ_API_KEY is not set. Add it to .env in the project folder.")
            from openai import OpenAI
            self._client = OpenAI(api_key=os.environ["GROQ_API_KEY"], base_url=BASE_URL, max_retries=1,
                                  timeout=float(os.environ.get("LLM_TIMEOUT") or 45))
        return self._client

    def json(self, system: str, user: str, schema: dict | None = None, name: str = "result",
             max_tokens: int = 4000) -> tuple[dict, dict]:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        # gpt-oss is a reasoning model: no temperature; "low" effort is plenty for copying values off a page
        # (the same settings Clinic Concierge uses on Groq).
        kwargs = {"messages": messages, "max_completion_tokens": max_tokens,
                  "reasoning_effort": os.environ.get("GROQ_REASONING_EFFORT", "low")}
        t = time.perf_counter()
        resp, model = self._create(kwargs, schema, name)
        choice = resp.choices[0]
        text = choice.message.content or ""
        if not text and choice.finish_reason == "length":
            raise LLMError(f"The model ran out of tokens before answering (max {max_tokens}).")
        usage = getattr(resp, "usage", None)
        info = {"model": model, "seconds": round(time.perf_counter() - t, 2),
                "tokens": getattr(usage, "total_tokens", None)}
        try:
            return clean(parse_json(text)), info
        except ValueError as e:
            raise LLMError(f"The model's answer wasn't valid JSON ({e}): {text[:200]}") from e

    # ---------------------------------------------------------------------------------------------
    def _create(self, kwargs, schema, name):
        import openai
        fmt = ({"type": "json_schema", "json_schema": {"name": name, "schema": schema, "strict": False}}
               if schema and self.schema_mode else {"type": "json_object"})
        models = [self.model] + ([self.fallback] if self.fallback else [])
        last = None
        for i, model in enumerate(models):
            try:
                return self.client.chat.completions.create(model=model, response_format=fmt, **kwargs), model
            except openai.BadRequestError as e:
                msg = str(getattr(e, "message", e)).lower()
                if fmt["type"] == "json_schema" and ("json_schema" in msg or "response_format" in msg):
                    log.warning("structured output not accepted (%s); using JSON mode", msg[:120])
                    self.schema_mode = False
                    return self._create(kwargs, schema, name)
                if "json_validate_failed" in msg or "failed to generate json" in msg or "tool_use_failed" in msg:
                    last = e                     # a malformed answer: random, so try again (next model)
                    continue
                raise LLMError(f"Groq rejected the request: {getattr(e, 'message', e)}") from e
            except openai.AuthenticationError as e:
                raise LLMError("Groq rejected the API key. Check GROQ_API_KEY in .env.") from e
            except openai.NotFoundError as e:
                raise LLMError(f"Groq doesn't offer the model '{model}' to this key. Set GROQ_MODEL in .env.") from e
            except (openai.APITimeoutError, openai.InternalServerError, openai.RateLimitError,
                    openai.APIConnectionError) as e:
                last = e
                log.warning("%s didn't answer (%s)%s", model, type(e).__name__,
                            f"; trying {models[i + 1]}" if i + 1 < len(models) else "")
                continue
        if isinstance(last, openai.RateLimitError):
            raise LLMError("Groq's usage limit was reached. Wait a minute, or check the plan's limits.") from last
        if isinstance(last, openai.BadRequestError):
            raise LLMError("The model kept producing malformed JSON. Try again, or try another GROQ_MODEL.") from last
        raise LLMError(f"Groq didn't answer ({type(last).__name__}: {last}). Check the internet connection, "
                       f"or try again shortly.") from last
