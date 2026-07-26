'''Shared configuration for the oe2d package.'''
from __future__ import annotations

# Fireworks' Kimi K2 drives the RLM code-writing. Hardcoded — no per-run model
# override needed. litellm reads FIREWORKS_AI_API_KEY (supplied by the repo .env).
TASK_LM = 'fireworks_ai/accounts/fireworks/models/kimi-k2p7-code'
