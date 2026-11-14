"""In plain words: every sentence Skilluminati shows you lives in this file, so the wording can change
without touching code. Nothing here runs by itself; the page, the chat and the command read these lines.

Every user-facing owl string, in one place.

Edit strings here only. Nothing else in the plugin may hold a themed word. The Desktop page and the dashboard API read these strings from the
payload built by ``payload()``; the slash command and the recommendation object read them directly.

Rules for the copy: plain and dry, Australian English, no em dashes. A themed word is always shown
with its plain label, as ``"<Themed>: <plain>"``.

Standard library only and no package imports, so the dashboard backend can load this file by path.
"""

# ── The author's words ───────────────────────────────────────────────
# The verdict and strength words, and their plain meaning. The chat, the popup and the page all read
# these four tables, so changing a word here changes it everywhere.

#: Verdict word by verdict key (verdict.lean): helps | no_difference | hurts | not_enough.
WORDS = {"helps": "Enlightened", "no_difference": "Dead weight", "hurts": "Hindrance",
         "not_enough": "Unproven"}
#: What each verdict means, in one plain line.
MEANING = {
    "helps": "Answers are clearly better with it. Keep it.",
    "no_difference": "The model does this anyway. It only costs tokens.",
    "hurts": "Answers are worse with it. Fix or remove.",
    "not_enough": "Too few uses to tell yet.",
}
#: How sure, by strength key (verdict.lean): strong | likely.
STRENGTH = {"strong": "Probable", "likely": "Plausible"}
#: The finished report (chat and terminal): same counts line as the chat result.
REPORT_COUNTS = "Better {w} · Same {t} · Worse {l}."
REPORT_USES = "Tested on {n}."  # ({n} is e.g. "its latest 12 uses")
REPORT_MODEL = "Model: {model}."
#: F12: one line in chat (``/skilluminati last``) from a task the skill lost.
LOST_ONE = "Why it lost: {reason}"
REPORT_CAVEAT = "One model, one answer each way, judged blind by the Owl. Advice, not a measurement."
STRENGTH_MEANING = {"strong": "a clear, consistent result", "likely": "a lean, worth re-testing with more uses"}
#: A "no difference" that is not Probable is shown with these words, not Dead weight: one run cannot
#: tell no effect from a small one. The stored verdict stays no_difference (see ``shown``).
UNCLEAR_WORD = "No clear difference yet"
UNCLEAR_MEANING = "Close to even. One run cannot tell no effect from a small effect."

# ── who and what ─────────────────────────────────────────────────────────────

JUDGE_NAME = "the Owl"
RUN_VERB = "bring a skill to trial"
TRIAL_NAME = "The Owl's trial"
TRIAL_LINE = "Plants a helpful, harmful, and an empty skill to prove the Owl can tell them apart."

#: One plain sentence on what the Owl does. Shown beside the concept line.
CONCEPT_PLAIN = "The Owl judges a skill on work it has already done."

#: Concept line.
CONCEPT_QUOTE = "The owl of Minerva spreads its wings only with the falling of the dusk."
CONCEPT_ATTRIBUTION = "G.W.F. Hegel"

#: Desktop page tagline.
TAGLINE = "Is this skill doing the job it says it does, on the model you run today?"

# ── verdicts ─────────────────────────────────────────────────────────────────
# Key = the internal label in verdict.py (verdict.LABELS). Value = (themed word, plain label).
# The headline is always rendered as HEADLINE_FORMAT, themed word first.

HEADLINE_FORMAT = "{themed}. {plain}"

#: Internal label -> verdict key. The words come from WORDS and MEANING above.
LABEL_KEYS = {"Earns its keep": "helps", "Model does this already": "no_difference",
              "Gets in the way": "hurts", "Not sure yet": "not_enough"}
VERDICTS = {label: (WORDS[key], MEANING[key]) for label, key in LABEL_KEYS.items()}

#: Badge tone for the Desktop page, per internal label.
VERDICT_TONE = {
    "Earns its keep": "success",
    "Model does this already": "muted",
    "Gets in the way": "destructive",
    "Not sure yet": "warn",
}

