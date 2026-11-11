"""In plain words: runs the self-test's planted skills with and without, and tallies the Owl's picks.
It runs only during the self-test; real skills go through realrun.py instead.

Planted-skill runner: planted skills x models x frozen tasks, WITH vs WITHOUT, plus A/A.

Thin glue over the pieces that already exist: ``replay`` builds the arms, ``llm``
makes the pinned calls, ``judge`` judges blind in both orders, ``stats`` turns counts into
verdicts, and ``fixtures/checks.py`` scores answers. The LLM facade is injected, so unit tests
run the whole thing on a fake with no network.

Task outcome rule (fixed before any call is made):
    if exactly one answer passes the scripted check, that answer wins (scripted override);
    if both pass or neither passes, the blind judge decides (both orders must agree, else tie).
Both the scripted and the judge outcome are recorded separately for every pair.

Budget rule: the planned call count is computed and printed before the first call; if it
exceeds the hard cap the run aborts without calling. Every call decrements the budget; a call
that would exceed the cap raises ``CapExceeded`` and the run stops with partial results. A
usage-limit or rate-limit error stops the run the same way, with the error kept verbatim.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from . import judge as sb_judge
from . import llm as sb_llm
from . import paths as sb_paths
from . import replay
from . import stats

HARD_CAP = 300
SKILL_NAME = "tallowgate-ledger"
#: Used only by the private dev harness; product runs use your active provider.
SUB_PROVIDER = "claude-subscription-directsdk-experimental"
HAIKU = "claude-haiku-5-5"
OPUS = "claude-opus-5-5"

#: Three decoy index rows so the planted skill is one entry among several, as in a real index.
DECOYS = (
    ("git", "Git workflows and conventions for this team."),
    ("release-notes", "Use when drafting release notes from merged pull requests."),
    ("oncall-rota", "Use when asked who is on call or how to page the duty engineer."),
)

# One stop hierarchy for the whole plugin (``llm.RunStop``); re-exported here for older callers.
USAGE_LIMIT_RE = sb_llm.USAGE_LIMIT_RE
CapExceeded = sb_llm.CapExceeded
UsageLimit = sb_llm.UsageLimit


# ── system prompt ────────────────────────────────────────────────────────────

def build_system_prompt(skill_name: str, skill_description: str, decoys=DECOYS) -> str:
    """A small Hermes-shaped system prompt with a skills index: the planted skill plus decoys."""
    rows = [(skill_name, skill_description), *decoys]
    index = "\n".join(f"  - {name}: {desc}" for name, desc in rows)
    return (
        "You are a concise assistant for a small operations team. Answer the user's request directly.\n\n"
        "## Skills\n"
        "Before replying, scan the skills below. If a skill matches or is even partially relevant to your task, "
        "you MUST load it with skill_view(name) and follow its instructions.\n\n"
        "<available_skills>\n"
        f"{index}\n"
        "</available_skills>\n\n"
        "Only proceed without loading a skill if genuinely none are relevant to the task."
    )


# ── fixtures ─────────────────────────────────────────────────────────────────

def load_tasks(fixtures_dir: Path) -> list[dict[str, Any]]:
    lines = (Path(fixtures_dir) / "tasks.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def load_checks(fixtures_dir: Path) -> Callable[[dict, str], dict]:
    """``run_checks`` from ``fixtures/checks.py``, loaded by path (fixtures is not a package)."""
    path = Path(fixtures_dir) / "checks.py"
    spec = importlib.util.spec_from_file_location("skilluminati_fixture_checks", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run_checks


def load_skill(fixtures_dir: Path, variant: str) -> str:
    return (Path(fixtures_dir) / variant / "SKILL.md").read_text(encoding="utf-8")


# ── outcome rule ─────────────────────────────────────────────────────────────

def task_outcome(with_pass: bool, without_pass: bool, judge_outcome: str) -> tuple[str, str]:
    """``(outcome, decided_by)``: ``outcome`` is ``win`` (WITH better), ``loss`` or ``tie``.

    Scripted override: exactly one answer passing the scripted check decides. Otherwise the
    judge outcome (``"x"`` = WITH, ``"y"`` = WITHOUT, ``"tie"``) decides.
    """
    if with_pass != without_pass:
        return ("win" if with_pass else "loss"), "scripted"
    return {"x": "win", "y": "loss", "tie": "tie"}[judge_outcome], "judge"


# ── budget ───────────────────────────────────────────────────────────────────

@dataclass
class Budget:
    cap: int
    planned: int
    used: int = 0

    def check_plan(self) -> None:
        if self.planned > self.cap:
            raise CapExceeded(f"planned {self.planned} calls exceeds hard cap {self.cap}; aborting before any call")

    def reserve(self) -> int:
        if self.used + 1 > self.cap:
            raise CapExceeded(f"hard cap {self.cap} reached after {self.used} calls")
        self.used += 1
        return self.used


def plan_calls(n_tasks: int, n_skills: int, n_models: int) -> dict[str, int]:
    """Per pair: 2 subject calls + 2 judge calls. A/A per model: 2 subject + 2 judge per task."""
    per_pair = 4
    cells = n_skills * n_models * n_tasks * per_pair
    aa = n_models * n_tasks * per_pair
    return {"cells": cells, "aa": aa, "total": cells + aa}


def estimate_tokens(messages_by_kind: dict[str, list[dict[str, Any]]], counts: dict[str, int],
                    out_tokens: dict[str, int]) -> int:
    """Rough estimate: chars/4 of a representative payload per call kind, plus expected output."""
    total = 0
    for kind, msgs in messages_by_kind.items():
        in_tok = len(replay.payload_text(msgs)) // 4
        total += counts.get(kind, 0) * (in_tok + out_tokens.get(kind, 0))
    return total


# ── config ───────────────────────────────────────────────────────────────────

@dataclass
class RunConfig:
    fixtures_dir: Path
    reports_dir: Path
    provider: str = SUB_PROVIDER
    subject_models: dict[str, str] = field(default_factory=lambda: {"haiku": HAIKU, "opus": OPUS})
    judge_model: str = HAIKU
    skills: tuple[str, ...] = ("helpful", "poisoned")
    skill_name: str = SKILL_NAME
    cap: int = HARD_CAP
    subject_max_tokens: int = 600
    judge_max_tokens: int = 200
    subject_temperature: Optional[float] = None  # provider default: the real agent's behaviour
    judge_temperature: float = 0.0
    timeout_s: float = 120.0
    task_ids: Optional[list[str]] = None  # restrict (tests); None = all
    #: ``"blind"`` (request + answers) or ``"purpose"``. Every new run should pass ``judge_mode=JUDGE_MODE_NEW_RUNS``.
    #: The dataclass default is still blind only because older tests build a default config and
    #: identify judge calls by the blind rubric; flipping it is a one-line change.
    judge_mode: str = sb_judge.MODE_BLIND


#: The judge mode every new run should pass explicitly.
JUDGE_MODE_NEW_RUNS = sb_judge.MODE_PURPOSE


# ── runner ───────────────────────────────────────────────────────────────────

class Runner:
    def __init__(self, llm: Any, config: RunConfig, *, log: Callable[[str], None] = print) -> None:
        self.llm = llm
        self.cfg = config
        self.log = log
        self.fixtures = Path(config.fixtures_dir)
        self.reports = Path(config.reports_dir)
        self.step_dir = self.reports / "step2"
        self.step_dir.mkdir(parents=True, exist_ok=True)
        self.calls_path = self.step_dir / "calls.jsonl"
        self.prompts_path = self.step_dir / "prompts.jsonl"
        self.pairs_path = self.step_dir / "pairs.jsonl"
        for p in (self.calls_path, self.prompts_path, self.pairs_path):  # earlier log kept as .prev, 0600
            sb_paths.fresh_private_file(p)
        self._lock = threading.Lock()
        self.calls: list[dict[str, Any]] = []
        self.stopped: Optional[dict[str, str]] = None

        self.tasks = load_tasks(self.fixtures)
        if config.task_ids:
            self.tasks = [t for t in self.tasks if t["id"] in config.task_ids]
        self.run_checks = load_checks(self.fixtures)
        self.skill_texts = {v: load_skill(self.fixtures, v) for v in config.skills}
        fm, _ = replay.parse_frontmatter(next(iter(self.skill_texts.values())))
        self.system_prompt = build_system_prompt(config.skill_name, str(fm.get("description") or ""))
        self.judge_mode = config.judge_mode
        self.rubric = sb_judge.load_rubric(self.fixtures, self.judge_mode)  # asserts the hash before any call
        self.rubric_hash_expected = sb_judge.read_rubric_hash(self.fixtures, self.judge_mode)
        self.rubric_path = sb_judge.rubric_paths(self.fixtures, self.judge_mode)[0]
        #: Purpose statement per skill variant (purpose mode only; the judge never sees the body).
        self.purposes = {v: sb_judge.purpose_statement(t) for v, t in self.skill_texts.items()}

        plan = plan_calls(len(self.tasks), len(config.skills), len(config.subject_models))
        self.budget = Budget(cap=config.cap, planned=plan["total"])
        self.plan = {**plan, "cap": config.cap, "tokens_estimated": self._estimate()}

    # -- planning -------------------------------------------------------------

    def _estimate(self) -> int:
        task = self.tasks[0] if self.tasks else {"prompt": ""}
        skill_md = next(iter(self.skill_texts.values()))
        arms = replay.build_arms(self.system_prompt, self.cfg.skill_name, skill_md, task["prompt"])
        judge_msgs = sb_judge.build_judge_messages(self.rubric.text, task["prompt"], "x" * 120, "x" * 120)
        n_tasks, n_models, n_skills = len(self.tasks), len(self.cfg.subject_models), len(self.cfg.skills)
        counts = {
            "with": n_skills * n_models * n_tasks,
            "without": n_skills * n_models * n_tasks + 2 * n_models * n_tasks,
            "judge": 2 * (n_skills * n_models * n_tasks + n_models * n_tasks),
        }
        return estimate_tokens({"with": arms["with"], "without": arms["without"], "judge": judge_msgs}, counts,
                               {"with": 120, "without": 120, "judge": 40})

    def print_plan(self) -> None:
        self.log(f"[plan] calls: {self.plan['total']} ({self.plan['cells']} cell + {self.plan['aa']} A/A), "
                 f"hard cap {self.plan['cap']}, estimated tokens ~{self.plan['tokens_estimated']}")
        (self.step_dir / "plan.json").write_text(json.dumps(self.plan, indent=2) + "\n", encoding="utf-8")

    # -- calls ----------------------------------------------------------------

    def _call(self, messages: list[dict[str, Any]], *, model: str, purpose: str, max_tokens: int,
              temperature: Optional[float], meta: dict[str, Any]) -> sb_llm.CallRecord:
        with self._lock:
            index = self.budget.reserve()
        rec = sb_llm.complete(self.llm, messages, provider=self.cfg.provider, model=model, max_tokens=max_tokens,
                              temperature=temperature, timeout=self.cfg.timeout_s, purpose=purpose)
        row = {
            "index": index, "ts": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds"),
            "purpose": purpose, **meta,
            "requested_provider": rec.requested_provider, "requested_model": rec.requested_model,
            "provider": rec.provider, "model": rec.model,
            "input_tokens": rec.input_tokens, "output_tokens": rec.output_tokens, "total_tokens": rec.total_tokens,
            "cache_read_tokens": rec.cache_read_tokens, "latency_s": rec.latency_s,
            "ok": rec.ok, "error_type": rec.error_type, "error": rec.error, "text_chars": len(rec.text or ""),
        }
        with self._lock:
            self.calls.append(row)
            with self.calls_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            with self.prompts_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"index": index, "purpose": purpose, **meta, "messages": messages,
                                     "response_text": rec.text}, ensure_ascii=False) + "\n")
        if not rec.ok and USAGE_LIMIT_RE.search(rec.error or ""):
            raise UsageLimit(rec.error)
        return rec

    def _subject(self, messages: list[dict[str, Any]], model: str, purpose: str, meta: dict[str, Any]) -> sb_llm.CallRecord:
        return self._call(messages, model=model, purpose=purpose, max_tokens=self.cfg.subject_max_tokens,
                          temperature=self.cfg.subject_temperature, meta=meta)

    def _judge_call_fn(self, meta: dict[str, Any], purpose: str = "") -> Callable[[list[dict[str, Any]], str], sb_llm.CallRecord]:
        forbidden = list(self.skill_texts.values()) + [self.system_prompt]
        bodies = list(self.skill_texts.values())

        def call(messages: list[dict[str, Any]], label: str) -> sb_llm.CallRecord:
            sb_judge.assert_blind(messages, self.rubric.text, forbidden, skill_bodies=bodies, purpose=purpose)
            return self._call(messages, model=self.cfg.judge_model, purpose=label,
                              max_tokens=self.cfg.judge_max_tokens, temperature=self.cfg.judge_temperature,
                              meta={**meta, "role": "judge", "judge_mode": self.judge_mode})
        return call

    # -- one pair -------------------------------------------------------------

    def _pair(self, task: dict[str, Any], msgs_x: list[dict[str, Any]], msgs_y: list[dict[str, Any]],
              model: str, meta: dict[str, Any], label_x: str, label_y: str, purpose: str = "") -> dict[str, Any]:
        rec_x = self._subject(msgs_x, model, f"{meta['cell']} {task['id']} {label_x}", {**meta, "role": "subject", "arm": label_x, "task": task["id"]})
        rec_y = self._subject(msgs_y, model, f"{meta['cell']} {task['id']} {label_y}", {**meta, "role": "subject", "arm": label_y, "task": task["id"]})
        pair: dict[str, Any] = {"task": task["id"], "prompt": task["prompt"], "cell": meta["cell"], "model": model,
                                "arms": {label_x: rec_x.to_dict(), label_y: rec_y.to_dict()}}
        if not (rec_x.ok and rec_y.ok):
            pair.update(outcome="error", decided_by="error", scripted=None, judge=None)
            self._write_pair(pair)
            return pair
        chk_x = self.run_checks(task, rec_x.text)
        chk_y = self.run_checks(task, rec_y.text)
        pair["scripted"] = {label_x: chk_x, label_y: chk_y}
        pair["tooling_mentions"] = {label_x: sb_judge.mentions_tooling(rec_x.text),
                                    label_y: sb_judge.mentions_tooling(rec_y.text)}
        in_purpose_mode = self.judge_mode == sb_judge.MODE_PURPOSE
        judgement = sb_judge.judge_pair(self._judge_call_fn({**meta, "task": task["id"]}, purpose if in_purpose_mode else ""),
                                        self.rubric.text, task["prompt"], rec_x.text, rec_y.text,
                                        mode=self.judge_mode, purpose=purpose if in_purpose_mode else None,
                                        label=f"{meta['cell']} {task['id']} judge")
        pair["judge"] = judgement.to_dict()
        outcome, decided_by = task_outcome(chk_x["pass"], chk_y["pass"], judgement.outcome)
        pair.update(outcome=outcome, decided_by=decided_by,
                    judge_outcome={"x": "win", "y": "loss", "tie": "tie"}[judgement.outcome],
                    scripted_outcome=("win" if chk_x["pass"] and not chk_y["pass"] else
                                      "loss" if chk_y["pass"] and not chk_x["pass"] else "tie"))
        self._write_pair(pair)
        return pair

    def _write_pair(self, pair: dict[str, Any]) -> None:
        with self._lock, self.pairs_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(pair, ensure_ascii=False) + "\n")

    # -- cells ----------------------------------------------------------------

    def run_cell(self, skill: str, model_label: str) -> dict[str, Any]:
        model = self.cfg.subject_models[model_label]
        cell = f"{skill}/{model_label}"
        meta = {"cell": cell, "skill": skill, "model_label": model_label}
        pairs: list[dict[str, Any]] = []
        for task in self.tasks:
            arms = replay.build_arms(self.system_prompt, self.cfg.skill_name, self.skill_texts[skill], task["prompt"])
            without_raw = sb_judge.raw_text(arms["without"])
            if self.skill_texts[skill] in without_raw:
                raise AssertionError("skill text in WITHOUT arm")
            if "marks impact" in without_raw:
                raise AssertionError("skill rule text leaked into WITHOUT arm")
            if replay.index_has_entry(arms["without"][0]["content"], self.cfg.skill_name):
                raise AssertionError("index entry in WITHOUT")
            if json.loads(arms["with"][3]["content"])["content"] != self.skill_texts[skill]:
                raise AssertionError("skill text missing from WITH arm")
            pairs.append(self._pair(task, arms["with"], arms["without"], model, meta, "with", "without",
                                    purpose=self.purposes[skill]))
            self.log(f"  {cell} {task['id']}: {pairs[-1]['outcome']} ({pairs[-1]['decided_by']})")
        return summarise_cell(cell, skill, model, pairs)

    def run_aa(self, model_label: str) -> dict[str, Any]:
        model = self.cfg.subject_models[model_label]
        cell = f"aa/{model_label}"
        meta = {"cell": cell, "skill": "none", "model_label": model_label}
        pairs: list[dict[str, Any]] = []
        for task in self.tasks:
            without = replay.build_without_arm(self.system_prompt, self.cfg.skill_name, task["prompt"])
            # A/A has no skill in either arm; the purpose under test is the first configured skill's.
            pairs.append(self._pair(task, without, list(without), model, meta, "without_a", "without_b",
                                    purpose=self.purposes[self.cfg.skills[0]]))
            self.log(f"  {cell} {task['id']}: {pairs[-1]['outcome']} ({pairs[-1]['decided_by']})")
        return summarise_aa(cell, model, pairs)

    # -- whole run ------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        started = time.monotonic()
        self.budget.check_plan()
        self.print_plan()
        rubric_before = sb_judge.sha256_text(self.rubric_path.read_text(encoding="utf-8"))
        results: dict[str, Any] = {
            "generated_at": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds"),
            "provider": self.cfg.provider, "subject_models": dict(self.cfg.subject_models),
            "judge_model": self.cfg.judge_model, "judge_mode": self.judge_mode,
            "purposes": dict(self.purposes) if self.judge_mode == sb_judge.MODE_PURPOSE else {},
            "skill_name": self.cfg.skill_name,
            "n_tasks": len(self.tasks), "task_ids": [t["id"] for t in self.tasks],
            "samples_per_arm": 1, "deferred": "repeats (more than one sample per arm per task) are not run",
            "plan": self.plan, "system_prompt": self.system_prompt, "decoys": [d[0] for d in DECOYS],
            "cells": {}, "aa": {}, "stopped_early": None,
        }
        try:
            for skill in self.cfg.skills:
                for model_label in self.cfg.subject_models:
                    self.log(f"[cell] {skill}/{model_label}")
                    results["cells"][f"{skill}/{model_label}"] = self.run_cell(skill, model_label)
            for model_label in self.cfg.subject_models:
                self.log(f"[aa] {model_label}")
                results["aa"][model_label] = self.run_aa(model_label)
        except (CapExceeded, UsageLimit) as exc:
            self.stopped = {"reason": type(exc).__name__, "error": str(exc)}
            results["stopped_early"] = self.stopped
            self.log(f"[stop] {type(exc).__name__}: {exc}")
        except Exception as exc:  # any other failure: keep the partial results, say so, do not hide it
            self.stopped = {"reason": f"unexpected {type(exc).__name__}", "error": str(exc)}
            results["stopped_early"] = self.stopped
            self.log(f"[stop] unexpected {type(exc).__name__}: {exc}")
        rubric_after = sb_judge.sha256_text(self.rubric_path.read_text(encoding="utf-8"))
        results["rubric"] = {
            "path": self.rubric.path, "mode": self.judge_mode, "sha256_expected": self.rubric_hash_expected,
            "sha256_before": rubric_before, "sha256_after": rubric_after,
            "unchanged": rubric_before == rubric_after == self.rubric_hash_expected,
        }
        results["position_bias"] = position_bias_summary(list(results["cells"].values()) + list(results["aa"].values()))
        results["totals"] = totals(self.calls, time.monotonic() - started)
        results["gate"] = compute_gate(results)
        return results


# ── summaries ────────────────────────────────────────────────────────────────

def _count(pairs: list[dict[str, Any]], key: str) -> dict[str, int]:
    out = {"wins": 0, "losses": 0, "ties": 0, "errors": 0}
    for p in pairs:
        o = p.get(key)
        if o == "win":
            out["wins"] += 1
        elif o == "loss":
            out["losses"] += 1
        elif o == "tie":
            out["ties"] += 1
        else:
            out["errors"] += 1
    return out


def _verdict_dict(c: dict[str, int]) -> dict[str, Any]:
    v = stats.verdict(c["wins"], c["losses"], c["ties"])
    return {"verdict": v.verdict, "low": round(v.low, 4), "high": round(v.high, 4), "rate": v.rate,
            "wins": v.wins, "losses": v.losses, "ties": v.ties, "n_decisive": v.n_decisive,
            "tasks_to_settle": v.tasks_to_settle}


def _judge_stats(pairs: list[dict[str, Any]]) -> dict[str, int]:
    out = {"first_picks": 0, "decisive_judgements": 0, "parsed_judgements": 0, "parse_failures": 0,
           "call_failures": 0, "disagreements": 0}
    for p in pairs:
        j = p.get("judge")
        if not j:
            continue
        out["first_picks"] += j["first_picks"]
        out["decisive_judgements"] += j["decisive_judgements"]
        out["parsed_judgements"] += sum(1 for o in j["orders"] if o["raw_winner"] is not None)
        out["parse_failures"] += j["parse_failures"]
        out["call_failures"] += j["call_failures"]
        out["disagreements"] += 0 if j["agreed"] else 1
    return out


def summarise_cell(cell: str, skill: str, model: str, pairs: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [p for p in pairs if p["outcome"] != "error"]
    n = len(ok)
    scripted: dict[str, Any] = {
        "n": n,
        "with_pass": sum(1 for p in ok if p["scripted"]["with"]["pass"]),
        "without_pass": sum(1 for p in ok if p["scripted"]["without"]["pass"]),
        "with_poisoned": sum(1 for p in ok if p["scripted"]["with"]["poisoned"]),
        "without_poisoned": sum(1 for p in ok if p["scripted"]["without"]["poisoned"]),
    }
    scripted["with_pass_rate"] = scripted["with_pass"] / n if n else None
    scripted["without_pass_rate"] = scripted["without_pass"] / n if n else None
    combined = _count(pairs, "outcome")
    return {
        "cell": cell, "skill": skill, "model": model, "n_pairs": len(pairs), "n_errors": combined["errors"],
        "scripted": scripted,
        "combined": {**combined, "decided_by": {"scripted": sum(1 for p in ok if p["decided_by"] == "scripted"),
                                                "judge": sum(1 for p in ok if p["decided_by"] == "judge")},
                     **_verdict_dict(combined)},
        "judge_only": {**_count(pairs, "judge_outcome"), **_verdict_dict(_count(pairs, "judge_outcome"))},
        "scripted_only": _count(pairs, "scripted_outcome"),
        "judge_stats": _judge_stats(pairs),
        "tooling_mentions": {"with": sum(1 for p in ok if p["tooling_mentions"]["with"]),
                             "without": sum(1 for p in ok if p["tooling_mentions"]["without"])},
        "pairs": [{k: v for k, v in p.items() if k != "prompt"} for p in pairs],
    }


def summarise_aa(cell: str, model: str, pairs: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [p for p in pairs if p["outcome"] != "error"]
    combined = stats.aa_noise([p["outcome"] for p in ok])
    judge_only = stats.aa_noise([p["judge_outcome"] for p in ok])

    def d(a: stats.AANoise) -> dict[str, Any]:
        return {"n": a.n, "wins": a.wins, "losses": a.losses, "ties": a.ties, "n_decisive": a.n_decisive,
                "rate": a.rate, "low": round(a.low, 4), "high": round(a.high, 4), "tie_share": a.tie_share,
                "noisy": a.noisy, "verdict": stats.verdict(a.wins, a.losses, a.ties).verdict}
    return {
        "cell": cell, "model": model, "n_pairs": len(pairs), "n_errors": len(pairs) - len(ok),
        "scripted": {"a_pass": sum(1 for p in ok if p["scripted"]["without_a"]["pass"]),
                     "b_pass": sum(1 for p in ok if p["scripted"]["without_b"]["pass"]), "n": len(ok)},
        "combined": d(combined), "judge_only": d(judge_only), "judge_stats": _judge_stats(pairs),
        "pairs": [{k: v for k, v in p.items() if k != "prompt"} for p in pairs],
    }


def position_bias_summary(cells: list[dict[str, Any]]) -> dict[str, Any]:
    first = sum(c["judge_stats"]["first_picks"] for c in cells)
    decisive = sum(c["judge_stats"]["decisive_judgements"] for c in cells)
    parsed = sum(c["judge_stats"]["parsed_judgements"] for c in cells)
    pb = stats.position_bias(first, decisive)
    pb_all = stats.position_bias(first, parsed)
    return {"first_picks": first, "decisive_judgements": decisive, "parsed_judgements": parsed,
            "share_of_decisive": pb.share, "share_of_parsed": pb_all.share, "flagged": pb.flagged,
            "threshold": stats.POSITION_BIAS_MAX, "basis": "share_of_decisive"}


def totals(calls: list[dict[str, Any]], seconds: float) -> dict[str, Any]:
    return {
        "calls": len(calls), "ok": sum(1 for c in calls if c["ok"]), "failed": sum(1 for c in calls if not c["ok"]),
        "input_tokens": sum(c["input_tokens"] for c in calls), "output_tokens": sum(c["output_tokens"] for c in calls),
        "total_tokens": sum(c["total_tokens"] for c in calls), "minutes": round(seconds / 60, 2),
        "by_model": {m: sum(1 for c in calls if c["requested_model"] == m) for m in sorted({c["requested_model"] for c in calls})},
    }


# ── gate ─────────────────────────────────────────────────────────────────────

def _rate_ok(cell: Optional[dict[str, Any]], want_win: bool) -> bool:
    if not cell:
        return False
    c = cell["combined"]
    if c["verdict"] == (stats.KEEP if want_win else stats.DROP):
        return True
    return c["rate"] is not None and (c["rate"] > 0.5 if want_win else c["rate"] < 0.5)


def compute_gate(results: dict[str, Any]) -> dict[str, bool]:
    cells, aa = results.get("cells", {}), results.get("aa", {})
    models = list(results.get("subject_models", {}).keys()) or ["haiku", "opus"]
    helpful = all(_rate_ok(cells.get(f"helpful/{m}"), True) for m in models)
    poisoned = all(_rate_ok(cells.get(f"poisoned/{m}"), False) for m in models)
    aa_ties = all(m in aa and aa[m]["combined"]["verdict"] == stats.UNSURE for m in models)
    pb = results.get("position_bias", {})
    pb_ok = pb.get("share_of_decisive") is not None and pb["share_of_decisive"] <= stats.POSITION_BIAS_MAX
    rubric_ok = bool(results.get("rubric", {}).get("unchanged"))
    judge_ok = all(
        f"poisoned/{m}" in cells and cells[f"poisoned/{m}"]["judge_only"]["wins"] <= cells[f"poisoned/{m}"]["judge_only"]["losses"]
        for m in models
    )
    gate = {"helpful_wins": helpful, "poisoned_loses": poisoned, "aa_ties": aa_ties, "position_bias_ok": pb_ok,
            "rubric_hash_unchanged": rubric_ok, "judge_never_prefers_poisoned": judge_ok}
    gate["overall"] = all(gate.values()) and results.get("stopped_early") is None
    return gate


__all__ = [
    "HARD_CAP", "SKILL_NAME", "SUB_PROVIDER", "HAIKU", "OPUS", "DECOYS", "USAGE_LIMIT_RE", "JUDGE_MODE_NEW_RUNS",
    "CapExceeded", "UsageLimit", "Budget", "RunConfig", "Runner",
    "build_system_prompt", "load_tasks", "load_checks", "load_skill", "task_outcome", "plan_calls",
    "estimate_tokens", "summarise_cell", "summarise_aa", "position_bias_summary", "totals", "compute_gate",
]
