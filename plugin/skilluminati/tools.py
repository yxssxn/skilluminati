"""In plain words: the two tools the agent can call. skilluminati_result shows the last finished test
(and says if one is still going); skilluminati_estimate shows what each skill costs in prompt
tokens. They run when the agent calls them, and always answer, never crash."""

from __future__ import annotations

import json

from . import cost

def result(args: dict, **kwargs) -> str:
    """``skilluminati_result``: the finished run's report (markdown), from the run folder on disk."""
    try:
        from . import report, run
        args = args or {}
        skill = str(args.get("skill") or "").strip() or None
        run_id = str(args.get("run") or "").strip() or None
        going = run.active_status()
        lines = []
        if going:
            if going["cap"]:
                lines.append(f"A Skilluminati {going['label']} is still going: {going['used']} of {going['cap']} calls so far.")
            else:
                lines.append(f"A Skilluminati {going['label']} is starting: reading your past uses of the skill.")
        folder = report.find_run(skill=skill, run=run_id)
        if folder is None:
            what = f"run {run_id!r}" if run_id else (f"Skilluminati run for {skill!r}" if skill else "Skilluminati run")
            lines.append(f"No finished {what} was found." if run_id or skill
                         else "No finished Skilluminati run yet. Start one with /skilluminati <skill> or /skilluminati selftest.")
            return json.dumps({"success": True, "found": False, "report": " ".join(lines)})
        text = report.saved_or_built(folder)
        if text is None:
            return json.dumps({"success": False, "error": f"the result in run {folder.name} could not be read"})
        if going:
            lines.append("The last finished result:")
        lines.append(text)
        return json.dumps({"success": True, "found": True, "run": folder.name, "kind": report.run_kind(folder.name),
                           "report": "\n\n".join(lines),
                           "instructions": "Show the report as it is. Add no verdict of your own."})
    except Exception as exc:  # handlers must not raise
        try:
            return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})
        except Exception:
            return '{"success": false, "error": "unserialisable error"}'


def estimate(args: dict, **kwargs) -> str:
    """``skilluminati_estimate``: per-skill prompt-token cost (estimate, chars/4, list price)."""
    try:
        args = args or {}
        model = str(args.get("model") or "").strip() or None
        top = args.get("top")
        result = cost.estimate(model=model, top=top)
        skill = str(args.get("skill") or "").strip()
        if skill:
            result["skills"] = [r for r in result["skills"] if r["name"] == skill]
            if not result["skills"]:
                result["notes"].append(f"skill {skill!r} not found among enabled skills")
        return json.dumps({"success": True, **result})
    except Exception as exc:  # handlers must not raise
        try:
            return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})
        except Exception:
            return '{"success": false, "error": "unserialisable error"}'