# ── the Trial: planted self-test skills ──────────────────────────────────────
# Key = the planted skill kind in selftest.py. Value = (themed name, plain meaning).

TRIAL = {
    "helpful": ("the Adept", "helps"),
    "placebo": ("the Idler", "does nothing"),
    "poisoned": ("the Impostor", "sabotages"),
    "bloat": ("the Glutton", "bloats"),
}
TRIAL_FORMAT = "{themed} ({plain})"

# ── confidence ───────────────────────────────────────────────────────────────

CONFIDENCE_LABEL = "Confidence"
CONFIDENCE_WORDS = {"Strong": STRENGTH["strong"], "Moderate": STRENGTH["likely"], "Weak": "Weak"}
#: ``{n}`` decisive tasks, ``{low}`` and ``{high}`` the win-rate range as whole percentages.
CONFIDENCE_WHY = "{n} decisive tasks, win rate between {low}% and {high}%"
CONFIDENCE_WHY_NONE = "no decisive tasks yet"

# ── flags ────────────────────────────────────────────────────────────────────

FLAGS_LABEL = "Flags"
NO_FLAGS = "none"
#: The Heavy flag text itself comes from cost.heavy_text; this is only its tag.
FLAG_FEW_TASKS = "Few tasks: only {n} decisive tasks so far"
FLAG_STALE = "Stale: tested on {tested}, re-test on {active}"
FLAG_JUDGES_SPLIT = "Split decision: the Owl leaned on answer order, so treat this with care"
#: The tested model also judged (or no judge was set, so the active model judged).
FLAG_SAME_JUDGE = ("Same-model judge: the Owl is grading its own answers, so treat this as a lean. "
                   "Set judge.provider and judge.model to another maker's model to firm it up")
#: Half or more of the tested uses went straight to tool calls in real use, or came back empty.
FLAG_TOOL_DRIVEN = "Most tested uses went straight to tools in real use, or came back empty here. This test reads answers only."
#: Added to the confidence reason when that flag holds a Probable result at Plausible. ``{word}``
#: is the strength word shown.
CONFIDENCE_WHY_TOOL_CAP = "; held at {word} because this test reads answers only"

#: The short note ``commands.stale_label`` returns.
STALE_NOTE = "stale, re-test on {model}"
#: Shown with a verdict when the skill's SKILL.md is no longer the file that was tested.
FLAG_SKILL_CHANGED = "Skill changed since this test"

# ── next step ────────────────────────────────────────────────────────────────

NEXT_STEP_LABEL = "Next step"
NEXT_KEEP = "Keep"
NEXT_TRIM = "Consider trimming"
NEXT_ARCHIVE = "Consider archiving"  # only after a Probable result (recommend.next_step)
#: Next step for a Plausible "no difference" or "hindrance": one more run before acting on it.
NEXT_ASK_AGAIN = "Ask again"
NEXT_RUN_MORE = "Run about {n} more {tasks}"  # {tasks} is "task" when n is 1
#: Next step when the run already used every task in the user's history: no number to act on.
NEXT_NO_HISTORY = "Not enough history to call it. Use the skill on more work, then run this again."
#: Flag on a verdict from a run that stopped early (page chip and report line).
FLAG_PARTIAL = "Partial: the run stopped early"
FLAG_UNREAD = "Judge failed: it gave no readable answer on {n} of {total} pairs. Check judge.model"
NEXT_CHECK_JUDGE = "Check the judge setting (judge.model), then ask again."
#: Shown once under a recommendation: the tool only advises.
NEXT_NOTE = "Skilluminati never deletes or edits a skill."

# ── end of a run (Desktop) ───────────────────────────────────────────────────

#: The line the Desktop half sends to the agent, on the user's behalf, when a run ends. It names the
#: tool so a host that loads tools on demand finds it. ``{what}`` = the skill name or "the self-test".
SHOW_RESULT_PROMPT = "Show my Skilluminati result for {what} (skilluminati_result tool)."

