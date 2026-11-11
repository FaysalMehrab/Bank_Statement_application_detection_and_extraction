"""
Provider Registry, Model Configurations & Token Pricing
"""

SUPPORTED_PROVIDERS = {
    "openai": {
        "label": "OpenAI",
        "default_model": "gpt-4o",
        "base_url": "https://api.openai.com/v1",
        "models": [
            {"id": "gpt-4o", "label": "GPT-4o (Omni - Recommended)", "vision": True, "in_price": 2.50, "cached_price": 1.25, "out_price": 10.00},
            {"id": "gpt-4o-mini", "label": "GPT-4o Mini (Fast & Low Cost)", "vision": True, "in_price": 0.15, "cached_price": 0.075, "out_price": 0.60},
            {"id": "o3-mini", "label": "o3-mini (High Reasoning)", "vision": False, "in_price": 1.10, "cached_price": 0.55, "out_price": 4.40},
            {"id": "gpt-5.6-terra", "label": "GPT-5.6 Terra (Internal Benchmark)", "vision": True, "in_price": 2.00, "cached_price": 0.20, "out_price": 12.00},
        ],
    },
    "anthropic": {
        "label": "Anthropic (Claude)",
        "default_model": "claude-3-7-sonnet-20250219",
        "base_url": "https://api.anthropic.com/v1",
        "models": [
            {"id": "claude-3-7-sonnet-20250219", "label": "Claude 3.7 Sonnet (Hybrid Reasoning)", "vision": True, "in_price": 3.00, "cached_price": 0.30, "out_price": 15.00},
            {"id": "claude-3-5-sonnet-20241022", "label": "Claude 3.5 Sonnet v2", "vision": True, "in_price": 3.00, "cached_price": 0.30, "out_price": 15.00},
            {"id": "claude-3-5-haiku-20241022", "label": "Claude 3.5 Haiku", "vision": False, "in_price": 0.80, "cached_price": 0.08, "out_price": 4.00},
        ],
    },
    "openrouter": {
        "label": "OpenRouter",
        "default_model": "meta-llama/llama-3.3-70b-instruct:free",
        "base_url": "https://openrouter.ai/api/v1",
        "models": [
            {"id": "meta-llama/llama-3.3-70b-instruct:free", "label": "Meta Llama 3.3 70B (Free)", "vision": False, "in_price": 0.0, "cached_price": 0.0, "out_price": 0.0},
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
