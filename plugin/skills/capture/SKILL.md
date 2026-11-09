---
name: capture
description: Use when a task produced new research knowledge worth keeping, such as a measured result with its condition, a failed approach and why, a decision with its reason, a derivation, or an assessment of a paper. Decides between an inbox line and a note.
allowed-tools: Bash(sb *)
---

1. Default: `sb capture "one line: what, under which condition" -s <source path or citation key>`.
   That is enough; the next sleep decides what becomes a note.
2. A note directly only if it has rank (`sb rules`, section Write) and you can state it in one line:
   `sb new <type> <slug> -p <project> --why "…"`. Read the similar notes it prints first; extend one of
   them instead if possible.
3. Fill every placeholder, keep the limits that `sb rules` prints (title, body, links),
   then run `sb check`.
4. Never capture conversation state, run status, logs, naming decisions or summaries without own
   assessment. No bulk import unless the user asks for it.