# ── Desktop page and command chrome ──────────────────────────────────────────

UI = {
    "headline": "Recommendation",
    "confidence": CONFIDENCE_LABEL,
    "flags": FLAGS_LABEL,
    "next_step": NEXT_STEP_LABEL,
}

def headline(label):
    """``(themed, plain, text)`` for an internal verdict label; ``None`` when the label is unknown."""
    pair = VERDICTS.get(label)
    if pair is None:
        return None
    return pair[0], pair[1], HEADLINE_FORMAT.format(themed=pair[0], plain=pair[1])


def shown(key, strength):
    """``(word, meaning, strength word or None)`` the user sees for a verdict key and strength. A
    "no difference" that is not Probable shows ``UNCLEAR_WORD`` and no strength word."""
    if key == "no_difference" and strength != "strong":
        return UNCLEAR_WORD, UNCLEAR_MEANING, None
    return WORDS.get(key, WORDS["not_enough"]), MEANING.get(key, MEANING["not_enough"]), STRENGTH.get(strength)


def trial_name(kind):
    """``"the Adept (helps)"`` for a planted skill kind; ``None`` when unknown."""
    pair = TRIAL.get(kind)
    return None if pair is None else TRIAL_FORMAT.format(themed=pair[0], plain=pair[1])


def payload():
    """The copy block the dashboard API sends to the Desktop page. Plain dict of strings."""
    return {
        "judge": JUDGE_NAME,
        "run_verb": RUN_VERB,
        "tagline": TAGLINE,
        "concept": {"quote": CONCEPT_QUOTE, "attribution": CONCEPT_ATTRIBUTION,
                    "plain": CONCEPT_PLAIN},
        "ui": dict(UI),
        "no_flags": NO_FLAGS,
        "next_note": NEXT_NOTE,
    }


# --- page wording ---
# Lines for starting a run, its progress popup and the result line (run.py, commands.py).

HELP = (
    "Skilluminati tests whether a skill still helps. Open the Skilluminati page to pick a skill.\n"
    "  /skilluminati <skill>    test one skill now\n"
    "  /skilluminati last       show the latest result again (add a skill name for that skill's)\n"
    "  /skilluminati stop       stop the test that is running\n"
    "  /skilluminati selftest   " + TRIAL_NAME + ". " + TRIAL_LINE
)
#: The instant reply, before the history is read (the plan line below follows once it is).
START_LINE = "Bringing {skill} before the Owl: finding its latest uses. /skilluminati stop to cancel."
#: Once the plan is known. ``{n}`` uses tested, ``{minutes}`` the estimate.
PLAN_LINE = "Bringing {skill} before the Owl: testing its last {n} uses, with and without. About {minutes} min."
PACK_PLAN_LINE = ("Bringing {skill} before the Owl: testing its {n} built-in questions, with and without. "
                  "About {minutes} min.")
#: Progress, in the popup and on the page. ``{done}`` and ``{total}`` count comparisons.
PROGRESS_LINE = "The Owl is weighing the evidence. {done} of {total} compared."
#: The live line under it, so you can see it working. One per kind of call; {secs} counts up.
NOW_WITH = "Now: answering with the skill ({secs}s)"
NOW_WITHOUT = "Now: answering without it ({secs}s)"
NOW_JUDGING = "Now: the Owl is judging ({secs}s)"
NOW_NOISE = "Now: a noise check, two answers without it ({secs}s)"
SELFTEST_START_LINE = TRIAL_NAME + " started: 3 planted skills, about {minutes} min. /skilluminati stop to cancel."
GETTING_READY = "Skilluminati: reading your history for {skill}."
#: The result line in the chat. ``{strength}`` is dropped (with its brackets) when there is none.
CHAT_RESULT_LINE = "{skill}: {verdict} ({strength}). Better {w} · Same {t} · Worse {l}."
CHAT_RESULT_LINE_NO_STRENGTH = "{skill}: {verdict}. Better {w} · Same {t} · Worse {l}."
SOURCE_HISTORY = "your history"
SOURCE_PACK = "built-in questions"
#: How much evidence a verdict rests on, across every run of the same skill file and model:
#: "12 tasks, 3 runs". {tasks} is "task" when n is 1, {runs} is "run" when r is 1.
POOL_LINE = "{n} {tasks}, {r} {runs}"

