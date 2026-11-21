"""One tiny client for any OpenAI-compatible chat API.

Swapping models = changing --provider / --model (or a custom --base-url).
Gemini, Groq, OpenRouter, OpenAI, Together, a Kuku-internal gateway and local
Ollama all speak this protocol, so there is no SDK dependency to fight with.
Every request/response is cached on disk (re-runs are free) and logged raw.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

import requests

PROVIDERS = {
    "gemini":     ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY", "gemini-2.5-flash"),
    "groq":       ("https://api.groq.com/openai/v1", "GROQ_API_KEY", "openai/gpt-oss-120b"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "meta-llama/llama-3.3-70b-instruct:free"),
    "openai":     ("https://api.openai.com/v1", "OPENAI_API_KEY", "gpt-4o-mini"),
    "ollama":     ("http://localhost:11434/v1", "", "llama3.1"),
}


# ---------- keys.env support: paste your key in keys.env, no export needed ----------
PLACEHOLDER = "paste_your"


def load_env() -> None:
    """Read keys.env (or .env) from the current folder or the project folder into os.environ."""
    here = Path(__file__).resolve().parent
    for folder in (Path.cwd(), here.parent):
        for name in ("keys.env", ".env"):
            p = folder / name
            if not p.is_file():
                continue
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if v and not v.lower().startswith(PLACEHOLDER) and not os.environ.get(k):
                    os.environ[k] = v


load_env()
DEFAULT_PROVIDER = os.environ.get("LLM_PROVIDER", "groq")
DEFAULT_MODEL = os.environ.get("LLM_MODEL") or None


class LLMError(RuntimeError):
    """fatal=True means retrying or skipping cannot help (bad key, unknown model): stop the run."""

    def __init__(self, msg: str, fatal: bool = False):
        super().__init__(msg)
        self.fatal = fatal


class LLM:
    def __init__(self, provider: str | None = None, model: str | None = None, api_key: str | None = None,
                 base_url: str | None = None, cache_dir: str | Path = ".cache",
                 temperature: float = 0.2, min_interval: float = 2.5, log_path: str | Path | None = None):
        provider = provider or DEFAULT_PROVIDER
        model = model or (DEFAULT_MODEL if provider == DEFAULT_PROVIDER else None)
        if provider == "mock":
            self.base_url, env, default = "", "", "mock"
        elif provider in PROVIDERS:
            self.base_url, env, default = PROVIDERS[provider]
        elif base_url:
            env, default = "LLM_API_KEY", model
        else:
            raise LLMError(f"unknown provider {provider!r}; use one of {list(PROVIDERS)} or --base-url")
        self.provider = provider
        self.base_url = (base_url or self.base_url).rstrip("/")
        self.model = model or default
        self.api_key = api_key or (os.environ.get(env, "") if env else "")
        if provider not in ("ollama", "mock") and not self.api_key:
            raise LLMError(f"No API key found. Open the file keys.env in the project folder and paste "
                           f"your key after {env}= (then run again).")
        self.temperature = temperature
        self.min_interval = min_interval     # free tiers allow ~10–30 requests/min
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.log_path = Path(log_path) if log_path else None
        self._last = 0.0
        self.calls = 0
        self.cached_calls = 0
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0}

    def name(self) -> str:
        return f"{self.provider}:{self.model}"

    def _log(self, rec: dict):
        if self.log_path:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def chat_json(self, system: str, user: str, tag: str = "", mock_fn=None) -> dict:
        key = hashlib.sha256(json.dumps([self.name(), self.temperature, system, user]).encode()).hexdigest()[:24]
        cpath = self.cache / f"{key}.json"
        if cpath.exists():
            self.cached_calls += 1
            raw = json.loads(cpath.read_text())["raw"]
        elif self.provider == "mock":
            raw = json.dumps(mock_fn(user) if mock_fn else {})
        else:
            raw = self._post(system, user)
            cpath.write_text(json.dumps({"model": self.name(), "tag": tag, "raw": raw}))
        self._log({"tag": tag, "model": self.name(), "prompt_chars": len(system) + len(user), "raw": raw})
        try:
            return parse_json(raw)
        except ValueError as e:
            raise LLMError(f"{tag}: model did not return JSON ({e})") from e

    def _post(self, system: str, user: str) -> str:
        body = {"model": self.model, "temperature": self.temperature,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "response_format": {"type": "json_object"}}
        if "gpt-oss" in self.model:          # reasoning model: keep its thinking short and cheap
            body["reasoning_effort"] = "low"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        delay = 10.0
        r = None
        for attempt in range(10):
            wait = self.min_interval - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            try:
                r = requests.post(f"{self.base_url}/chat/completions", json=body, headers=headers, timeout=180)
            except requests.RequestException as e:
                err = str(e)
                r = None
            else:
                if r.status_code == 200:
                    data = r.json()
                    self.calls += 1
                    for k in self.usage:
                        self.usage[k] += (data.get("usage") or {}).get(k, 0) or 0
                    return data["choices"][0]["message"]["content"] or ""
                err = f"HTTP {r.status_code}: {r.text[:300]}"
                if r.status_code == 400:
                    optional = [k for k in ("reasoning_effort", "response_format") if k in body]
                    hit = [k for k in optional if k in r.text] or optional[-1:]
                    if hit:                          # some models reject an optional setting: drop it, retry
                        body.pop(hit[0])
                        continue
                if r.status_code in (401, 403):
                    raise LLMError(f"API key rejected ({r.status_code}). Check the key in keys.env. {err}", fatal=True)
                if r.status_code == 404:
                    raise LLMError(f"Model '{self.model}' is not available on this account. Set LLM_MODEL in "
                                   f"keys.env to a model listed at console.groq.com/docs/models. {err}", fatal=True)
                if r.status_code not in (429, 500, 502, 503, 504):
                    raise LLMError(err)
            ra = r.headers.get("retry-after") if "r" in locals() and r is not None else None
            try:
                wait_s = float(ra) + 1 if ra else delay
            except ValueError:
                wait_s = delay
            time.sleep(min(wait_s, 120))
            delay = min(delay * 2, 90)
        raise LLMError(f"gave up after retries: {err}")


def parse_json(raw: str) -> dict:
    s = raw.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.S)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", s, re.S)
        if not m:
            raise ValueError("no JSON object found")
        return json.loads(m.group(0))
