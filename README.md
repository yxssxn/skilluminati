![Skilluminati](docs/img/banner.png)

# Skilluminati

[![tests](https://github.com/yxssxn/skilluminati/actions/workflows/tests.yml/badge.svg)](https://github.com/yxssxn/skilluminati/actions/workflows/tests.yml)

**Your skills, judged blind.**
Find out whether each skill your Hermes agent loads makes its answers better.

> "The owl of Minerva spreads its wings only with the falling of the dusk." 
(G.W.F. Hegel)

## What it answers

A skill is a file of instructions your agent reads before a job, and every skill you install adds to every prompt. Skilluminati redoes work you have already done with a skill, once with it and once without, and the Owl, judging blind, says which answers were better.

## Try it

```bash
hermes plugins install yxssxn/skilluminati/plugin
hermes plugins enable skilluminati
```

This installs v0.2.0 from this repo. Hermes calls it an unreviewed source because it does not come from the Hermes catalogue. The catalogue copy (`hermes plugins install skilluminati`) stays on v0.1.0, the hackathon entry; see [CHANGELOG.md](CHANGELOG.md).

In Hermes Desktop, also turn on **Capabilities -> Plugins -> Skilluminati** to add the Skilluminati page (the book icon in the sidebar). Then open a new chat and type:

```
/skilluminati systematic-debugging
```

A new install has no history to test, so this uses the skill's 8 built-in questions. After a few minutes in the background a popup offers **Show in chat**. On the page, **Ask the Owl** does the same for any of your skills.

`frontend-design` also has built-in questions, but takes much longer: every answer is a whole web page.

Two more commands:

- `/skilluminati selftest` checks the Owl on three planted skills (see below).
- `/skilluminati stop` cancels the run in progress.

## What you get

<!-- The words live in plugin/skilluminati/copy.py (WORDS, MEANING, STRENGTH, UNCLEAR_WORD). Change them there and in this table. -->

| Verdict | What it means |
|---|---|
| Enlightened | Answers are clearly better with it. Keep it. |
| Dead weight | The model does this anyway. It only costs tokens. |
| Hindrance | Answers are worse with it. Fix or remove. |
| Unproven | Too few uses to tell yet. |

Each verdict also says how sure it is: **Probable** (clear and consistent, the stronger of the two) or **Plausible** (a lean, worth re-testing). A Dead weight that is only Plausible is shown as **No clear difference yet**, with the next step **Ask again**: one run cannot tell no effect from a small one. **See what the Owl saw** shows every task, the counts and the Owl's own checks.

A **task** is one past use that gave the Owl two answers to judge; a use whose answer came back empty, or whose call failed, is dropped. A **run** is one go at a skill. Asking again adds a run: on the same skill version, model and judge, its answers join earlier ones task by task, and the result says what it rests on ("12 tasks, 3 runs").

### Flags

- **Tool-driven:** half or more of the tested uses went straight to tool calls in the real chat, or came back empty. This check reads answers only, so the result is held at Plausible.
- **Heavy:** the skill adds at least twice the tokens of your median skill.
- **Few tasks:** fewer than 10 decisive tasks (Better or Worse; Same does not count).
- **Split decision:** the Owl leaned on answer order; treat with care.
- **Same-model judge** (**Self-judged** on the page): the Owl is grading its own model's answers. A judge from another maker firms it up ([plugin/README.md](plugin/README.md)).
- **Skill changed:** the skill file differs from the one tested. **Stale:** you now use a different model.

### Results on the author's machine

As of 14 Nov 2026 (runs up to 20:00), with Opus 5.5 answering and judging, on the author's own history. Every figure comes from `scripts/skilluminati_numbers.py`, which runs the plugin's own verdict code over the stored runs. Run it on yours:

```bash
python scripts/skilluminati_numbers.py --runs ~/.hermes/plugin-data/skilluminati/runs
```

| Skill | Runs | Tasks | Better / Same / Worse | v0.2 rules on these stored runs | Flags |
|---|---|---|---|---|---|
| `systematic-debugging`, two v0.2 runs (14 Nov) | 2 | 12 | 8 / 4 / 0 | Enlightened (Plausible, held there by the tool flag) | Tool-driven: 11 of 12 uses in each run, Few tasks |
| `systematic-debugging`, three v0.1 runs (12 Nov) | 3 | 12 | 8 / 2 / 2 | Enlightened (Plausible) | Tool-driven: 11 of 12 uses in each run |
| `unslop` (the author's writing skill) | 4 | 21 | 13 / 5 / 3 | Enlightened (Probable) | Heavy, Split decision |
| `kanban-operations-hygiene` | 1 | 9 | 7 / 1 / 1 | Enlightened (Plausible, held there by the tool flag) | Tool-driven: 12 of 12 uses (3 answers came back empty), Heavy, Few tasks |

A fourth, private skill ran once: 11 tasks, Better / Same / Worse 8 / 1 / 2, Enlightened (Plausible), Tool-driven: 11 of 12 uses. All five results (the four rows and this one) carry the Same-model judge flag.

The first row is the only result made end to end by v0.2, as the plugin showed it. Its two runs scored Better / Same / Worse 10 / 1 / 1 and 8 / 1 / 3 on the same 12 tasks as the three 12 Nov runs, which scored 7 / 4 / 1, 5 / 6 / 1 and 7 / 1 / 4. Single runs move: v0.1 kept only the last of those three and called it Dead weight, where v0.2 says No clear difference yet. The rows stay apart because v0.1 runs do not record which version of the skill file they tested; the plugin shows them as stored.

## How it works

1. It finds the latest 12 times you used the skill, each with the chat as it stood at that moment.
2. Your model answers each one twice: once with the skill and once without.
3. The Owl reads both answers and the skill's one-line description of its job. It is not told which answer had the skill.
4. The Owl sees every pair twice, with the answers swapped. If it picks differently the second time, the pair counts as Same.
5. The counts (Better / Same / Worse) become one of the four verdicts.

With fewer than 6 usable uses in your history, a skill falls back to built-in questions if it has a set: `frontend-design`, `systematic-debugging` and `hermes-agent` do ([docs/packs.md](docs/packs.md)). The full method is in [docs/method.md](docs/method.md).

## How often is the Owl wrong?

Given two answers made the same way, the Owl still named a winner most of the time, so one run's lead alone is weak evidence.

This is the same-vs-same check: on up to 4 tasks in every run (three early runs checked 9) the Owl also compares two answers made without the skill. A firm pick there (the same winner in both orders) is a difference the skill did not cause.

On the author's machine, over 11 finished runs, the Owl made a firm pick **37 of 59 times (63%)**: the first answer 15 times and the second 22 times, with Same 22 times. That is Opus 5.5 judging its own answers, on a small base, and it is why runs add up and close results read No clear difference yet. Your own figure appears from your second run on (8 pairs or more), as "Same-vs-same: a firm pick N of M times". A judge from another maker may do better; nobody has measured that here yet.

`/skilluminati selftest` is the other check, like the test button on a smoke alarm. It plants three made-up skills (one that helps, one with wrong rules, one that says nothing useful), marked by script. If the Owl cannot sort them, do not trust it on yours. Here a planted skill is Unproven unless the result passes a standard statistical test at 95% confidence, stricter than the everyday verdict.

## What it cannot tell you

- **Whether a skill helps over a whole session.** Each task gets one answer and no tools, so a skill whose value is in tool work is not judged fairly (the Tool-driven flag marks these). A live-tool runner such as [skill-court](https://github.com/saatvik333/hermes-skill-court) measures that.
- **Whether your agent should have loaded the skill at all.**
- **The truth for certain.** The Owl is a model. Blind judging in both orders cuts its mistakes but does not remove them.
- **Much about a skill you rarely use.** Few uses, weak verdict.
- **Anything about another model.** A stronger model may not need a skill a weaker one did.

## Safe by design

- **It never edits or deletes a skill.**
- **It reads your history read-only.** Run folders keep redacted copies of the tasks, readable only by you (files 0600 in a 0700 folder). Redaction works by pattern plus your optional names list (`names.txt`) and is best effort: a password or secret that does not look like a key can slip through. Folders made by v0.1 hold unredacted copies (see CHANGELOG).
- **One model call at a time**, in the background, so you can keep chatting.
- **Data goes to your active provider, and to a judge's provider only if you set one.** Each task (the chat up to that point, with its system prompt, so your memory and profile, and tool output) goes to your active provider, where your chats already go. If you set a judge (`judge.provider` and `judge.model`), the redacted request and both answers also go to its provider. Before anything leaves, emails, phone numbers, key-shaped strings, URLs carrying tokens and money amounts are blanked out, the account name in most home-folder paths becomes `~`, and names only if you list them, one per line, in `HERMES_HOME/plugin-data/skilluminati/names.txt`.
- **It spends tokens, but only when you ask.** A run on one skill is up to 64 model calls (12 tasks plus 4 same-vs-same checks, 4 calls each). A model call is one request to your model, billed like any other (a long replayed chat costs more). A run starts only in a local session, never from a scheduled job (cron) or a messaging gateway such as Telegram.
- **Results stay on your machine**, in `HERMES_HOME/plugin-data/skilluminati/` (`HERMES_HOME` is your Hermes folder, usually `~/.hermes`). Delete that folder to remove them. There is no telemetry.
- **It adds one line to every prompt.** Hermes keeps plugin tools behind its tool search, so the plugin ships a short skill, `skilluminati:skilluminati`. When you ask whether a skill is worth keeping, your agent reads the saved verdict instead of guessing. The line costs about 25 tokens a turn (characters divided by 4). Disable the plugin to remove it.

## What's in the repo

- `plugin/`: what Hermes loads. The `/skilluminati` command, two read-only tools, the Desktop page and the bundled skill (`skills/skilluminati/SKILL.md`).
- `plugin/skilluminati/`: the code for a run, from finding past uses to the verdict. `fixtures/` holds the planted skills and built-in question sets.
- `docs/`: [method.md](docs/method.md) (how the judging works) and [packs.md](docs/packs.md) (the built-in question sets).
- `scripts/skilluminati_numbers.py` prints every figure in this README. Standard library only; it reads run folders and (unless you pass `--no-state-db`) your history file, read-only.

**`tests/`**: the public tests, listed in `tests/PUBLIC.txt`. GitHub Actions runs them on Python 3.11, 3.12, 3.13 and 3.14 (`.github/workflows/tests.yml`). To run them yourself:

```bash
python -m pip install pytest fastapi
python -m pytest -q $(grep -v '^#' tests/PUBLIC.txt)
```

On a fresh copy of this repo, with FastAPI installed as CI does and Node 20.19 or newer on the PATH, that gives 815 passed and 4 skipped. Without Node, the three tests that run the page script also skip: 812 passed and 7 skipped. The other 4 need development files that are not in this repo.

## Credits and licences

- Skilluminati: MIT, Copyright 2026 Yassin Ghomari. See `LICENSE`.
- `frontend-design` skill: Apache-2.0, Anthropic, PBC ([anthropics/skills](https://github.com/anthropics/skills)).
- `systematic-debugging` and `hermes-agent` skills: MIT, Nous Research ([NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)). `systematic-debugging` is adapted from [obra/superpowers](https://github.com/obra/superpowers) (MIT, Jesse Vincent).
- The source of every built-in question is listed in its set's `README.md` and `tasks.jsonl`.