# --- page wording ---

#: Themed words from earlier word sets that old saved results may still carry, by internal label.
OLD_THEMED = {"Cast out": "Gets in the way"}

# --- page wording ---
# In plain words: the words on the Skilluminati page in Hermes Desktop. Change a word here and the
# page changes; the page itself holds no wording.
# The Desktop page (plugin/desktop/plugin.js). Every word the page shows comes from ``page_copy()``.
# Verdict keys: helps | no_difference | hurts | not_enough, strength strong | likely.

PAGE_TITLE = "Skilluminati"
#: One plain sentence at the top of the page; it defines "skill" once.
PAGE_WHAT = ("The Owl replays a skill's latest uses with it and without it, and a blind comparison shows "
             "which skills truly add wisdom.")
PAGE_EMPTY = "Nothing brought before the Owl yet. Pick a skill to test its worth."

#: The four verdict words the page shows, by verdict key.
VERDICT_WORDS = dict(WORDS)
#: Badge tone per verdict key (Desktop Badge variants).
VERDICT_TONES = {"helps": "success", "no_difference": "muted", "hurts": "destructive", "not_enough": "warn"}
#: What the verdict means, in one plain line (hover the verdict, and at the top of the working).
VERDICT_MEANING = dict(MEANING)
#: A "no difference" that is not Probable (see ``UNCLEAR_WORD``): its word, meaning and summary forms.
VERDICT_UNCLEAR = {"word": UNCLEAR_WORD, "meaning": UNCLEAR_MEANING,
                   "summary": ["{n} with no clear difference yet", "{n} with no clear difference yet"]}
#: The one-line summary at the top: [one, many] per verdict key, ``{n}`` is the count.
SUMMARY_PARTS = {
    "helps": ["{n} Enlightened", "{n} Enlightened"],
    "no_difference": ["{n} Dead weight", "{n} Dead weight"],
    "hurts": ["{n} Hindrance", "{n} Hindrances"],
    "not_enough": ["{n} Unproven", "{n} Unproven"],
}
SUMMARY_UNTESTED = "{n} not tested yet"
#: How sure, shown small beside the verdict; the tip explains it on hover.
STRENGTH_WORDS = dict(STRENGTH)
STRENGTH_TIPS = {k: STRENGTH_MEANING[k][0].upper() + STRENGTH_MEANING[k][1:] + "." for k in STRENGTH}
#: For a "not enough to tell" result, which way it leans (``lean``), shown small.
LEAN_WORDS = {k: "leans " + WORDS[k] for k in ("helps", "hurts", "no_difference")}
LEAN_TIP = "Not enough clear results to call it, but this is the way it leans so far."

#: ``{wins}``, ``{ties}``, ``{losses}``, ``{n}``. Same order as the bar: better, same, worse.
RESULT_LINE = "Better {wins} · Same {ties} · Worse {losses}"
RESULT_BAR = "Out of {n} tasks: {wins} better with the skill, {ties} the same, {losses} worse."
LEGEND = {"win": "Better", "tie": "Same", "loss": "Worse"}
#: Hover tips on Better / Same / Worse . The verdict word is the overall call; these
#: count the single uses it is built on.
LEGEND_TIPS = {"win": "Uses where the Owl preferred the answer with the skill.",
               "tie": "Uses where the Owl saw no real difference between the two answers.",
               "loss": "Uses where the Owl preferred the answer without the skill."}
LEGEND_NOTE = "Each bar is one test: your past tasks, redone with and without the skill."
RESULT_WHEN = "Tested {when} with {model}."
MODEL_UNKNOWN = "an unrecorded model"

