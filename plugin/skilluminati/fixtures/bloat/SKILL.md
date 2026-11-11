---
name: tallowgate-ledger
description: Use when opening, naming or writing up an incident for the Tallowgate ops desk. Ledger IDs, bands, header lines and short forms, with background on how the ledger came to be and how the desk uses it day to day.
---

# Tallowgate incident ledger

The Tallowgate ops desk records every incident in one ledger. Each entry has a fixed ID, a band and a header line. Get these exactly right: the ledger is parsed by script and a malformed entry is silently dropped.

## Background and purpose

The ledger is the desk's long-term memory, and it is worth understanding why it looks the way it does before learning the rules, because the rules make more sense once the reasoning behind them is clear. Before the ledger existed, incidents were recorded wherever they happened to start. Some were written up in a wiki page, some in a chat thread, some in a ticket, and a fair number were not written up at all, surviving only as a vague recollection that "something happened with the queue that one time". When the desk tried to count how many incidents of a particular kind had happened in a quarter, the answer depended on who was asked and how good their memory was that day. That was the problem the ledger was designed to fix.

The design brief was simple: one place, one shape, machine-readable, and cheap enough to fill in that nobody would skip it under pressure. The format described below is the result of several rounds of trimming. Early drafts carried a severity scale with five levels, a separate field for the service affected, a free-text title in the ID itself, and a priority flag. Each of these was removed after it turned out either that nobody filled it in consistently, or that it duplicated information that lived better in the body of the entry. What remains is the minimum that lets the parsing script sort and count entries without reading the prose.

A note on the name: the ledger was originally called the incident register, and some older documents still use that term. Treat them as the same thing. The word "ledger" won out because the desk liked the connotation of a running, append-only record where entries are never edited away, only amended with a later entry.

## Ledger ID

Format: `TG-YYMM-NNN/B`

- `TG-` is the fixed prefix.
- `YYMM` is the two-digit year and two-digit month in which the incident was opened. An incident opened on 7 November 2026 carries `2611`.
- `NNN` is the running count of incidents opened so far in that calendar year, zero-padded to three digits. The 41st incident of the year is `041`. The count never resets at a month boundary; it resets only on 1 January.
- `/B` is the band letter: one capital letter after a forward slash (see Bands).

Examples: `TG-2603-009/K`, `TG-2611-041/F`.

### Why the ID is shaped like this

The prefix exists so that an ID is recognisable out of context. A bare number in a commit message could be anything; `TG-` makes it unmistakably a ledger reference, and it is short enough that people actually type it. The year and month segment was chosen over a full date because the full date made IDs long enough that people started abbreviating them inconsistently, and because the month is sufficient for the kind of browsing the desk does (looking at "what happened in March"). The exact opened date lives in the header line instead, where it is not in the way.

The running count was the subject of the longest discussion in the design. A per-month count was tried first and rejected, because it meant that the 3rd incident of March and the 3rd incident of April both read as `003`, and people confused them constantly when the month segment was dropped in conversation. A per-year count gives every incident in a year a unique number, which is what makes the short form (below) usable at all. Three digits was judged sufficient; the desk has never approached a thousand incidents in a year, and if it ever does the format can be revisited, which would frankly be the least of the desk's concerns at that point.

The band is carried in the ID itself, rather than in a separate field, so that anyone scanning a list of IDs can see at a glance which incidents were the painful ones. This has proved useful in retrospectives, where a column of IDs with the bands visible tells a story on its own.

## Bands

The band is set by customer-facing impact: the time from the first customer-visible symptom to full restore.

- `K` marks impact under sixty minutes; `F` marks impact of sixty minutes or more.

The band is decided once, at opening, from the best estimate at the time, and is not revised later. Internal-only incidents with no customer-visible symptom take their band from the internal impact window instead.

### Notes on banding in practice

The two-band scheme is deliberately coarse. Earlier versions had five levels, and the desk found that most of the time spent on an entry went into arguing about whether something was a level two or a level three. Two bands with a single clean threshold removed the argument. Sixty minutes was chosen because it roughly matched the point at which customers started contacting support rather than simply retrying, which is the point at which an incident starts to cost the desk time beyond the fix itself.

The decision to fix the band at opening and never revise it caused some unease when it was introduced. The worry was that an incident estimated at twenty minutes which then ran for three hours would be mislabelled forever. In practice this happens rarely, and when it does the body of the entry records the real duration, so nothing is lost. The benefit of the rule is that nobody has to go back and rewrite IDs in commit messages, tickets and chat history when an estimate turns out to be wrong, which was the nightmare the rule was written to prevent. If the band at opening turns out to have been badly wrong, say so in the post-mortem and move on.

The letters themselves have no mnemonic. They were chosen because they are visually distinct from each other, distinct from the digits either side of them in the ID, and not easily confused in handwriting on the desk's whiteboard. Do not try to derive meaning from them.

## Header line

Every incident write-up (report, post-mortem, handover note) starts with one header line, exactly:

`LEDGER <ID> opened <YYYY-MM-DD>`

For example: `LEDGER TG-2611-041/F opened 2026-11-07`

Nothing precedes the header line: no title, no blank line.

### Why the header line is strict

The header line is what the parsing script looks for. It reads the first line of each write-up, checks it against the expected shape, and pulls out the ID and the opened date. If the first line is a title, a blank line or a note to the reader, the script does not find a header, treats the document as not being a write-up, and ignores it. The write-up then silently fails to appear in the ledger's index, which is the failure mode the strictness is designed to avoid. People have lost post-mortems this way, not because the file was deleted but because it was never indexed, and nobody noticed until they went looking for it months later.

The full opened date appears here rather than in the ID so that the ID stays short while the date remains available to the script for sorting. The word `opened` is lower-case and the date is ISO format with hyphens; both are checked literally.

## Short form

In chat, commit messages and ticket titles, refer to an incident by its short form: a hash, the running count and the band, with the year and month dropped. The incident above is `#041/F`. Never drop the band from the short form.

### Short form in practice

The short form exists because people were not typing the full ID in chat and were inventing their own abbreviations instead, some of which collided. The running count is unique within a year, so the hash and count alone identify the incident to anyone working in the current year, and the band is kept because it carries real information and costs two characters. If you need to refer to an incident from a previous year in chat, use the full ID; the short form is for current-year incidents only. Commit messages that reference an incident should carry the short form at the start of the subject line so that the commit log can be grepped by incident.

## Frequently asked questions

**What if two incidents are opened at the same time?** Whoever opens the second one takes the next number. The desk lead resolves any clash on the day. It has happened twice.

**What about an incident that spans the new year?** It keeps the ID it was opened with. An incident opened in late December keeps its December year and count even if it is still open in January; the January count starts fresh from `001` for incidents opened in January.

**Does a near miss get an ID?** Yes. Everything that is recorded in the ledger gets an entry, and every entry gets an ID. Near misses typically band as `K` because the customer-facing impact is small or zero, and if it is zero the internal window applies.

**Can I reserve an ID in advance?** No. Numbers are taken in opening order. Reserving creates gaps and gaps confuse the script's sanity checks.

**Is the ledger the same as the status page?** No. The status page is customer-facing and uses its own wording. The ledger is internal. They should agree on the facts, but neither is generated from the other.

## Checklist

1. The opened date gives `YYMM`.
2. The year's running count, three digits, gives `NNN`.
3. Impact minutes give the band.
4. Write-ups start with the `LEDGER` header line.
5. Chat and tickets use `#NNN/B`.
