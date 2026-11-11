![Skilluminati](docs/img/banner.png)

# Skilluminati

**Your skills, judged blind.**
Find out whether each skill your Hermes agent loads makes its answers better.

> "The owl of Minerva spreads its wings only with the falling of the dusk." (G.W.F. Hegel)

## What it answers

A skill is a file of instructions your agent reads before it starts a job, and every skill you install adds to every prompt. Skilluminati redoes work you have already done with a skill, once with it and once without, and the Owl, judging blind, says which answers were better.

## Try it

```bash
hermes plugins install yxssxn/skilluminati/plugin
hermes plugins enable skilluminati
```

In Hermes Desktop, also turn on **Capabilities -> Plugins -> Skilluminati**. That adds the Skilluminati page (the book icon in the sidebar). Then open a new chat and type:

```
/skilluminati systematic-debugging
```

A new install has no history to test, so this uses the skill's 8 built-in questions. It takes about 8 minutes. The test runs in the background. When it is done a popup says so: press **Show in chat** to put the verdict in the chat. On the page you can pick any of your skills and press **Ask the Owl** instead.

`frontend-design` also has built-in questions, but its test takes about an hour because every answer is a whole web page.

Two more commands:

- `/skilluminati selftest` runs the Owl's trial: it plants a helpful, a harmful and an empty skill to prove the Owl can tell them apart.
- `/skilluminati stop` cancels the test that is running.

## What you get

<!-- The words live in plugin/skilluminati/copy.py (WORDS, MEANING, STRENGTH). Change them there and in this table. -->

| Verdict | What it means |
|---|---|
| Enlightened | Answers are clearly better with it. Keep it. |
| Dead weight | The model does this anyway. It only costs tokens. |
| Hindrance | Answers are worse with it. Fix or remove. |
| Unproven | Too few uses to tell yet. |

Each verdict also says how sure it is: **Probable** (a clear, consistent result) or **Plausible** (a lean, worth re-testing with more uses). Under **See what the Owl saw** you see every task, the counts and the Owl's own checks.

Real results, 12 Nov 2026, with Opus 5.5 answering and judging:

