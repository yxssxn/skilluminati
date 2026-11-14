# How the judging works

Skilluminati asks one question about a skill: on the work you actually use it for, are the answers better with it or without it?

## A run

For each of the latest 12 times you used the skill, Skilluminati takes the chat as it stood just before the skill was opened. Your model then answers that moment twice. One answer gets the skill, delivered the way Hermes delivers it in a live chat. The other gets the same chat with the skill taken out of the list of available skills. Nothing else differs.

A skill with fewer than 6 usable uses in your history is tested on its built-in questions instead, if it has a set ([packs.md](packs.md)).

## What makes it fair

**The Owl is blind.** It sees the request, the two answers and the skill's one-line description of its job. It is not told which answer had the skill and never sees the skill's text.

**Every pair is judged in both orders.** A model can favour whichever answer it reads first, so it sees each pair twice with the answers swapped, and only a pick that holds both ways counts. If it changes its mind, the pair counts as Same. 'See what the Owl saw' reports how often that happened.

**There is a same-vs-same check.** On up to 4 tasks per run, the Owl also compares two answers made the same way, both without the skill. Neither had the skill, so a firm pick between them (the same winner in both orders) is a difference the skill did not cause. These pairs are added up across runs, per model and judge, and every report says "Same-vs-same: a firm pick N of M times". Under 8 pairs it says there are not enough yet. At 50% or more it adds a note that single-run leads are weak on this model. On the author's machine the figure is 37 of 59 (63%), for one model judging itself.

**The instructions are locked.** The Owl's instructions are fixed files, each with a fingerprint (a SHA-256 checksum that changes if the file changes). A run refuses to start if one was changed.

**The self-test proves it can tell.** `/skilluminati selftest` (the Owl's trial) plants three versions of a made-up skill: one that helps, one with wrong rules planted in it and one that says nothing useful. Their answers can be marked by script, so we know the right call for each. If the Owl cannot sort them, it should not be trusted on your skills. The self-test uses a stricter rule than the everyday verdict: it calls a planted skill Unproven unless the result passes a standard statistical test at 95% confidence.

## How the verdict is decided

- Fewer than 6 tasks: **Unproven** (too few uses to tell yet). A run with fewer than 6 usable uses says so before it makes any call.
- Better beats Worse by at least a third of all tasks: **Enlightened** (answers are clearly better with it).
- Worse beats Better by at least a third of all tasks: **Hindrance** (answers are worse with it).
- Anything closer: **Dead weight** (the model does this anyway; the skill only costs tokens).

For example, Better 7, Same 4, Worse 1 out of 12 is Enlightened (a lead of 6, and a third of 12 is 4). That was the first of three runs of `systematic-debugging` on 12 uses from the author's own history.

Each verdict is then marked **Probable** (a clear, consistent result) or **Plausible** (a lean, worth re-testing with more uses). Enlightened and Hindrance are Probable when a standard statistical check agrees: the 95% range for the share of Better (a Wilson interval, over the tasks that were not Same) sits wholly on one side of 50%. Dead weight is Probable only when there are at least 12 tasks and at least half of them were Same, so a close split of Better and Worse stays Plausible. A Plausible Dead weight is shown as **No clear difference yet**, with the next step **Ask again**, because one run cannot tell no effect from a small one. Archiving is only suggested at Probable. The rule is in `plugin/skilluminati/verdict.py`.

For example, `unslop` on 12 of its uses scored Better 5, Same 5, Worse 2: the lead (3) is under a third of 12, so it is Dead weight, and with only 5 Same out of 12 it is Plausible. It shows as No clear difference yet.

## Asking again

A new run of the same skill version, on the same model and judge, is added to the earlier ones. Each task then counts once: Better when its Better answers outnumber its Worse ones, Worse the other way round, Same when they are level. The same rule as above turns those counts into the verdict, and the result says what it rests on, for example "12 tasks, 3 runs". A run that stopped early is not added. A changed skill file or a different model or judge starts again from nothing.

The three `systematic-debugging` runs above scored Better / Same / Worse 7 / 4 / 1, 5 / 6 / 1 and 7 / 1 / 4 on the same 12 tasks. Taken alone, the third shows No clear difference yet. Added together, they give 8 / 2 / 2: Enlightened (Plausible). `scripts/skilluminati_numbers.py` prints these figures from the stored runs.

## Flags

- **Skill changed:** the skill file now differs from the one that was tested.
- **Stale:** the result was made on another model than the one a new run would use.
- **Tool-driven:** half or more of the tested uses went straight to tool calls in the real chat (no answer of 200 characters or more in the text or in a content argument such as a file body), or came back empty here. The shown strength is held at Plausible.
- **Same-model judge** (tagged **Self-judged** on the page): the Owl is the model under test, so it is grading its own answers.
- **Heavy:** the skill adds at least twice the tokens of your median skill.
- **Few tasks:** fewer than 10 decisive tasks (Better or Worse; Same does not count).
- **Split decision:** the Owl leaned on answer order, so treat the result with care.

## What it cannot tell you

- **It tests one answer, not a whole session.** Each task is answered in one model call with no tools to use. A skill whose main job is to drive tools or helper agents over many steps is not judged fairly. The Tool-driven flag marks those results.
- **It assumes the skill was the right one to load.** It does not test whether your agent would have picked it.
- **The Owl is a model.** Blind judging, both orders and the same-vs-same check reduce its mistakes. They do not remove them. The same-vs-same figure says how often it finds a winner that is not there.
- **Your history sets the limit.** A skill you rarely use gives few tasks and a weaker verdict.
- **A verdict holds for one model.** It names the model it was run on, and shows Stale once you switch model. A stronger model may not need a skill a weaker one did.
- **12 tasks is a small sample.** A Plausible verdict can change on the next run. Ask again: the new run adds to the old one.