SKILLS_FILTER = "Find a skill"
SKILLS_SHOW_ALL = "Show all {n} skills"
SKILLS_NONE = "No skills found in your skills folder."
SKILLS_NO_MATCH = "No skill matches \"{q}\"."
USED_NEVER = "Not yet"
TOKENS_PER_PROMPT = "{n} tokens"
SKILLS_HEAD = {"name": "Skill", "uses": "Times you used it", "tokens": "Size when loaded", "verdict": "Result"}
#: Hover text on the column headings (only where a heading needs explaining).
SKILLS_HEAD_TIPS = {"tokens": "The text the skill adds to a chat each time it loads. It then rides along with every later message in that chat."}
TOKENS_TIP = ("About {load} tokens each time it loads (a token is about four characters). Its one-line "
              "entry in Hermes's skill list adds about {n} more to every message.")
NOT_TESTED = "Not tested"
PACK_MARK = "Built-in questions"
PACK_TIP = "Questions that ship with the plugin, for skills you have not used much."

TEST_BUTTON = "Ask the Owl"
TEST_AGAIN = "Ask again"
TEST_STARTED = "Started a test of {skill} in a new chat."
TEST_COPIED = "Copied {command}. Paste in any chat."
TEST_FAILED = "Could not start or copy. Type {command} in any chat."
TEST_BUSY = "A test is already running. Wait for it to finish."

SHOW_WORKING = "See what the Owl saw"

RUNNING_TITLE = "Testing {skill}"
RUNNING_PROGRESS = PROGRESS_LINE
RUNNING_UNKNOWN = "Running…"
RUNNING_SELFTEST = TRIAL_NAME

# "Show the working": each line explains itself in plain words.
WORKING_TASKS = "Task by task"
#: Beside that heading when the counts above are pooled over more than one run (S8).
WORKING_TASKS_LATEST_ONLY = "Latest run only"
WORKING_TASK_ROW = {"win": "Better", "loss": "Worse", "tie": "Same",
                    "error": "Could not be judged"}
WORKING_TASK_LABEL = "Task {i}"
WORKING_STRENGTH = "How sure: {word}. {tip}"
#: counts. ``{uses}`` times found in your history, ``{usable}`` usable as tests, ``{tested}`` tested.
WORKING_COUNTS = "From your history: {uses} past uses found, {usable} usable as tests, {tested} tested."
WORKING_RANGE = ("Range: with this many tasks, the skill's true win rate is somewhere between {low}% and "
                 "{high}% (95% range). Above 50% means it helps.")
WORKING_RANGE_NONE = "Range: no clear wins or losses yet, so there is no range."
WORKING_ORDER = ("Order check: the Owl saw every pair twice, once in each order. It changed its pick on "
                 "{flips} of {n} tasks.")
WORKING_ORDER_BIAS = "It picked the answer shown first {pct}% of the time, so treat a close result with care."
#: F11: the same-vs-same check, pooled per tested model and judge (``noise.py``). A firm pick is the
#: Owl naming the same winner in both orders between two answers made the same way. Report and page.
SAME_VS_SAME = "Same-vs-same: a firm pick {firm} of {n} times ({pct}%)."
SAME_VS_SAME_FEW = "Not enough same-vs-same pairs yet ({n})."
SAME_VS_SAME_WEAK = ("The judge often picks a winner between identical answers on this model, "
                     "so single-run leads are weak.")
