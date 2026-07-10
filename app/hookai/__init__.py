"""Hook AI — internal agent plane.

An orchestrator routing to six specialist agents over one canonical Profile.
Only two specialists take outward actions, and both pass an approval gate.
LLM calls go through an OpenAI-compatible client pointed at OpenRouter.

See ../docs/architecture.md for the design this implements.
"""

__version__ = "0.1.0"
