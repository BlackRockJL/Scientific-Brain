---
name: review
description: Use when the user asks how the Scientific Brain is doing as a whole - its structure, what is used, what fades, whether the sleeps keep up - or wants an overview or map of it. Read-only; not for looking up a topic (that is recall).
allowed-tools: Bash(sb *)
---

1. Run `sb review -o <report.md> --map <map.png>` into a scratch or output folder the user approves; without
   numpy and matplotlib leave out `--map`, the report then carries a Mermaid mind map. `sb review <project>`
   narrows it to one project. If old history was imported as use, pass `--since <date of the import>`.
2. Read the report. Give the user its warnings first, then a short judgement in your own words: which
   projects carry the work, what fades, whether the sleeps keep up, what the structure says (hubs, unlinked notes,
   prefixes of another type).
3. Propose follow-up actions (a sleep, archiving a note, settling inbox lines) only with the numbers from the
   report, and change nothing without the user's yes. sb learns its own limits; `sb.toml` has nothing to tune.
   The review itself never changes the vault.