WORKING_COST = "This test used {calls} model calls and {tokens} tokens."
WORKING_RUN = "Run id: {run}"
WORKING_JUDGE = "The Owl's model: {judge}"
#: F12: above up to three of the Owl's reasons from tasks the skill lost (report and page).
LOST_LABEL = "Where it lost, the Owl said:"
#: Beside the verdict when the tested model judged itself (tag), and its hover text.
SAME_JUDGE_TAG = "Self-judged"
SAME_JUDGE_TIP = FLAG_SAME_JUDGE + "."
#: The same kind of tag for the show-time and tool-driven flags (S7), so the row never hides them.
#: The Stale hover uses the row's own flag text (with the model names) when it has one.
STALE_TAG = "Stale"
STALE_TIP = "Tested on a different model from the one a new test would use. Ask again to re-test."
SKILL_CHANGED_TAG = "Skill changed"
SKILL_CHANGED_TIP = FLAG_SKILL_CHANGED + ". Ask again to test this version."
TOOL_DRIVEN_TAG = "Tool-driven"
TOOL_DRIVEN_TIP = FLAG_TOOL_DRIVEN
WORKING_PARTIAL = "This run stopped early, so the result is partial."
WORKING_TOOL_DRIVEN = FLAG_TOOL_DRIVEN
WORKING_NONE = "No task-by-task detail was saved for this result."
LOAD_ERROR = "Could not load the page data"
LOAD_ERROR_HINT = "The page's backend loads after the plugin is enabled and the gateway has restarted."


def page_copy():
    """The page wording block the Desktop page renders: every constant after the ``# --- page wording ---`` marker,
    lower-cased (``PAGE_EMPTY`` -> ``page_empty``)."""
    names = list(globals())
    w4 = names[names.index("PAGE_TITLE"):]
    return {n.lower(): globals()[n] for n in w4 if n.isupper()}


# --- W3 PY ---
# SEC-S3: added to the run plan line. {names} is "0 names", "1 name", "3 names".
PLAN_NAMES = "{names} loaded for redaction."
# W2-Q1: "Why it lost" names each answer by role; the judge wrote "Answer 1" / "Answer 2".
LOST_ANSWER_WITH = "the answer with the skill"
LOST_ANSWER_WITHOUT = "the answer without it"
# --- W3 UI ---
# In plain words: tonight's page words (W3 UI). Appended after page_copy(), which still picks them
# up: it reads every upper-case name defined after PAGE_TITLE.
#: A Plausible (strength ``likely``) result reads as a lean, so its meaning line must not say
#: "clearly" (review-page should-fix 2). Used on hover and as the first line of the working.
VERDICT_MEANING_LEAN = {"helps": "Answers lean better with it so far. Keep it, and test again to firm it up.",
                        "hurts": "Answers lean worse with it so far. Test again before you remove it."}
#: The summary splits each verdict by how sure (``{part}`` is e.g. "2 Enlightened", ``{word}`` is
#: Probable or Plausible), so a Plausible count never hides inside a Probable one.
SUMMARY_STRENGTH = "{part} ({word})"
#: Shown once under the summary when every result was judged by the model it tested, in place of a
#: "Self-judged" tag on every row (review-page should-fix 5). Hover shows SAME_JUDGE_TIP.
SAME_JUDGE_NOTE = "Self-judged: every result here was graded by the same model it tested."
#: Words the page code used to hold itself (review-page should-fix 7). The chat strip, the run-done
#: notice and the running row read them from the same page_copy() block.
RUNNING_COUNT = "{done} of {total}"
STRIP_OPEN = "Open page"
STRIP_STOP = "Stop"
RUN_DONE_READY = "Skilluminati: your result is ready."
RUN_DONE_SHOW = "Show in chat"
RUN_DONE_OPEN = "Open page"
RUN_DONE_BUSY = "Skilluminati: that chat is busy. Your result is on the page."
#: The chat strip's Stop asks once before it stops a run (review-page should-fix 8).
STRIP_STOP_ASK = "Stop this test? Calls already made are still paid for."
STRIP_STOP_YES = "Yes, stop"
STRIP_STOP_NO = "Keep going"
#: SEC-S4: the "Show in chat" line. The page builds it from this fixed text and the skill name only;
#: the prompt text in the run.done event is ignored. ``{what}`` is the skill name or RUN_DONE_SELFTEST.
RUN_DONE_PROMPT = SHOW_RESULT_PROMPT
RUN_DONE_SELFTEST = "the self-test"


# --- W4 gate 2 ---
JUDGE_FAILED_TAG = "Judge failed"
JUDGE_FAILED_TIP = "The judge gave no readable answer on most pairs, so this run says nothing about the skill. Check judge.model."
