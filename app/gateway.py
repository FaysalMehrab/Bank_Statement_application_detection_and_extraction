"""
Universal BYOK LLM Gateway
Executes requests strictly against the user-specified provider and model.
Handles model-specific input structures, reasoning parameters, token limits,
and structured output modes across OpenAI, Anthropic, DeepSeek, OpenRouter, and custom endpoints.
"""

import json
import logging
import re
import httpx
from openai import OpenAI
from app.config import SUPPORTED_PROVIDERS

logger = logging.getLogger("gateway")


def _find_json_in_text(raw_text: str, prefer_object: bool = True) -> dict | list | None:
    """Safely extracts and parses JSON from text, stripping think tags, markdown, and chatter."""
    if not raw_text or not isinstance(raw_text, str):
        return None

    cleaned = raw_text.strip()

    # Strip thinking traces if model is a reasoning model: <think>...</think>
    if "<think>" in cleaned and "</think>" in cleaned:
        cleaned = re.sub(r"<think>[\s\S]*?</think>", "", cleaned).strip()
    elif "</think>" in cleaned:
        cleaned = cleaned.split("</think>")[-1].strip()

    # Strip markdown code fences: ```json ... ``` or ``` ... ```
    if "```" in cleaned:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned)
        if match:
            cleaned = match.group(1).strip()

    # 1. Direct JSON parse
    try:
        data = json.loads(cleaned)
        if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict) and prefer_object:
            return data[0]
        if isinstance(data, (dict, list)):
            return data
    except Exception:
        pass

    # 2. Prioritize outermost {...} if an object is expected
    if prefer_object:
        first_brace = cleaned.find("{")
        last_brace = cleaned.rfind("}")
        if first_brace != -1 and last_brace > first_brace:
            candidate = cleaned[first_brace:last_brace + 1]
            try:
                data = json.loads(candidate)
                if isinstance(data, dict):
                    return data
            except Exception:
                pass

    # 3. Check outermost [...]
    first_bracket = cleaned.find("[")
    last_bracket = cleaned.rfind("]")
    if first_bracket != -1 and last_bracket > first_bracket:
        candidate = cleaned[first_bracket:last_bracket + 1]
        try:
            data = json.loads(candidate)
            if isinstance(data, list):
                if len(data) > 0 and isinstance(data[0], dict) and prefer_object:
                    return data[0]
                return data
        except Exception:
            pass

    # 4. Truncation boundary healing
    last_b = cleaned.rfind("}")
    if last_b != -1:
        candidate = cleaned[:last_b + 1]
        open_brackets = candidate.count("[") - candidate.count("]")
        open_braces = candidate.count("{") - candidate.count("}")
        repaired = candidate + ("]" * max(0, open_brackets)) + ("}" * max(0, open_braces))
        try:
            data = json.loads(repaired)
            if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict) and prefer_object:
                return data[0]
            if isinstance(data, (dict, list)):
                return data
        except Exception:
            pass

    return None


def _extract_text_from_response(resp) -> str:
    """Extracts raw text from tool calls, content, or reasoning fields."""
    if not resp or not getattr(resp, "choices", None) or len(resp.choices) == 0:
        return ""

    choice = resp.choices[0]
    msg = getattr(choice, "message", None)
    if not msg:
        return ""

    # 1. Tool call arguments
    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls and len(tool_calls) > 0:
        fn = getattr(tool_calls[0], "function", None)
        if fn and getattr(fn, "arguments", None):
            return fn.arguments.strip()

    # 2. Primary content
    content = getattr(msg, "content", None)
    if content and isinstance(content, str) and content.strip():
        return content.strip()

    # 3. Reasoning / Thought fields (if JSON is embedded in thought output)
    for field in ("reasoning", "reasoning_content", "thought"):
        val = getattr(msg, field, None)
        if val and isinstance(val, str) and ("{" in val or "[" in val):
            return val.strip()

    # 4. Refusal
    if getattr(msg, "refusal", None):
        raise ValueError(f"Model refused request: {msg.refusal}")

    return ""


