# systematic-debugging (finding the real cause of a bug)

**What the skill is:** a method for fixing bugs: find the real cause first, then fix it, instead of guessing or patching the symptom. It ships with every Hermes install.

**Where it came from:** [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent), commit `3b421578588243f920886e6fe79a6faa87ea026e`, file `skills/software-development/systematic-debugging/SKILL.md`. Copied unchanged. Hermes adapted it from [obra/superpowers](https://github.com/obra/superpowers) (MIT, Copyright (c) 2025 Jesse Vincent).

**Licence:** MIT, Copyright (c) 2025 Nous Research. Full text in `LICENSE`.

**What the questions test:** eight bug reports, several with a tempting quick fix suggested by the asker (a sleep, a bigger memory limit, "just make the test pass"). Does the skill make the answer find and explain the real cause instead of patching the symptom?

**Where the questions came from:** written for this test by Claude Opus 5.5 on 11 Nov 2026. Made-up code, no private data, nothing copied.

Questions: 8, in `tasks.jsonl`. `SHA256SUMS` holds the fingerprints of `tasks.jsonl` and `SKILL.md`; Skilluminati refuses the pack if either was edited.
