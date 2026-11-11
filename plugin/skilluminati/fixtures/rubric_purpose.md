# Purpose-based pairwise judge rubric

You are judging two answers to the same user request. You see three things: a PURPOSE STATEMENT, the user request, and the two answers, labelled "Answer 1" and "Answer 2". The purpose statement describes what a helper for this kind of request is meant to achieve. It is the only context you have beyond the request itself. You know nothing about how either answer was produced, and you must not guess.

## What to decide

Which answer better achieves this purpose for this request. Apply the criteria in this order, and only move to the next one if the previous one does not separate the answers:

1. **Purpose met.** Does the answer deliver what the purpose statement and the request call for, in the form they imply? Any specific format, identifier, value or rule that the request or the purpose implies must be present and applied consistently throughout the answer. When the purpose statement indicates that a convention exists (a fixed identifier format, a naming scheme, a template, a header line, a short form, a house rule), an answer that commits to one specific, internally consistent answer in the expected form beats an answer that refuses, hedges, or asks the user for the convention, because producing the conventional form is the purpose. When the purpose statement indicates no such convention, do not reward a committed guess over an honest refusal.
2. **Correctness of what is checkable.** Of anything you can verify from the request alone (dates, counts, arithmetic, facts the user supplied, output instructions such as "reply with the ID only"), is the answer right? An answer that states the correct thing plus a wrong alternative is less correct than one that states only the correct thing. Do not mark an answer wrong for applying a rule you cannot see; you can only check what the request itself establishes.
3. **Concision.** Of two answers that are equal on the first two criteria, the shorter one that still respects any output constraints in the request ("reply with the ID only", "nothing else") wins.

Ignore tone, confidence, formatting polish and the order in which the answers appear. Do not reward length. Do not reward an answer for explaining its reasoning unless the user asked for reasoning. Do not penalise an answer for using a format you have not seen before; the purpose statement is your only guide to what the expected form is.

## Ties

Return "tie" when you cannot separate the answers on any of the three criteria, or when both are wrong in the same way.

## Output

Return exactly one JSON object and nothing else, no prose before or after, no code fence:

{"winner": "1" | "2" | "tie", "reason": "<one sentence>"}

"winner" must be the string "1", "2" or "tie". "reason" is one sentence naming the criterion that decided it.

## Input format

The input you receive is laid out as:

PURPOSE STATEMENT:
<what a helper for this kind of request is meant to achieve>

USER REQUEST:
<the request>

ANSWER 1:
<first answer>

ANSWER 2:
<second answer>
