"""In plain words: the short descriptions the agent reads to decide when to call Skilluminati's two
tools. Hermes reads them once, when the plugin loads.

Tool schemas: what the model reads to decide when to call skilluminati tools."""

ESTIMATE = {
    "name": "skilluminati_estimate",
    "description": (
        "Token cost of each installed skill, per turn and per load. "
        "Skilluminati counts the prompt tokens each skill adds to every turn (its index line) and "
        "each time it is loaded (its SKILL.md), converted to an approximate cost. Use when the "
        "user asks which skills are expensive, cost the most to keep loaded, or wants to trim "
        "their skill set. Read-only, no model calls; numbers are estimates (chars/4, list input "
        "price). Whether a skill actually helps is skilluminati_result."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "skill": {
                "type": "string",
                "description": "Optional skill name to estimate. Omit for all installed skills.",
            },
            "model": {
                "type": "string",
                "description": "Model id to price at, e.g. claude-opus-5-5. Unknown models give cost 'unknown'.",
            },
            "top": {
                "type": "integer",
                "description": "Return only the N skills with the largest index-line cost.",
                "minimum": 1,
            },
        },
        "required": [],
    },
}

RESULT = {
    "name": "skilluminati_result",
    "description": (
        "Skilluminati's tested verdict on whether a skill helps or hurts your answers. "
        "Returns the saved report of a finished Skilluminati run (a skill verdict or the "
        "self-test) as markdown, read from disk. Call it when the user asks whether a skill is "
        "worth keeping, pulling its weight, helping or hurting, what Skilluminati said about a "
        "skill, or how the self-test went. Show the report to the user exactly as returned, with "
        "its table. Do not add a verdict, score or advice of your own. If no run exists for the "
        "skill, say so and offer /skilluminati <skill> to test it. Read-only, no model calls. "
        "For what a skill costs in tokens, use skilluminati_estimate."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "skill": {
                "type": "string",
                "description": "Optional skill name, or 'selftest'. Omit for the most recent finished run.",
            },
            "run": {
                "type": "string",
                "description": "Optional run id (the run folder name). Overrides skill.",
            },
        },
        "required": [],
    },
}
