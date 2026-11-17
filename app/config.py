"""
Provider Registry, Model Configurations & Token Pricing
High-end paid frontier models (GPT-5.6 and Claude 5.5 series)
"""

SUPPORTED_PROVIDERS = {
    "openai": {
        "label": "OpenAI",
        "default_model": "gpt-5.6-terra",
        "base_url": "https://api.openai.com/v1",
        "models": [
            {"id": "gpt-5.6-terra", "label": "GPT-5.6 Terra (Flagship Underwriting & High Reasoning)", "vision": True, "effort_levels": ["low", "medium", "high", "xhigh"], "in_price": 2.00, "cached_price": 0.20, "out_price": 12.00},
            {"id": "gpt-5.6-astra", "label": "GPT-5.6 Astra (Frontier Maximum Capability)", "vision": True, "effort_levels": ["low", "medium", "high", "xhigh"], "in_price": 10.00, "cached_price": 1.00, "out_price": 50.00},
            {"id": "gpt-5.6-sol", "label": "GPT-5.6 Sol (Deep Financial Analytics)", "vision": True, "effort_levels": ["low", "medium", "high"], "in_price": 3.00, "cached_price": 0.30, "out_price": 15.00},
            {"id": "gpt-5.6", "label": "GPT-5.6 (Frontier Multimodal)", "vision": True, "effort_levels": ["low", "medium", "high"], "in_price": 2.50, "cached_price": 0.25, "out_price": 12.50},
            {"id": "gpt-5.5", "label": "GPT-5.5 (High Performance Vision)", "vision": True, "effort_levels": ["low", "medium", "high"], "in_price": 2.00, "cached_price": 0.20, "out_price": 10.00},
            {"id": "o3-high", "label": "OpenAI o3 High (Deep Multi-Step Reasoning)", "vision": True, "effort_levels": ["low", "medium", "high"], "in_price": 5.00, "cached_price": 0.50, "out_price": 25.00},
            {"id": "o3-mini", "label": "OpenAI o3-mini (High-Speed Reasoning)", "vision": False, "effort_levels": ["low", "medium", "high"], "in_price": 1.10, "cached_price": 0.55, "out_price": 4.40},
            {"id": "o1", "label": "OpenAI o1 (Frontier Reasoning)", "vision": True, "effort_levels": ["low", "medium", "high"], "in_price": 15.00, "cached_price": 7.50, "out_price": 60.00},
            {"id": "gpt-4o", "label": "GPT-4o (Omni Vision)", "vision": True, "effort_levels": [], "in_price": 2.50, "cached_price": 1.25, "out_price": 10.00},
        ],
    },
    "anthropic": {
        "label": "Anthropic (Claude)",
        "default_model": "claude-sonnet-5-5",
        "base_url": "https://api.anthropic.com/v1",
        "models": [
            {"id": "claude-sonnet-5-5", "label": "Claude Sonnet 5.5 (Flagship Underwriting & Adaptive Reasoning)", "vision": True, "effort_levels": ["low", "medium", "high", "xhigh"], "in_price": 3.00, "cached_price": 0.30, "out_price": 15.00},
            {"id": "claude-opus-5-5", "label": "Claude Opus 5.5 (Frontier Deep Reasoning)", "vision": True, "effort_levels": ["low", "medium", "high", "xhigh"], "in_price": 15.00, "cached_price": 1.50, "out_price": 75.00},
            {"id": "claude-sonnet-5", "label": "Claude Sonnet 5 (High Capability Vision)", "vision": True, "effort_levels": ["low", "medium", "high"], "in_price": 3.00, "cached_price": 0.30, "out_price": 15.00},
            {"id": "claude-opus-5", "label": "Claude Opus 5 (Deep Reasoning)", "vision": True, "effort_levels": ["low", "medium", "high"], "in_price": 15.00, "cached_price": 1.50, "out_price": 75.00},
            {"id": "claude-3-7-sonnet-20250219", "label": "Claude 3.7 Sonnet (Hybrid Vision & Reasoning)", "vision": True, "effort_levels": ["low", "medium", "high"], "in_price": 3.00, "cached_price": 0.30, "out_price": 15.00},
            {"id": "claude-3-5-sonnet-20241022", "label": "Claude 3.5 Sonnet (Production Vision)", "vision": True, "effort_levels": [], "in_price": 3.00, "cached_price": 0.30, "out_price": 15.00},
            {"id": "claude-3-opus-20240229", "label": "Claude 3 Opus (Complex Financial Analysis)", "vision": True, "effort_levels": [], "in_price": 15.00, "cached_price": 1.50, "out_price": 75.00},
        ],
    },
    "openrouter": {
        "label": "OpenRouter",
        "default_model": "openai/gpt-5.6-terra",
        "base_url": "https://openrouter.ai/api/v1",
        "models": [
            {"id": "openai/gpt-5.6-terra", "label": "OpenAI: GPT-5.6 Terra (High Reasoning Vision)", "vision": True, "effort_levels": ["low", "medium", "high", "xhigh"], "in_price": 2.00, "cached_price": 0.20, "out_price": 12.00},
            {"id": "openai/gpt-5.6", "label": "OpenAI: GPT-5.6 Flagship", "vision": True, "effort_levels": ["low", "medium", "high"], "in_price": 2.50, "cached_price": 0.25, "out_price": 12.50},
            {"id": "anthropic/claude-5.5-sonnet", "label": "Anthropic: Claude Sonnet 5.5", "vision": True, "effort_levels": ["low", "medium", "high"], "in_price": 3.00, "cached_price": 0.30, "out_price": 15.00},
            {"id": "anthropic/claude-5.5-opus", "label": "Anthropic: Claude Opus 5.5", "vision": True, "effort_levels": ["low", "medium", "high"], "in_price": 15.00, "cached_price": 1.50, "out_price": 75.00},
            {"id": "anthropic/claude-3.7-sonnet", "label": "Anthropic: Claude 3.7 Sonnet", "vision": True, "effort_levels": ["low", "medium", "high"], "in_price": 3.00, "cached_price": 0.30, "out_price": 15.00},
            {"id": "anthropic/claude-3.5-sonnet", "label": "Anthropic: Claude 3.5 Sonnet", "vision": True, "effort_levels": [], "in_price": 3.00, "cached_price": 0.30, "out_price": 15.00},
            {"id": "openai/o3-mini", "label": "OpenAI: o3-mini Reasoning", "vision": False, "effort_levels": ["low", "medium", "high"], "in_price": 1.10, "cached_price": 0.55, "out_price": 4.40},
            {"id": "openai/o1", "label": "OpenAI: o1 Frontier Reasoning", "vision": True, "effort_levels": ["low", "medium", "high"], "in_price": 15.00, "cached_price": 7.50, "out_price": 60.00},
        ],
    },
    "deepseek": {
        "label": "DeepSeek (Direct)",
        "default_model": "deepseek-chat",
        "base_url": "https://api.deepseek.com",
        "models": [
            {"id": "deepseek-chat", "label": "DeepSeek V3 (Direct API)", "vision": False, "effort_levels": [], "in_price": 0.14, "cached_price": 0.014, "out_price": 0.28},
            {"id": "deepseek-reasoner", "label": "DeepSeek R1 (Reasoning Direct)", "vision": False, "effort_levels": [], "in_price": 0.55, "cached_price": 0.14, "out_price": 2.19},
        ],
    },
    "custom": {
        "label": "Custom (Self-Hosted / Proxy)",
        "default_model": "custom-model",
        "base_url": "https://openrouter.ai/api/v1",
        "models": [],
    },
}

IMAGE_DPI = 150
IMAGE_DETAIL = "high"
RECONCILIATION_TOLERANCE = 0.01
