---
name: recall
description: Use before technical work on a research or simulation topic to check what the Scientific Brain already knows (findings, decisions, derivations, sources, open questions). Not for small tasks that need no background knowledge.
allowed-tools: Bash(sb *)
---

1. Run `sb rules` once per session if the rules are not yet in context; they are binding.
2. `sb recall <terms>` with three to six specific terms, in the language the notes are likely written in.
   Try a second phrasing before concluding that nothing is known. "no match" means nothing is known.
3. `sb show <slug>` only for notes whose gist is not enough.
4. Use what the notes say; do not re-derive it or ask the user again.
5. If a note turns out wrong or outdated while you use it, fix it now (edit the file, keep the limits) or
   archive it: `sb archive <slug> --why wrong --reason "…"` or `--why superseded --superseded-by <new>`.
6. If `recall` prints "sleep due", tell the main session; the `sleep` skill handles it.
