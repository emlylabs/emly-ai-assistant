"""G2c: LangSmith tracing stays off unless the operator opts in.

``langsmith`` ships with LangChain. Setting ``LANGSMITH_TRACING`` (or one of
its aliases) to ``true`` makes every LLM call send its full prompt and
output, conversation text included, to LangSmith's cloud. An operator's
``.env`` is passed straight into the container, so a leftover debugging
variable would do this silently.

``disable_unapproved_tracing()`` runs at the top of ``main`` and ``config``,
before LangChain is imported. Unless ``ALLOW_LANGSMITH_TRACING=true``:

- with ``MODE=prod`` (or another production-like mode) and a tracing
  switch set to ``true``, it refuses to start, so the misconfiguration is
  fixed rather than silently overridden (Q-G2);
- otherwise it removes the tracing switches from the environment and logs
  a warning naming the variables (never their values).

Kept dependency-free so it can run before anything else is imported.
"""
from __future__ import annotations

import logging
import os
import sys

from utils.runtime import env_flag, is_prod_mode

log = logging.getLogger(__name__)

OPT_IN_ENV = "ALLOW_LANGSMITH_TRACING"
# Every name langsmith.utils.tracing_is_enabled() consults
# (get_env_var("TRACING_V2") / ("TRACING") under both namespaces).
TRACING_SWITCHES = ("LANGSMITH_TRACING_V2", "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING", "LANGCHAIN_TRACING")


def disable_unapproved_tracing() -> list[str]:
    """Remove tracing switches unless opted in. Returns the names that
    were asking for tracing (empty when nothing was removed or the
    operator opted in)."""
    if env_flag(OPT_IN_ENV, default=False):
        return []
    requested = [n for n in TRACING_SWITCHES if (os.environ.get(n) or "").strip().lower() == "true"]
    if requested and is_prod_mode():
        raise RuntimeError(
            f"{', '.join(requested)} is set, which would send every prompt and reply to LangSmith's "
            f"cloud. Remove it, or set {OPT_IN_ENV}=true if that is intended."
        )
    for name in TRACING_SWITCHES:
        os.environ.pop(name, None)
    # langsmith caches env lookups; drop anything read before this ran.
    utils = sys.modules.get("langsmith.utils")
    if utils is not None and hasattr(utils.get_env_var, "cache_clear"):
        utils.get_env_var.cache_clear()
    if requested:
        log.warning(
            "%s set, but LangSmith tracing would send every prompt and reply to LangSmith's cloud. "
            "Tracing is OFF. Set %s=true to allow it.",
            ", ".join(requested), OPT_IN_ENV,
        )
    return requested
