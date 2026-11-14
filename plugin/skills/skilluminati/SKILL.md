---
name: skilluminati
description: "Use when asked if a skill helps, is worth keeping or costs."
---

# Skilluminati: answer "does this skill help?" from a real test

Skilluminati has tested skills on this machine by redoing past work with and without the skill and having a blind judge (the Owl) pick the better answer. Use its saved results instead of guessing from load counts or file sizes.

1. **Whether a skill helps or hurts:** call the `skilluminati_result` tool with `skill` set to the skill's name. If tools are behind tool search, find it with the query `skilluminati result`. Show the report exactly as returned. Do not add a verdict of your own.
2. **What a skill costs in tokens:** call `skilluminati_estimate` (optionally with `skill` or `top`).
3. **No result yet for that skill:** say so, and tell the user they can test it with `/skilluminati <skill>`. A test makes model calls, so only the user starts it. Never start one yourself.

Both tools are read-only and make no model calls.
