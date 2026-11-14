# Scientific Brain: rules for the agent

The commands below are the whole interface; `sb <command> --help` lists their options.

The brain holds knowledge about the work: results, decisions, derivations, sources, open questions. It does
not hold instructions about how you should work; those belong in your own memory.

## Read

- Recall for technical work, not before every small task. Run `sb recall <terms>` first; run `sb show <slug>`
  only for what you need. `sb map` lists titles when you want an overview.
- A note must contain a share of at least {min_coverage} of the terms; more terms narrow the search.
  Punctuation and filler words are ignored. Terms under three characters (`CG`) match only slug and `cue`.
  Nothing is translated; try German and English.
- What the brain says is not re-derived and not asked again. "no match" means nothing is known about these terms.
- `+N archived matches` means older, forgotten notes also match; `--archive` shows them. `+N inbox matches`
  means unsorted raw lines match; `--inbox` shows only those.
- The first hit of `recall`, notes `show` opens, edits and `restore` count as used; use keeps a note from
  being archived. Do not recall to keep notes alive.

## Write

- The default for anything new is the inbox: `sb capture "one line" -s <source>`; the project comes from the
  working folder or `-p`. It prints the line's id. `sb inbox` lists open lines, `--due` those older than
  {inbox_wait_days} days; `sb inbox resolve <id> --note <slug>` (an existing note), `--drop` or `--wait` settles one.
- A note is written directly only if it has rank and you can state the rank in one line (`--why`). `sb new`
  warns once {new_per_day} notes were created today:

  | Type | Rank |
  |---|---|
  | `finding` | own result with number and condition, or a pitfall with cause and fix; `sources:` |
  | `decision` | a choice with its reason; section `## Rejected` |
  | `derivation` | own derivation or proof step that is not in a source; `sources:` |
  | `source` | a paper or document with your own assessment: what it means, where it is weak; `ref:` is a DOI or a BibTeX key of `sb.toml` |
  | `question` | an open question that something depends on; section `## Depends on` |
  | `concept` | an own definition used by at least two other notes, not textbook material |

- Never a note: conversation state, run status, logs, naming decisions, summaries without own assessment,
  anything that code, version control or a project file already records.
- `sb new` prints similar notes first; if there are any, it writes nothing (exit 2, "not written"). Extend one,
  or rerun with `--anyway` if the note has rank of its own. One note, one topic; small facts about one thing
  belong together.
- Negative results have rank: an approach that failed, with the reason, is a `finding`.
- Limits are hard: title at most {title_words} words, body at most {body_words} words, at most {links} links, one project per note.
  If a topic does not fit, the detail belongs in the source, and the note links it with `sources:`.
- The first paragraph is the gist, shown by `recall`; it must read without the rest.
- `cue` states in one line when the note is needed; search weights it highest.
- Fill every `TODO` of a skeleton, then run `sb check`; it must pass without errors.
- No bulk import without an explicit instruction from the user.
- Use `sb rename`, never a manual rename, so that links and `superseded_by` follow.
- `sb archive <slug> --why faded|superseded|wrong [--reason "one sentence"] [--superseded-by <slug> ...]
  [--fix-links]` moves a note out of the default search and lists the notes that still link to it. `--why` is
  always explicit. `--reason` is required for
  `wrong` and for `superseded` without a successor. `--fix-links` points linking notes to the first successor,
  or without one removes the `## Links` bullets and turns inline links into plain text. Archived notes show
  their reason in `show` and `recall --archive`; `recall --archive` marks a wrong one with `WRONG:`.
- `sb restore <slug>` brings a note back, drops the archive fields and counts as use; a note archived as `wrong`
  needs `--force`, so fix its text first.

## Reconsolidate

- A retrieved note that turns out wrong or outdated is fixed in the same session, or archived (`--why wrong` or `superseded`, with `--superseded-by`). The note states the current truth; no correction banners.
- If you find a contradiction between two notes, fix the weaker one or ask the user which holds.

## Sleep

When sb says "sleep due" (a week since the last sleep, or a full plan of work waiting), the main session starts
the sleep agent. It works in this order:

1. `sb sleep begin`. Now edits of notes the plan names are not use. Read notes as files: `sb show` and
   `sb recall` count as use, as a parallel session's do. An unfinished sleep blocks a second one (`--force`
   replaces it).
2. `sb sleep plan` prints five lists, each entry with its slug or inbox id. Work them in order. Every note you
   change must still pass `sb check`.
   - Consolidate: settle every open inbox line with `sb inbox resolve`. Extend a similar note, or create one
     only with rank, then `--note <slug>`; otherwise `--drop` or `--wait` (at most {inbox_wait_days} days from capture; a DUE
     line cannot wait again and expires at `sleep done`). Lines captured twice are promoted.
   - Condense: for each near-duplicate pair merge into one note and archive the other with `--why superseded
     --superseded-by`, or keep both with sharper titles and cues.
   - Forget: archive each candidate with `--why faded`, or keep it with `sb sleep veto <slug> "one sentence"`.
     A veto counts as use.
   - Verify: for each new, changed or never verified note, check its numbers against the listed sources and
     compare it with the listed neighbours for contradictions; fix or archive. Then the most active notes listed. After checking,
     `sb sleep verified <slug> ...` sets `verified` without touching `updated`. Only verified notes leave the list. An answered question is archived with the answering note as successor.
   - Dream: link a pair (with a why) if they meet in substance, not only in words; else `sb sleep unrelated`.
3. `sb sleep done "one-line summary"` expires the due inbox lines the plan showed and you left open, logs the
   sleep and prints a summary, each expired line apart and the dream count. It warns when more than
   {max_promotion_rate} of at least ten settled inbox lines were promoted.

The plan touches at most {sleep_budget} notes and inbox lines, learned from past sleeps: inbox lines first, then
forget candidates and duplicate pairs, then verify, then the rest. It reports what did not fit as
`deferred`; do not work beyond it, but finish it: an unfinished plan shrinks the next.

## Never

- Delete a note file. Rewrite the inbox or the logs. Write outside the vault.
- Edit the index (a cache; `sb check --rebuild` rebuilds it).
- Edit `.sb/` files; only `sb` appends to them.

`sb stats` shows counts and sleep state, `sb review` an inventory.