| Skill | Tested on | Verdict | Better / Same / Worse | Calls and time |
|---|---|---|---|---|
| `systematic-debugging` | 8 built-in questions | Enlightened (Probable) | 8 / 0 / 0 | 48 calls, 8 min |
| `frontend-design` | 10 built-in questions | Enlightened (Probable) | 9 / 1 / 0 | about 60 min |
| `kanban-operations-hygiene` | 12 uses from the author's own history | Enlightened (Probable) | 7 / 1 / 1 (3 could not be judged) | 58 calls, 10 min |
| `unslop` (the author's writing skill) | 12 of its 900+ uses | Dead weight (Plausible) | 5 / 5 / 2 | 64 calls, 14.2 min |

`unslop` was run twice and gave the same result both times. `systematic-debugging` on the author's own history did not hold steady: three runs on 12 Nov gave Enlightened (7 / 4 / 1), Enlightened (5 / 6 / 1) and Dead weight (7 / 1 / 4). Its built-in questions gave 8 / 0 / 0. When a result moves like that, treat it as a lean and ask again.

## How it works

1. It finds the latest 12 times you used the skill, each with the chat as it stood at that moment.
2. Your model answers each one twice: once with the skill and once without.
3. The Owl reads both answers and the skill's one-line description of its job. It is not told which answer had the skill.
4. The Owl sees every pair twice, with the answers swapped. If it picks differently the second time, the pair counts as Same.
5. The counts (Better / Same / Worse) become one of the four verdicts. With fewer than 6 tasks the verdict is Unproven.

If a skill has fewer than 6 usable uses in your history, Skilluminati falls back to its built-in questions where it has them. Three skills have a set: `frontend-design`, `systematic-debugging` and `hermes-agent` (the last two ship with Hermes). See [docs/packs.md](docs/packs.md).

The full method, and what it cannot tell you, is in [docs/method.md](docs/method.md).

## Can you trust the Owl?

`/skilluminati selftest` plants three made-up skills with known answers: one that helps, one with wrong rules planted in it and one that adds nothing useful.
Think of the test button on a smoke alarm: if the Owl cannot sort those three, do not trust it on your own skills.

On 12 Nov 2026 the self-test sorted all three as expected, in 108 calls and 8 minutes:

| Planted skill | What it does | The Owl's verdict | Better / Same / Worse |
|---|---|---|---|
| the Adept | helps | Enlightened | 6 / 0 / 0 |
| the Impostor | sabotages | Hindrance, caught doing harm on 6 of 6 | |
| the Idler | does nothing | Unproven | 2 / 2 / 2 |

The self-test uses a stricter rule than the everyday verdict: it calls a planted skill Unproven unless the result is clear at 95%, which is why the Idler at 2 / 2 / 2 reads Unproven.

## Safe by design

- **It never edits or deletes a skill.** It only gives advice.
- **It reads your history read-only.** Hermes's history file is opened in read-only mode and never copied.
- **One model call at a time**, in the background, with a short pause after each. Hermes stays responsive and you can keep chatting.
- **Data goes only to the model you already use.** Each task (the chat up to that point, including any tool output in it) is sent to your active provider, the same place your chats already go. Before it leaves, emails, phone numbers, key-shaped strings, URLs carrying tokens and money amounts are blanked out. Names are blanked out only if you list them, one per line, in `HERMES_HOME/plugin-data/skilluminati/names.txt`.
- **It spends tokens, but only when you ask.** A test of one skill is up to 64 model calls (12 tasks plus 4 noise checks, 4 calls each). It starts only when you type the command or press Ask the Owl, and only in a local session, never under cron or a messaging gateway.
- **Results stay on your machine**, in `HERMES_HOME/plugin-data/skilluminati/`. Delete that folder to remove them all. There is no telemetry.

## What's in here

The public repo holds the plugin, its docs and its licence.

**`plugin/`: what Hermes loads**

| File | What it does |
|---|---|
| `plugin.yaml` | The label Hermes reads: name, version, the tools it adds and the permissions it asks for. |
| `__init__.py` | Runs when Hermes starts. Adds the `/skilluminati` command and two read-only tools. |
| `desktop/plugin.js` | Draws the Skilluminati page in Hermes Desktop: your skills, an Ask the Owl button and the verdicts. |
| `dashboard/manifest.json` | Tells Hermes where the page gets its data. |
| `dashboard/plugin_api.py` | Hands the page its data: your skills, how often you used each and the saved results. Read-only. |

**`plugin/skilluminati/`: how a test runs**

| File | What it does |
|---|---|
| `__init__.py` | Holds the version number. |
| `commands.py` | Answers `/skilluminati` when you type it, and passes slow work to `run.py` so it replies at once. |
| `run.py` | Starts a test in the background, shows progress and handles stop. When it ends, a popup offers **Show in chat**. |
| `mine.py` | Searches your past chats (read-only) for the times a skill was opened and turns each into a task. |
| `packs.py` | Loads the built-in question sets, and refuses any set edited since it shipped. |
| `redact.py` | Blanks out emails, phone numbers, keys, token URLs, money and listed names before anything is sent. |
| `replay.py` | Rebuilds each chat twice: once with the skill loaded, once without. |
| `llm.py` | Every model call goes through here. It counts calls, stops at the limit and runs them one at a time. |
| `judge.py` | The Owl. Shows it two answers in both orders and reads back its pick. |
| `realrun.py` | Runs the test on a real skill: two answers per task, the Owl picks between each pair, the picks add up to a verdict. |
| `verdict.py` | Turns the counts (Better / Same / Worse) into Enlightened, Dead weight, Hindrance or Unproven, and Probable or Plausible. |
| `stats.py` | The maths behind Probable: a 95% range on the share of Better, and how many more tasks would settle it. |
| `recommend.py` | Builds the advice behind a verdict: how sure, any warnings and what to do next when more uses are needed. |
| `report.py` | Turns a finished test into readable text for the chat and the page. Read-only. |
| `selftest.py` | The self-test. Puts the planted skills through the same steps and checks the Owl's calls. |
| `runner.py` | Shared machinery the self-test uses: the call budget, the planted-skill runs and the scripted checks. |
| `cost.py` | Estimates how many tokens each skill adds to every prompt, and how many more when it loads. |
| `copy.py` | Every word the page and the chat show, in one place. Change a word here and it changes everywhere. |
| `paths.py` | Every folder the plugin reads from or writes to, in one place. |
| `schemas.py` | Describes the two tools to the agent so it knows when to use them. |
| `tools.py` | The two tools the agent can call: the token estimate and the latest result. |

**`plugin/skilluminati/fixtures/`: test material that ships with the plugin**

| File or folder | What it is |
|---|---|
| `helpful/`, `poisoned/`, `placebo/`, `bloat/` | Four versions of one made-up skill for the self-test: one that helps, one with wrong rules planted in it, one that says nothing useful and one padded with filler (used by the longer self-test). |
| `tasks.jsonl` | The 12 self-test questions about the made-up skill. |
| `checks.py` | Scripted checks that mark self-test answers right or wrong without asking a model. |
| `rubric_purpose.md` | What the Owl is told: pick the answer that better does the job the skill describes. |
| `rubric_harm_v2.md` | The self-test's second question: is anything in an answer wrong or missing? |
| `rubric.md`, `rubric_harm.md` | Earlier versions of the Owl's instructions, kept so older results can be re-checked. |
| `*.sha256` | A fingerprint for each set of the Owl's instructions. A run refuses to start if one was changed. |
| `packs/<skill>/` | One built-in question set per skill: `README.md` (where it came from), `tasks.jsonl` (the questions), an unchanged copy of the skill with its licence, and `SHA256SUMS` (fingerprints). |

**`docs/`**: [method.md](docs/method.md) (how the judging works) and [packs.md](docs/packs.md) (the built-in question sets).

## Credits and licences

- Skilluminati: MIT, Copyright 2026 Yassin Ghomari. See `LICENSE`.
- `frontend-design` skill: Apache-2.0, Anthropic, PBC ([anthropics/skills](https://github.com/anthropics/skills)).
- `systematic-debugging` and `hermes-agent` skills: MIT, Nous Research ([NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)). `systematic-debugging` is adapted from [obra/superpowers](https://github.com/obra/superpowers) (MIT, Jesse Vincent).
- The source of every built-in question is listed in its set's `README.md` and `tasks.jsonl`.
