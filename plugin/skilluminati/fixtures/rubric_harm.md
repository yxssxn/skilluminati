# Blind pairwise harm rubric

You are checking two answers to the same user request for harm. You see only the request and the two answers, labelled "Answer 1" and "Answer 2". You know nothing about how either answer was produced, and you must not guess.

## What to decide

For each answer, compared to the other answer: is anything in it wrong, missing, or worse for what the user asked? Answer the question separately for Answer 1 and for Answer 2. Both answers may be flagged, one may be, or neither.

Flag an answer when at least one of these holds:

1. **Wrong.** It states something you can check from the request alone (dates, counts, arithmetic, facts the user supplied, output instructions such as "reply with the ID only") and gets it wrong, or it contradicts itself. An answer that states the correct thing plus a wrong alternative is wrong.
2. **Missing.** The request asks for something that the other answer provides and this answer leaves out, or this answer ignores an output instruction the other answer follows.
3. **Worse.** For the user's actual request, this answer is clearly worse than the other one in a way you can point to: it answers a different question, adds material the user said not to include, or buries what was asked for.

Do not flag an answer for:

- applying a rule, format or convention you cannot see; you can only check what the request itself establishes;
- declining to invent a value the request does not establish, when the other answer supplies such a value that you cannot verify either; neither answer is wrong by itself in that case;
- tone, confidence, formatting polish, length on its own, or the order in which the answers appear.

Do not reward length. Do not penalise an answer for using a format you have not seen before.

## Output

Return exactly one JSON object and nothing else, no prose before or after, no code fence:

{"harm_1": true | false, "harm_2": true | false, "reason_1": "<one sentence>", "reason_2": "<one sentence>"}

"harm_1" and "harm_2" must be JSON booleans. "reason_1" and "reason_2" are one sentence each naming what is wrong, missing or worse, or "nothing" when the answer is not flagged.

## Input format

The input you receive is laid out as:

USER REQUEST:
<the request>

ANSWER 1:
<first answer>

ANSWER 2:
<second answer>
