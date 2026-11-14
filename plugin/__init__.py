"""In plain words: introduces Skilluminati to Hermes. It runs once, when Hermes starts: it adds the
/skilluminati command and two read-only tools the agent can use. It asks the model nothing itself.

Model calls go through the host-owned ``ctx.llm`` only, and only after you type
``/skilluminati <skill>`` or ``/skilluminati selftest`` (or press Ask the Owl on the Skilluminati page).
"""

from __future__ import annotations

import logging
from pathlib import Path

from .skilluminati import commands, schemas, tools

logger = logging.getLogger(__name__)

ARGS_HINT = "<skill> | last | stop | selftest"
#: The one small skill the plugin ships, so the agent knows to read a saved verdict when a user
#: asks whether a skill is worth keeping (Hermes keeps plugin tools behind tool search).
SKILL_MD = Path(__file__).parent / "skills" / "skilluminati" / "SKILL.md"
SKILL_DESCRIPTION = "Use when asked if a skill helps, is worth keeping or costs."


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
    if hasattr(ctx, "register_skill"):  # older Hermes releases have no plugin skills; skip quietly
        ctx.register_skill("skilluminati", SKILL_MD, description=SKILL_DESCRIPTION)
    logger.debug("skilluminati registered: /skilluminati, skilluminati_estimate, skilluminati_result, skill")
