# Changelog

## v0.2.0 (14 Nov 2026)

Install with `hermes plugins install yxssxn/skilluminati/plugin`. The catalogue still installs v0.1.0.

### Results

- Asking again adds evidence. A new run of the same skill version, on the same model and judge, is added to the earlier ones task by task, and the result says what it rests on ("12 tasks, 3 runs"). v0.1 replaced the old result with the new one.
- A close "no difference" that is only Plausible reads "No clear difference yet" with the next step "Ask again". Archiving is only suggested at Probable. The four verdict words are unchanged.
- Runs saved by v0.1 carry no fingerprint, so they are not re-pooled or re-flagged. They show as stored, in the new wording, and the first v0.2 run starts the pool.
- Pool files are written atomically under a lock. A pool that cannot be read is kept aside, never overwritten.
- A run with fewer than 6 usable uses says so before it makes any model call. v0.1 started at 4 and spent the calls on a certain Unproven.

### Flags

- Skill changed: each result stores a SHA-256 of the SKILL.md it tested and says so when the file has changed since.
- Stale now works. It compares the tested model with the model a new test would use (the `subject.model` setting, or the active default). In v0.1 it could never show.
- Tool-driven: when half or more of the tested uses went straight to tool calls in the real chat, or came back empty, the result says this test reads answers only and holds the strength at Plausible.
- Same-model judge: shown when the Owl is the model under test. The plugin README shows how to set a judge from another maker.
- The Desktop page shows Stale, Skill changed and Tool-driven tags beside the verdict.

### Noise

- The same-vs-same check now reports a figure: "Same-vs-same: a firm pick N of M times", pooled per model and judge across runs, with a note at 50% or more. The old "noisy" flag, which never fired on the author's runs and could only fire on a 4 to 0 sweep, is gone.

### Fixes

- A stop during a run is reported as the real reason, not "unexpected CancelledError".
- The chat result shows Plausible on a lean result. Before, the word only appeared when the Tool-driven flag held the result there.
- The chat result's same-vs-same line gives the pooled figure the page shows, also for runs saved before pooling.

### Repo

- Public tests in `tests/` (listed in `tests/PUBLIC.txt`) with a GitHub Actions workflow on `actions/checkout@v7` and `actions/setup-python@v7`.
- `scripts/skilluminati_numbers.py` prints what v0.2's rules give for the runs on disk. Every figure in the README comes from it.
- A `.gitignore` keeps Python caches out of the repo.
- The README no longer says `unslop` gave the same result twice. That was not supported by the stored runs.

### Second round (14 Nov)

- The plugin ships a short skill (`skills/skilluminati/SKILL.md`, about 25 tokens a turn) so your agent reads the saved verdict when you ask whether a skill is worth keeping.
- The two tools describe themselves more clearly to the agent.
- Each `result.json` records the plugin version and a code fingerprint (a short checksum of the plugin's code), and every report ends with a build line.
- Where the skill lost, the report and the page show up to three of the Owl's reasons.
- GitHub Actions also runs the public tests on Python 3.13 and 3.14.
- Stale stays off when only a test provider is set (no model named): that provider's default model cannot be read, so the plugin does not guess.
- Run folders keep only redacted copies of the tasks. Redaction also turns the account name in home-folder paths into `~`, and the run plan says how many names it loaded. Run folders made before this build keep the unredacted task text; to remove it, delete the folders in `HERMES_HOME/plugin-data/skilluminati/runs/` from before 0.2.0. The page keeps its rows and the pools stay, but `/skilluminati last`, the agent's result tool and the page's task-by-task detail then have nothing to show for those runs until you ask again.
- A judge that returns nothing no longer counts as "Same". Those pairs show as "Could not be judged". A run where the judge read fewer than half the pairs is flagged "Judge failed" in the report and on the page, is not added to the pool, and never replaces an earlier result.
- One damaged entry in a saved pool no longer stops that skill from pooling.
- `verdicts.json` is written under a lock, and `live.json` through a unique temp file, so two runs cannot overwrite each other.
- Page fixes: the dashboard tab no longer shows an error, the summary counts Plausible and Probable apart, Self-judged shows once, the tags are easier to read, Stop asks before it stops a run, and Show in chat sends fixed text.

## v0.1.0 (12 Nov 2026)

The hackathon entry, commit `0affc46`, as pinned in the Hermes plugin catalogue. `hermes plugins install skilluminati` installs this version.
