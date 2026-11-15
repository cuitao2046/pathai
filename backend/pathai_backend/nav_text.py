"""Safety-critical route text is rendered by deterministic route templates.

Keep this compatibility entry point for callers migrating from LLM rewriting.
Intent understanding may still use the LLM; executable navigation text may not.
"""
from __future__ import annotations

from .config import Settings
from .map_repository import PublishedMap
from .schemas.nav import RouteInfo


def rewrite_route_texts(
    route: RouteInfo,
    published: PublishedMap,
    settings: Settings,
    destination_name: str,
    llm: object | None = None,
    dictionary: object | None = None,
) -> RouteInfo:
    """Return the original route, preserving directions, negation and hazards.

    Numeric/keyword checks cannot prove semantic equivalence. Do not submit
    actionable instructions to a model or copy model text into the route.
    """
    return route