class UniversalLLMGateway:
    def __init__(self, provider: str, api_key: str, model: str, base_url: str = None, reasoning_effort: str = None):
        self.provider = (provider or "openai").lower().strip()
        self.api_key = (api_key or "").strip()
        self.model = (model or "").strip()
        self.reasoning_effort = (reasoning_effort or "").lower().strip() or None

        # Resolve base URL cleanly
        resolved_url = base_url.strip() if base_url and base_url.strip() else None
        if not resolved_url:
            resolved_url = SUPPORTED_PROVIDERS.get(self.provider, {}).get("base_url")

        if resolved_url:
            resolved_url = resolved_url.rstrip("/")
            if resolved_url.endswith("/chat/completions"):
                resolved_url = resolved_url[:-len("/chat/completions")]

        self.base_url = resolved_url

    def _is_reasoning_model(self) -> bool:
        """Determines if the target model is an OpenAI/DeepSeek reasoning model."""
        m = self.model.lower()
        return any(tag in m for tag in ("o1", "o3", "o4", "reasoner", "deepseek-r1")) or bool(self.reasoning_effort and self.provider == "openai")

    def test_handshake(self) -> str:
        """Lightweight verification test against the user's selected model."""
        if self.provider == "anthropic":
            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            }
            payload = {
                "model": self.model,
                "max_tokens": 10,
                "messages": [{"role": "user", "content": "ping"}]
            }
            with httpx.Client(timeout=30.0) as client:
                r = client.post("https://api.anthropic.com/v1/messages", json=payload, headers=headers)
                if r.status_code != 200:
                    raise ValueError(f"Anthropic API Error ({r.status_code}): {r.text}")
                return "OK"
        else:
            client = self._build_openai_client()
            kwargs = {
                "model": self.model,
                "messages": [{"role": "user", "content": "ping"}],
            }
            if self._is_reasoning_model():
                kwargs["max_completion_tokens"] = 10
            else:
                kwargs["max_tokens"] = 10
                kwargs["temperature"] = 0.0

            client.chat.completions.create(**kwargs)
            return "OK"

    def _build_openai_client(self) -> OpenAI:
        default_headers = {}
        if self.provider == "openrouter" or (self.base_url and "openrouter" in self.base_url):
            default_headers = {
                "HTTP-Referer": "http://localhost:8000",
                "X-Title": "UnderwriteIQ Engine",
            }

        return OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            default_headers=default_headers,
            timeout=180.0,
        )

    def call_structured(self, system_prompt: str, user_content: list, schema: dict, schema_name: str = "output_schema") -> tuple[dict | list, dict]:
        """Calls the user's exact chosen model with structured output parsing."""
        if self.provider == "anthropic":
            return self._call_anthropic(system_prompt, user_content, schema, schema_name)
        else:
            return self._call_openai_compatible(system_prompt, user_content, schema, schema_name)

    def _call_openai_compatible(self, system_prompt: str, user_content: list, schema: dict, schema_name: str) -> tuple[dict | list, dict]:
        client = self._build_openai_client()
        is_reasoning = self._is_reasoning_model()

        openai_content = []
        for part in user_content:
            p_type = part.get("type")
            if p_type in ("input_text", "text"):
                openai_content.append({"type": "text", "text": part.get("text", "")})
            elif p_type in ("input_image", "image"):
                openai_content.append({
                    "type": "image_url",
                    "image_url": {"url": part.get("image_url", ""), "detail": part.get("detail", "high")}
                })

        user_msg = openai_content[0]["text"] if (len(openai_content) == 1 and openai_content[0]["type"] == "text") else openai_content

        # Handle system prompt role
        system_role = "system"
        if is_reasoning and ("o1-mini" in self.model.lower() or "o1-preview" in self.model.lower()):
            system_role = "developer"

        messages = [
            {"role": system_role, "content": system_prompt},
            {"role": "user", "content": user_msg}
        ]

        # Base kwargs
        base_kwargs = {
            "model": self.model,
            "messages": messages,
        }

        # Temperature handling: reasoning models reject temperature parameter
        if not is_reasoning:
            base_kwargs["temperature"] = 0.0

        # Effort parameter handling
        if self.reasoning_effort:
            if self.provider == "openai":
                base_kwargs["reasoning_effort"] = self.reasoning_effort
            elif self.provider == "openrouter":
                base_kwargs["extra_body"] = {"reasoning": {"effort": self.reasoning_effort}}

        # Token limit handling
        if is_reasoning:
            base_kwargs["max_completion_tokens"] = 16384

        resp = None
        parsed_data = None
        is_obj_schema = schema.get("type") == "object"

        # Mode 1: Strict JSON Schema (Standard for OpenAI gpt-4o, gpt-5.6 series)
        try:
            mode1_kwargs = dict(base_kwargs)
            mode1_kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema}
            }
            resp = client.chat.completions.create(**mode1_kwargs)
            raw = _extract_text_from_response(resp)
            parsed_data = _find_json_in_text(raw, prefer_object=is_obj_schema)
        except Exception as e:
            logger.debug(f"Mode 1 json_schema failed ({e}), falling back to Mode 2...")

        # Mode 2: Tool / Function Calling (Standard for models that support tools)
        if not parsed_data:
            try:
                tools = [{
                    "type": "function",
                    "function": {
                        "name": schema_name,
                        "description": "Output the structured data matching this schema.",
                        "parameters": schema
                    }
                }]
                mode2_kwargs = dict(base_kwargs)
                mode2_kwargs["tools"] = tools
                mode2_kwargs["tool_choice"] = {"type": "function", "function": {"name": schema_name}}
                resp = client.chat.completions.create(**mode2_kwargs)
                raw = _extract_text_from_response(resp)
                parsed_data = _find_json_in_text(raw, prefer_object=is_obj_schema)
            except Exception as e:
                logger.debug(f"Mode 2 tool_choice failed ({e}), falling back to Mode 3...")

        # Mode 3: JSON Object Mode with Schema in prompt
        if not parsed_data:
            try:
                schema_prompt = f"\n\nOUTPUT FORMAT SPECIFICATION:\nReturn ONLY a valid JSON object matching this schema:\n{json.dumps(schema)}"
                retry_messages = [
                    {"role": system_role, "content": system_prompt + schema_prompt},
                    {"role": "user", "content": user_msg}
                ]
                mode3_kwargs = dict(base_kwargs)
                mode3_kwargs["messages"] = retry_messages
                mode3_kwargs["response_format"] = {"type": "json_object"}
                resp = client.chat.completions.create(**mode3_kwargs)
                raw = _extract_text_from_response(resp)
                parsed_data = _find_json_in_text(raw, prefer_object=is_obj_schema)
            except Exception as e:
                logger.debug(f"Mode 3 json_object failed ({e}), falling back to Mode 4...")

        # Mode 4: Plain text completion with prompt instruction
        if not parsed_data:
            try:
                schema_prompt = f"\n\nOUTPUT FORMAT SPECIFICATION:\nReturn raw JSON matching this schema:\n{json.dumps(schema)}"
                plain_messages = [
                    {"role": system_role, "content": system_prompt + schema_prompt},
                    {"role": "user", "content": user_msg}
                ]
                mode4_kwargs = dict(base_kwargs)
                mode4_kwargs["messages"] = plain_messages
                resp = client.chat.completions.create(**mode4_kwargs)
                raw = _extract_text_from_response(resp)
                parsed_data = _find_json_in_text(raw, prefer_object=is_obj_schema)
            except Exception as e:
                logger.error(f"Mode 4 text fallback failed: {e}")

        if not parsed_data:
            raw_preview = getattr(resp.choices[0].message, "content", "") if resp and resp.choices else "Empty response"
            raise ValueError(f"Model '{self.model}' did not return valid JSON for {schema_name}. Preview:\n{str(raw_preview)[:300]}")

        # Normalization guard: ensure object schemas return dicts
        if is_obj_schema and isinstance(parsed_data, list):
            if len(parsed_data) > 0 and isinstance(parsed_data[0], dict):
                parsed_data = parsed_data[0]
            else:
                parsed_data = {}

        usage = getattr(resp, "usage", None)
        in_tok = getattr(usage, "prompt_tokens", 0) or 0
        out_tok = getattr(usage, "completion_tokens", 0) or 0
        cached_tok = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0

        metrics = self._calc_cost(in_tok, cached_tok, out_tok)
        return parsed_data, metrics

    def _call_anthropic(self, system_prompt: str, user_content: list, schema: dict, schema_name: str) -> tuple[dict | list, dict]:
        anthropic_content = []
        for part in user_content:
            p_type = part.get("type")
            if p_type in ("input_text", "text"):
                anthropic_content.append({"type": "text", "text": part.get("text", "")})
            elif p_type in ("input_image", "image"):
                data_uri = part.get("image_url", "")
                if "," in data_uri:
                    header, b64_data = data_uri.split(",", 1)
                    media_type = header.split(";")[0].replace("data:", "")
                else:
                    media_type = "image/png"
                    b64_data = data_uri
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
            "system": system_prompt,
            "messages": [{"role": "user", "content": anthropic_content}],
            "tools": tools,
        }

        # Handle Anthropic thinking / reasoning configuration
        if self.reasoning_effort:
            budget_map = {"low": 1024, "medium": 2048, "high": 4096, "xhigh": 8192}
            budget = budget_map.get(self.reasoning_effort, 2048)
            payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
            payload["max_tokens"] = budget + 8192
            # When thinking is enabled, tool_choice cannot be forced {"type": "tool"}
            payload["tool_choice"] = {"type": "auto"}
        else:
            payload["max_tokens"] = 8192
            payload["temperature"] = 0.0
            payload["tool_choice"] = {"type": "tool", "name": schema_name}

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "output-128k-2025-02-19",
            "content-type": "application/json"
        }

        with httpx.Client(timeout=180.0) as client:
            resp = client.post("https://api.anthropic.com/v1/messages", json=payload, headers=headers)
            if resp.status_code != 200:
                raise ValueError(f"Anthropic API Error ({resp.status_code}): {resp.text}")
            res_data = resp.json()

        parsed = None
        is_obj_schema = schema.get("type") == "object"

        for content_block in res_data.get("content", []):
            b_type = content_block.get("type")
            if b_type == "tool_use" and content_block.get("name") == schema_name:
                parsed = content_block.get("input", {})
                break
            elif b_type == "text":
                text_parsed = _find_json_in_text(content_block.get("text", ""), prefer_object=is_obj_schema)
                if text_parsed:
                    parsed = text_parsed

        if parsed is None:
            parsed = {}

        if isinstance(parsed, list) and len(parsed) > 0 and isinstance(parsed[0], dict) and is_obj_schema:
            parsed = parsed[0]

        usage = res_data.get("usage", {})
        in_tok = usage.get("input_tokens", 0)
        out_tok = usage.get("output_tokens", 0)
        cached_tok = usage.get("cache_read_input_tokens", 0)

        metrics = self._calc_cost(in_tok, cached_tok, out_tok)
        return parsed, metrics

    def _calc_cost(self, in_tok: int, cached_tok: int, out_tok: int) -> dict:
        uncached_tok = max(in_tok - cached_tok, 0)
        price_cfg = {"in": 2.00, "cached": 0.20, "out": 10.00}
        for p in SUPPORTED_PROVIDERS.values():
            for m in p.get("models", []):
                if m["id"] == self.model:
                    price_cfg = {"in": m.get("in_price", 2.0), "cached": m.get("cached_price", 0.2), "out": m.get("out_price", 10.0)}
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
