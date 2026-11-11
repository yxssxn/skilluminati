# Built-in question sets

A new Hermes install has no chat history, so there is nothing of yours to test a skill on. For three well-known skills, Skilluminati ships its own short set of public questions, so anyone can test them on day one. A set is also used when a skill has fewer than 6 usable uses in your history.

| Skill | What it is | Questions | Licence |
|---|---|---|---|
| `frontend-design` | A skill for designing web pages | 10 design briefs | Apache-2.0 (skill); questions MIT or written for this |
| `systematic-debugging` | Find the real cause of a bug before fixing it. Ships with Hermes. | 8 bug reports | MIT (skill); questions written for this |
| `hermes-agent` | The Hermes user guide. Ships with Hermes. | 12 real questions from the Hermes issue tracker | MIT |

Why these three: `frontend-design` is widely used and its effect is easy to see on a page. The other two ship with every Hermes install, so anyone can test them without installing anything.

## What is in a set

Each set is a folder in `plugin/skilluminati/fixtures/packs/<skill>/`:

- `README.md`: what the skill is, where it came from, its licence and what the questions test.
- `tasks.jsonl`: the questions, one per line, each naming its source.
- `SKILL.md` with its `LICENSE` (and `NOTICE` for `frontend-design`): an unchanged copy of the skill.
- `SHA256SUMS`: fingerprints of the questions and the skill. If either was edited, Skilluminati refuses the set. To check by hand, run `shasum -a 256 -c SHA256SUMS` inside the folder.

## Results

Full runs on 12 Nov 2026, with Opus 5.5 answering and judging blind in both orders:

| Skill | Verdict | Better / Same / Worse | Time |
|---|---|---|---|
| `systematic-debugging` (8 questions) | Enlightened (Probable) | 8 / 0 / 0 | 48 calls, 8 min |
| `frontend-design` (10 questions) | Enlightened (Probable) | 9 / 1 / 0 | about 60 min |

`hermes-agent` has no full result yet.

## Long answers

`frontend-design` answers are whole web pages, some over 20,000 characters, so that set is allowed longer answers and more time per answer than the default, and its test takes about an hour. The settings are in `packs.answer_limits` in `plugin/skilluminati/packs.py`.

Every test except `frontend-design` (9,000) gives the answering model room for 6,000 tokens per answer. An answer only counts as runaway (and is left out) on the length of its visible text; thinking is not counted.
