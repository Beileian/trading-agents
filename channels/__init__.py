"""Channels: Declarative multi-backend routing for data/analysis engines.

Each module registers a preferred → fallback chain.
The channel router probes backends in order and selects the first healthy one.
Inspired by Agent Reach's channel/backend architecture.
"""
