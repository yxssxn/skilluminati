# How the judging works

Skilluminati asks one question about a skill: on the work you actually use it for, are the answers better with it or without it?

## The test

For each of the latest 12 times you used the skill, Skilluminati takes the chat as it stood just before the skill was opened. Your model then answers that moment twice. One answer gets the skill, delivered the way Hermes delivers it in a live chat. The other gets the same chat with the skill taken out of the list of available skills. Nothing else differs.

A skill with fewer than 6 usable uses in your history is tested on its built-in questions instead, if it has a set ([packs.md](packs.md)).

## What makes it fair

**The Owl is blind.** It sees the request, the two answers and the skill's one-line description of its job. It is not told which answer had the skill and never sees the skill's text.

**Every pair is judged in both orders.** A model can favour whichever answer it reads first, so it sees each pair twice with the answers swapped, and only a pick that holds both ways counts. If it changes its mind, the pair counts as Same. 'See what the Owl saw' reports how often that happened.

**There is a noise check.** On up to 4 tasks per test, the Owl also compares two answers made the same way, both without the skill. A fair Owl should find no winner. If it finds one, the working says so and you should treat a close result with care.

**The instructions are locked.** The Owl's instructions are fixed files with fingerprints. A run refuses to start if they were changed.

**The self-test proves it can tell.** `/skilluminati selftest` (the Owl's trial) plants three versions of a made-up skill: one that helps, one with wrong rules planted in it and one that says nothing useful. Their answers can be marked by script, so we know the right call for each. If the Owl cannot sort them, it should not be trusted on your skills. On 12 Nov 2026 it sorted all three: the Adept (helps) came out Enlightened, the Impostor (sabotages) Hindrance, caught doing harm on 6 of 6, and the Idler (does nothing) Unproven. The self-test uses a stricter rule than the everyday verdict: it calls a planted skill Unproven unless the result is clear at 95%, which is why the Idler at 2 / 2 / 2 reads Unproven.

## How the verdict is decided

- Fewer than 6 tasks: **Unproven** (too few uses to tell yet).
- Better beats Worse by at least a third of all tasks: **Enlightened** (answers are clearly better with it).
- Worse beats Better by at least a third of all tasks: **Hindrance** (answers are worse with it).
- Anything closer: **Dead weight** (the model does this anyway; the skill only costs tokens).

For example, Better 7, Same 4, Worse 1 out of 12 is Enlightened (a lead of 6, and a third of 12 is 4). That is the real result for `systematic-debugging` on 12 uses from the author's own history.

Each verdict is then marked **Probable** (a clear, consistent result) or **Plausible** (a lean, worth re-testing with more uses). Enlightened and Hindrance are Probable when a standard statistical check agrees: the 95% range for the share of Better (a Wilson interval, over the tasks that were not Same) sits wholly on one side of 50%. Dead weight is Probable only when there are at least 12 tasks and at least half of them were Same, so a close split of Better and Worse stays Plausible. The rule is in `plugin/skilluminati/verdict.py`.

For example, `unslop` on 12 of its uses scored Better 5, Same 5, Worse 2: the lead (3) is under a third of 12, so it is Dead weight, and with only 5 Same out of 12 it is Plausible.

## What it cannot tell you

- **It tests one answer, not a whole session.** Each task is answered in one model call with no tools to use. A skill whose main job is to drive tools or helper agents over many steps is not judged fairly.
- **It assumes the skill was the right one to load.** It does not test whether your agent would have picked it.
- **The Owl is a model.** Blind judging, both orders and the noise check reduce its mistakes. They do not remove them.
- **Your history sets the limit.** A skill you rarely use gives few tasks and a weaker verdict.
- **A verdict holds for one model.** It names the model it was run on. A stronger model may not need a skill a weaker one did, so re-test after you switch.
- **12 tasks is a small sample.** A Plausible verdict can change on the next run, so test again before you remove a skill on its strength.
