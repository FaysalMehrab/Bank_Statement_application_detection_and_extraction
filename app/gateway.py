"""
Universal BYOK LLM Gateway
Supports OpenRouter, OpenAI, Anthropic, DeepSeek, and Custom Endpoints.
"""

import json
import logging
import httpx
from openai import OpenAI
from app.config import SUPPORTED_PROVIDERS

logger = logging.getLogger("gateway")


class UniversalLLMGateway:
    def __init__(self, provider: str, api_key: str, model: str, base_url: str = None):
        self.provider = (provider or "openrouter").lower().strip()
        self.api_key = (api_key or "").strip()
        self.model = (model or "").strip()

        # Resolve and sanitize base_url
        resolved_url = base_url.strip() if base_url and base_url.strip() else None
        if not resolved_url:
            resolved_url = SUPPORTED_PROVIDERS.get(self.provider, {}).get("base_url")

        if resolved_url:
            resolved_url = resolved_url.rstrip("/")
            if resolved_url.endswith("/chat/completions"):
                resolved_url = resolved_url[:-len("/chat/completions")]

        self.base_url = resolved_url

    def test_handshake(self) -> str:
        """Lightweight handshake to verify API key and network reachability."""
        if self.provider == "anthropic":
            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            }
            payload = {
                "model": self.model,
                "max_tokens": 5,
                "messages": [{"role": "user", "content": "ping"}]
            }
            with httpx.Client(timeout=30.0) as client:
                r = client.post("https://api.anthropic.com/v1/messages", json=payload, headers=headers)
                if r.status_code != 200:
                    raise ValueError(f"Anthropic error ({r.status_code}): {r.text}")
                return "OK"
        else:
            client = self._build_openai_client()
            resp = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
            )
            return "OK"

    def _build_openai_client(self) -> OpenAI:
        default_headers = {}
        if self.provider == "openrouter" or (self.base_url and "openrouter" in self.base_url):
            default_headers = {
                "HTTP-Referer": "http://localhost:8000",
                "X-Title": "Universal Underwriting Engine",
            }

        return OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            default_headers=default_headers,
            timeout=120.0,
        )

    def call_structured(self, system_prompt: str, user_content: list, schema: dict, schema_name: str = "output_schema") -> tuple[dict, dict]:
        if self.provider == "anthropic":
            return self._call_anthropic(system_prompt, user_content, schema, schema_name)
        else:
            return self._call_openai_compatible(system_prompt, user_content, schema, schema_name)

    def _call_openai_compatible(self, system_prompt: str, user_content: list, schema: dict, schema_name: str) -> tuple[dict, dict]:
        client = self._build_openai_client()

        openai_content = []
        for part in user_content:
            p_type = part.get("type")
            if p_type == "input_text":
                openai_content.append({"type": "text", "text": part["text"]})
            elif p_type == "input_image":
                openai_content.append({
                    "type": "image_url",
                    "image_url": {"url": part["image_url"], "detail": part.get("detail", "high")}
                })

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": openai_content if len(openai_content) > 1 else (openai_content[0]["text"] if openai_content else "")}
        ]

        # Primary attempt: Strict json_schema
        try:
            resp = client.chat.completions.create(
                model=self.model,
                messages=messages,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema_name,
                        "strict": True,
                        "schema": schema,
                    }
                },
            )
            raw_text = resp.choices[0].message.content
            parsed = json.loads(raw_text)
        except Exception as e:
            # Fallback for models that don't support strict mode: standard json_object
            logger.warning(f"Strict json_schema rejected by {self.model} ({e}). Retrying with json_object mode...")
            resp = client.chat.completions.create(
                model=self.model,
                messages=messages + [{"role": "system", "content": "Return ONLY a valid JSON object matching the requested schema."}],
                response_format={"type": "json_object"},
            )
            raw_text = resp.choices[0].message.content
            parsed = json.loads(raw_text)

        usage = resp.usage
        in_tok = getattr(usage, "prompt_tokens", 0) or 0
        out_tok = getattr(usage, "completion_tokens", 0) or 0
        cached_tok = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0

        metrics = self._calc_cost(in_tok, cached_tok, out_tok)
        return parsed, metrics

    def _call_anthropic(self, system_prompt: str, user_content: list, schema: dict, schema_name: str) -> tuple[dict, dict]:
        anthropic_content = []
        for part in user_content:
            p_type = part.get("type")
            if p_type == "input_text":
                anthropic_content.append({"type": "text", "text": part["text"]})
            elif p_type == "input_image":
                data_uri = part["image_url"]
                header, b64_data = data_uri.split(",", 1)
                media_type = header.split(";")[0].replace("data:", "")
                anthropic_content.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": b64_data}
                })

        tools = [{
            "name": schema_name,
            "description": "Structured extraction schema",
            "input_schema": schema
        }]

        payload = {
            "model": self.model,
            "max_tokens": 8192,
            "system": system_prompt,
            "messages": [{"role": "user", "content": anthropic_content}],
            "tools": tools,
            "tool_choice": {"type": "tool", "name": schema_name}
        }

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }

        with httpx.Client(timeout=180.0) as client:
            resp = client.post("https://api.anthropic.com/v1/messages", json=payload, headers=headers)
            if resp.status_code != 200:
                raise ValueError(f"Anthropic API Error ({resp.status_code}): {resp.text}")
            res_data = resp.json()

        parsed = {}
        for content_block in res_data.get("content", []):
            if content_block.get("type") == "tool_use" and content_block.get("name") == schema_name:
                parsed = content_block.get("input", {})
                break

        usage = res_data.get("usage", {})
        in_tok = usage.get("input_tokens", 0)
        out_tok = usage.get("output_tokens", 0)
        cached_tok = usage.get("cache_read_input_tokens", 0)

        metrics = self._calc_cost(in_tok, cached_tok, out_tok)
        return parsed, metrics

    def _calc_cost(self, in_tok: int, cached_tok: int, out_tok: int) -> dict:
        uncached_tok = max(in_tok - cached_tok, 0)
        price_cfg = {"in": 0.0, "cached": 0.0, "out": 0.0}
        for p in SUPPORTED_PROVIDERS.values():
            for m in p.get("models", []):
                if m["id"] == self.model:
                    price_cfg = {"in": m["in_price"], "cached": m["cached_price"], "out": m["out_price"]}
                    break

        in_cost = (uncached_tok / 1_000_000 * price_cfg["in"])
        cached_cost = (cached_tok / 1_000_000 * price_cfg["cached"])
        out_cost = (out_tok / 1_000_000 * price_cfg["out"])
        return {
            "input_tokens": in_tok,
            "cached_input_tokens": cached_tok,
            "output_tokens": out_tok,
            "total_tokens": in_tok + out_tok,
            "cost": in_cost + cached_cost + out_cost,
        }