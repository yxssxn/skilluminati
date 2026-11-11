---
name: tallowgate-ledger
description: Use when opening, naming or writing up an incident for the Tallowgate ops desk. Ledger IDs, bands, header lines and short forms.
---

# Tallowgate incident ledger

The Tallowgate ops desk records every incident in one ledger. Each entry has a fixed ID, a band and a header line. Get these exactly right: the ledger is parsed by script and a malformed entry is silently dropped.

## Ledger ID

Format: `TG-YYMM-NNN/B`

- `TG-` is the fixed prefix.
- `YYMM` is the two-digit year and two-digit month in which the incident was opened. An incident opened on 7 November 2026 carries `2611`.
- `NNN` is the running count of incidents opened so far in that calendar year, zero-padded to three digits. The 41st incident of the year is `041`. The count never resets at a month boundary; it resets only on 1 January.
- `/B` is the band letter: one capital letter after a forward slash (see Bands).

Examples: `TG-2603-009/K`, `TG-2611-041/F`.

## Bands

The band is set by customer-facing impact: the time from the first customer-visible symptom to full restore.

- `K` marks impact under sixty minutes; `F` marks impact of sixty minutes or more.

The band is decided once, at opening, from the best estimate at the time, and is not revised later. Internal-only incidents with no customer-visible symptom take their band from the internal impact window instead.

## Header line

Every incident write-up (report, post-mortem, handover note) starts with one header line, exactly:

`LEDGER <ID> opened <YYYY-MM-DD>`

For example: `LEDGER TG-2611-041/F opened 2026-11-07`

Nothing precedes the header line: no title, no blank line.

## Short form

In chat, commit messages and ticket titles, refer to an incident by its short form: a hash, the running count and the band, with the year and month dropped. The incident above is `#041/F`. Never drop the band from the short form.

## Checklist

1. The opened date gives `YYMM`.
2. The year's running count, three digits, gives `NNN`.
3. Impact minutes give the band.
4. Write-ups start with the `LEDGER` header line.
5. Chat and tickets use `#NNN/B`.
