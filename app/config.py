"""
Provider Registry, Model Configurations & Token Pricing
"""

SUPPORTED_PROVIDERS = {
    "openai": {
        "label": "OpenAI",
        "default_model": "gpt-6.1-terra",
        "base_url": "https://api.openai.com/v1",
        "models": [
            {"id": "gpt-6.1-terra", "label": "GPT-6.1 Terra (Recommended)", "vision": True, "in_price": 1.00, "cached_price": 0.05, "out_price": 5.00},
            {"id": "gpt-6-luna", "label": "GPT-6 Luna (Fast & economical)", "vision": True, "in_price": 0.10, "cached_price": 0.01, "out_price": 0.50},
            {"id": "gpt-6.1-sol", "label": "GPT-6.1 Sol (Advanced reasoning)", "vision": True, "in_price": 2.00, "cached_price": 0.10, "out_price": 10.00},
            {"id": "gpt-6-astra", "label": "GPT-6 Astra (Highest capability)", "vision": True, "in_price": 10.00, "cached_price": 1.00, "out_price": 50.00},
        ],
    },
    "anthropic": {
        "label": "Anthropic (Claude)",
        "default_model": "claude-sonnet-5",
        "base_url": "https://api.anthropic.com/v1",
        "models": [
            {"id": "claude-sonnet-5", "label": "Claude Sonnet 5 (Recommended)", "vision": True, "in_price": 3.00, "cached_price": 0.30, "out_price": 15.00},
            {"id": "claude-opus-5", "label": "Claude Opus 5 (Highest capability)", "vision": True, "in_price": 15.00, "cached_price": 1.50, "out_price": 75.00},
            {"id": "claude-haiku-4-5-20251001", "label": "Claude Haiku 4.5 (Fast)", "vision": True, "in_price": 1.00, "cached_price": 0.10, "out_price": 5.00},
        ],
    },
    "openrouter": {
        "label": "OpenRouter",
        "default_model": "openrouter/auto",
        "base_url": "https://openrouter.ai/api/v1",
        "models": [
            {"id": "openrouter/auto", "label": "OpenRouter Auto (dynamic routing)", "vision": True, "in_price": 0.0, "cached_price": 0.0, "out_price": 0.0},
            {"id": "~anthropic/claude-sonnet-latest", "label": "Claude Sonnet Latest (dynamic alias)", "vision": True, "in_price": 0.0, "cached_price": 0.0, "out_price": 0.0},
            {"id": "~google/gemini-flash-latest", "label": "Gemini Flash Latest (dynamic alias)", "vision": True, "in_price": 0.0, "cached_price": 0.0, "out_price": 0.0},
            {"id": "nvidia/nemotron-3.5-super-vl:free", "label": "NVIDIA Nemotron 3.5 Super VL (Free 1M Vision)", "vision": True, "in_price": 0.0, "cached_price": 0.0, "out_price": 0.0},
            {"id": "nvidia/nemotron-3-ultra-550b-a55b:free", "label": "NVIDIA Nemotron 3 Ultra (Free 1M Text)", "vision": False, "in_price": 0.0, "cached_price": 0.0, "out_price": 0.0},
            {"id": "qwen/qwen-2.5-vl-72b-instruct:free", "label": "Qwen 2.5 VL 72B (Free Vision)", "vision": True, "in_price": 0.0, "cached_price": 0.0, "out_price": 0.0},
            {"id": "deepseek/deepseek-r1:free", "label": "DeepSeek R1 (Free Reasoning)", "vision": False, "in_price": 0.0, "cached_price": 0.0, "out_price": 0.0},
        ],
    },
    "deepseek": {
        "label": "DeepSeek (Direct)",
        "default_model": "deepseek-chat",
        "base_url": "https://api.deepseek.com",
        "models": [
            {"id": "deepseek-chat", "label": "DeepSeek-V3", "vision": False, "in_price": 0.27, "cached_price": 0.07, "out_price": 1.10},
            {"id": "deepseek-reasoner", "label": "DeepSeek-R1", "vision": False, "in_price": 0.55, "cached_price": 0.14, "out_price": 2.19},
        ],
    },
    "custom": {
        "label": "Custom (Self-Hosted / Proxy)",
        "default_model": "custom-model",
        "base_url": "https://openrouter.ai/api/v1",
        "models": [],
    },
}

IMAGE_DPI = 300
IMAGE_DETAIL = "high"
RECONCILIATION_TOLERANCE = 0.01
