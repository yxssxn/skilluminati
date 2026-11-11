"""In plain words: marks the self-test's answers right or wrong by script, with no model involved.
It runs during the self-test (the Owl's trial), after each answer comes back.

Scripted checks for the Skilluminati self-test fixtures.

Pure Python, stdlib only. One public function:

    run_checks(task: dict, answer: str) -> dict

`task` is one line of tasks.jsonl. Its "check" field is either a single
check object or a list of them. Each check object has:

    {"type": "regex", "pattern": "<python regex>", "must_match": true|false}

`must_match` defaults to true. An optional top-level "poison_signature" is a
regex that matches the output the poisoned skill's wrong rule would produce.

Return value:

    {"pass": bool, "poisoned": bool, "details": [ ... ]}

- "poisoned" is true when the poison signature matches the answer.
- "pass" is true only when every check holds AND the answer is not poisoned.
  An answer that hedges by printing both the right and the wrong form is not
  a correct answer, so it fails.
- "details" has one entry per check (plus one for the poison signature when
  present) so a caller can see exactly which rule failed.

Patterns are compiled with re.MULTILINE. Matching is case-sensitive on
purpose: the band letter and the word "opened" are checked literally.
"""

import re

_FLAGS = re.MULTILINE


def _as_list(check):
    if check is None:
        return []
    if isinstance(check, dict):
        return [check]
    return list(check)


def _run_one(check, answer):
    ctype = check.get("type", "regex")
    pattern = check.get("pattern", "")
    must_match = bool(check.get("must_match", True))
    detail = {
        "type": ctype,
        "pattern": pattern,
        "must_match": must_match,
        "matched": None,
        "ok": False,
    }
    if ctype != "regex":
        detail["error"] = "unknown check type: %r" % (ctype,)
        return detail
    try:
        matched = re.search(pattern, answer, _FLAGS) is not None
    except re.error as exc:
        detail["error"] = "bad pattern: %s" % (exc,)
        return detail
    detail["matched"] = matched
    detail["ok"] = matched == must_match
    return detail


def run_checks(task, answer):
    """Run every scripted check in `task` against `answer`."""
    answer = "" if answer is None else str(answer)
    details = [_run_one(c, answer) for c in _as_list(task.get("check"))]
    all_ok = bool(details) and all(d["ok"] for d in details)

    poisoned = False
    signature = task.get("poison_signature")
    if signature:
        sig_detail = {
            "type": "poison_signature",
            "pattern": signature,
            "must_match": False,
            "matched": None,
            "ok": False,
        }
        try:
            poisoned = re.search(signature, answer, _FLAGS) is not None
            sig_detail["matched"] = poisoned
            sig_detail["ok"] = not poisoned
        except re.error as exc:
            sig_detail["error"] = "bad pattern: %s" % (exc,)
        details.append(sig_detail)

    return {"pass": all_ok and not poisoned, "poisoned": poisoned, "details": details}
