"""In plain words: introduces Skilluminati to Hermes. It runs once, when Hermes starts: it adds the
/skilluminati command and two read-only tools the agent can use. It asks the model nothing itself.

Model calls go through the host-owned ``ctx.llm`` only, and only after you type
``/skilluminati <skill>`` or ``/skilluminati selftest`` (or press Ask the Owl on the Skilluminati page).
"""

from __future__ import annotations

import logging

from .skilluminati import commands, schemas, tools

logger = logging.getLogger(__name__)

ARGS_HINT = "<skill> | stop | selftest"


def register(ctx) -> None:
    """Wire the slash command and the tool. Called once at plugin load. Makes no model calls."""
    commands.bind(ctx)
    ctx.register_command(
        "skilluminati",
        handler=commands.handle_skilluminati,
        description="Test whether a skill still earns its keep on your current model (Skilluminati)",
        args_hint=ARGS_HINT,
    )
    ctx.register_tool(
        name="skilluminati_estimate",
        toolset="skilluminati",
        schema=schemas.ESTIMATE,
        handler=tools.estimate,
        description="Estimate the prompt-token cost each installed skill adds (Skilluminati)",
    )
    ctx.register_tool(
        name="skilluminati_result",
        toolset="skilluminati",
        schema=schemas.RESULT,
        handler=tools.result,
        description="Show the report of a finished Skilluminati run, read from disk (Skilluminati)",
    )
    logger.debug("skilluminati registered: /skilluminati, skilluminati_estimate, skilluminati_result")
