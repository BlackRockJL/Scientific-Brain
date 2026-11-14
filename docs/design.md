# Design

Why Scientific Brain is built the way it is. The user-facing description is in the [README](../README.md),
the agent contract in `src/scientific_brain/RULES.md` (`sb rules`).

## Problem

Knowledge stores maintained by an LLM agent tend to fail in three ways. They select nothing, so every session
adds notes and most are never read again. They forget nothing, so outdated and redundant notes compete with
good ones in every search. And their maintenance machinery (rules, review phases, logs) grows faster than the
knowledge it protects. What pays off is a strict schema with a checker, safe renaming, fast full-text search
and periodic checks of numbers and contradictions against the sources.

## Memory model

The design follows established findings from memory research, translated into mechanisms a small tool can
enforce:

- **Selective encoding.** Most experience is never stored; novelty and expected use gate encoding. Hence the
  rank requirement for notes and the `--why` line, and `sb new`, which writes nothing while similar notes
  exist unless it is run again with `--anyway`.
- **Complementary learning systems.** A fast store for episodes and a slow, consolidated store
  (McClelland, McNaughton & O'Reilly 1995; Kumaran, Hassabis & McClelland 2016). Hence inbox and notes, with
  promotion only during sleep.
- **Sleep consolidation and downscaling** (Klinzing, Niethard & Born 2019; Tononi & Cirelli 2014). Hence a
  periodic sleep that both consolidates and reduces.
- **Active forgetting** makes traces inaccessible rather than erasing them (Davis & Zhong 2017; Ryan &
  Frankland 2022). Hence the archive instead of deletion.
- **Use predicts need** (Anderson & Schooler 1991). Hence the usage log: what is used stays, what is not fades.
- **Spacing:** a memory retrieved shortly before it would be lost gains more stability than one retrieved again
  at once (Bjork & Bjork 1992; Cepeda et al. 2006), the model behind spaced-repetition schedulers such as FSRS
  (Ye, Su & Cao 2022). Hence one stability per note that grows with every use or veto.
- **Reconsolidation:** a retrieved memory becomes open to update (Nader, Schafe & LeDoux 2000). Hence the rule
  to correct a note while it is being used.
- **Encoding specificity** (Tulving & Thomson 1973): retrieval needs a matching cue. Hence the `cue` field.
- **Limited working memory** (Cowan 2001). Hence five gists per recall.

Two findings are deliberately not over-interpreted: whether sleep prefers future-relevant memories is
contested, and the synaptic homeostasis hypothesis is debated. The tool takes only the uncontroversial part:
growth needs periodic reduction.

## Storage

- Notes are Markdown with a strict YAML subset as frontmatter, parsed by the tool. Unknown fields are errors.
- The lifecycle is the location: inbox, `notes/`, `archive/`. There is no status field.
- Archived notes carry `archived`, `archived_why` (`faded`, `superseded`, `wrong`) and, where needed,
  `archived_reason` and `superseded_by`. Without the reason, a faded but true note and a wrong one would look
  the same; restoring a `wrong` note requires `--force`.
- Inbox, inbox results, usage, renames, vetoes, sleeps, unrelated pairs and listed notes are append-only TSV files under `.sb/`. Commands that
  read before they write (capture, inbox resolve, rename, archive, restore, sleep begin, baseline, verified and done,
  and the one-time migration of the snapshot) hold a lock on `.sb/lock`, so parallel sessions can append without
  losing lines, identical ones included. A `.gitignore` written by `sb init` ignores the lock file; an older one gets
  the line once, from `sb stats` or the session-start hook. `unrelated.tsv` and `listed.tsv` carry on each row the
  number of renames so far. Only `index.sqlite` (cache), `snapshot.tsv` (content hashes of the checked notes,
  each row with the number of renames it already accounts for and the version of the hash, so a freed name keeps its
  own row and a changed hash migrates once, without making unchanged notes unchecked) and the `sleeping` marker
  (start date, shown inbox lines, planned and verified notes, each line with the renames so far) are rewritten.
  Notes and the snapshot are replaced through a temporary file, so a crash leaves the old or the new text; `sb check`
  warns about a temporary file that a killed process left behind.
- A damaged `index.sqlite` is moved to `index.sqlite.broken` and rebuilt once (message on stderr). A locked index is
  waited for (10 s), then read as it is with a warning; an index not built yet gives "database is locked".
- `sb rename` and `sb archive --fix-links` are all or nothing: if any note cannot be read or written, nothing is
  changed. `archive` and `restore` write the target before they remove the source. `archive` refuses when the slug
  is in the archive already or two notes carry it. A note starting with a byte order mark fails `check` as such.

## Search

- **Pure Python, no compiled extension.** Profiling file-based note tools shows the cost in reading and
  parsing files, not in scoring. SQLite FTS5 (in the standard library, written in C) answers from an index.
- **Incremental index.** Each call checks `notes/` and the inbox by mtime and size; the archive is rescanned
  only when the mtime of `archive/` or one of its project folders changed, so files added there by hand count.
  A new file in an already indexed vault is an import, not an edit; a rebuild (version change, `--rebuild`,
  changed `skip_sections`) still reports the changed files the old index knew.
- **Text folding** on both index and query: lower case, `ß → ss`, umlauts and `ae/oe/ue` (not after a vowel or q,
  and not at the end of a word or before a final s or d there, so `due`, `values`, `does`, `continued` stay) folded to the base vowel. A trigram tokenizer finds parts of compounds and inflected
  forms. Every term is quoted, so hyphens, `C++` or `1e-8` cannot break a query; surrounding punctuation, a
  `field:` prefix and a possessive `'s` are stripped. Hyphens and dashes count as spaces in index and query, so
  `Crank-Nicolson` and `Crank Nicolson` find each other.
- **Filler words** (about a hundred German and English ones) are dropped from queries and from the keys the
  sleep plan searches with, so they neither match nor count towards coverage.
- **Short terms.** The trigram tokenizer cannot match terms under three characters; abbreviations such as `CG`
  are matched exactly against the words of slug and `cue` in a side table and rank after the bm25 matches.
- **Skipped sections.** Headings listed in `[index] skip_sections` keep their sections out of the full text,
  e.g. legacy metadata carried over from an import.
- **Coverage threshold instead of a score floor.** FTS5 clamps the IDF of a term that occurs in more than half
  of the documents, so in small vaults bm25 scores are unusable as a cut-off. A match must contain at least half
  of the search terms; bm25 with column weights `cue > title > gist > body` only orders the matches.
  Equal ranks are ordered by slug.

## Stability

Nothing about forgetting is set by hand; the vault learns it from its own log.

Usage events, at most one per note and day (the strongest counts): creation, `show`, `restore`, editing in a work
session and a sleep veto are full reviews, the first hit of `recall` half a one. Calls during a sleep do not count
by themselves, so maintenance cannot keep notes alive. `sb` cannot tell the sleeper from a parallel session, so the
sleep's calls are logged as sleep and `sleep done` gives `recall` and `show` back for every note, named by the plan
or not (the sleeper calls neither, so they are the main session's); an edit of a note the plan names lapses (taken for the
sleeper's own: verify fixes, merges, dream links), also when the main session made it. Vetoes count as they are. A marker older than a day is a forgotten one: it tags nothing and blocks nothing.
Edits found by the index sync are logged as `sync`, so that an import is not taken for use. A rename carries the
events logged before it to the new name, counted by row, so a name freed by it starts without history.

Each note has a stability `S`, the number of days it may rest unused. Replaying its events in order, the first
sets `S = S0`; every later review on a new day, of strength q, does

    S ← S · (1 + q (1 − R)),   R = 1 / (1 + t / S),   t = days since the last review

`R`, the retrievability, is 1 right after a review and 1/2 after `S` days; below 1/2 the note is a forget
candidate. Uses logged before a note's `created` date (an imported history) count; `created` itself restarts the
clock without growth, so no note is forgotten before it is `S` days old. Uses and seed rows dated after today are a wrong
clock and are left out. A review shortly before forgetting raises `S` by half, reviews in quick succession add almost nothing,
and a note kept again and again rests longer each time: a veto at the threshold gives 1.5 `S`, the next one
2.25 `S`. A half review must not cost what a later one gains: until the next full review the replay keeps the
states of the histories that skip the half reviews and grows the best of them, so one more use, or a stronger one,
never lowers `S` or `R`. With a fixed weight per event, as before, a kept note came back after a quarter of its horizon and the
same notes were judged over and over.

`S0` is learned per vault and type: the 90th percentile of the gaps between two days of real use of a note
(create, show, restore, edit, also one the index sync found, or a recall hit in a work session; vetoes only say a note
was kept, and a sync edit on or before a seed of the note belongs to its import),
since a note still in use is rarely left alone longer. Three biases are kept out. A gap counts only if it starts
more than 365 days ago, so every gap up to that length had the time to be seen; a young vault would otherwise learn
from its own short history. Each gap phases in over the following year and is capped at 365 days, so what is learned
moves smoothly once the log is a year old, and one stray old line cannot carry the vault. Each note weighs one, shared among its gaps, so much used notes do not outvote the
rest. The creation date counts as the first day only if no use came before it, so an imported history does not
count as use on the import day. The result is pulled towards 90 days with the weight of 20 notes, a type towards the
vault value in the same way (the vault value contains the type's own gaps, so in a vault of one type they count
twice and the 90 days weigh less than 20 notes), and stays within 30 to 365 days. A vault younger than a year therefore rests new notes
for 90 days, and the growth of `S` with each use and veto does the rest.

A note that an active note (one not below 1/2) links to is no candidate: sources and basic concepts are used
through the notes that cite them, rarely on their own. Links from faded notes do not count, so a faded chain or
cycle still fades.

## Sleep

The plan is computed without a model; the agent only judges.

- **Consolidate:** each open inbox line with its three most similar notes; pairs of similar inbox lines mark a
  finding captured twice.
- **Condense:** for each note updated since the last sleep, a search with the eight rarest words of its title
  and cue; a pair is listed when the best other note covers at least 60 % of them, measured both ways and the smaller share
  counting (an overview note contains the words of every note it summarises), and neither links the other
  (linked parts of one topic are not duplicates).
- **Forget:** the candidates, lowest retrievability first.
- **Verify:** notes not checked since they last changed, each with three nearest neighbours: a note is checked
  when the snapshot holds its content hash (ignoring the frontmatter field `verified:`, the `## Links` section and the targets of links, so a rename,
  repaired links and dream links leave a note checked; links turned into plain text by `archive --fix-links` without
  successor do count), or, never snapshotted, when its `verified` is not older than `updated`. Notes an earlier
  plan listed after their last `verified` go to the back, the longest unlisted first (`.sb/listed.tsv`), so notes that
  cannot be checked do not starve the others, while a checked note edited again keeps its place; then edited notes before never-checked ones, newer first, ties by slug. Then, among the
  twenty most active notes, the ten with the oldest `verified`.
- **Dream:** up to five pairs of notes that are not linked yet, outside the budget so that they are
  never crowded out. Seeds are the notes new or changed since the last sleep, then the most active ones (at most
  20); for each, a search with its eight rarest words gives related notes, which are cut by the checks for
  existing links, condense pairs and near-duplicates (coverage), the link limit and locked pairs, and by one
  appearance per note and sleep. Two pairs across projects, two within one (one kind fills up for the other), and
  one far pair: a concept or derivation among the fifty most active against a finding or question of another
  project sharing at most one key word of title and cue, picked by a daily hash. The agent judges each pair and
  logs a rejection with `sleep unrelated`; the pair then rests like a note, for the vault's `S0` after one
  rejection and longer after each further one. The phase does not make a sleep due, and `sleep done` counts linked,
  rejected and open pairs. A sleep shows one set: a second plan in the same sleep repeats the pairs still open
  instead of drawing new ones, so the count is what the agent saw. Near pairs are close by construction, so the
  bar is substance, not surprise: a pair is linked when one note uses, implements, corrects, measures or
  contradicts what the other states, or both share a mechanism, quantity or method; only a shared vocabulary
  is rejected. Sleep recombines what is known; without links a note stays a filing-cabinet entry. The
  candidates are cheap and deterministic, the judgement is expensive, hence few pairs, and forgetting rejections
  with the same stability keeps the memory of them as light as the memory of notes.
- **Budget:** a plan takes as many items as recent sleeps managed, learned from the size of each plan and the
  items it left undone (an inbox line not booked, a candidate neither archived nor vetoed, a changed note
  unverified), which `sleep done` logs. It starts at 40. A sleep that left more than a tenth of a full plan undone (one
  the budget filled; a smaller plan says nothing about the agent) sets the next plan to what it did and marks its
  size as too large, unless what it did is below 10; a sleep that finished a full plan (at most a tenth left) lets it grow by a quarter,
  but only halfway towards a size known to be too large, and once just below it, that limit moves up by one per
  sleep. The plan thus settles just below what the agent manages instead of swinging around it, and still finds a
  stronger agent again; it stays within 10 to 200. The plan is filled in the order consolidate, forget and condense
  (each capped by its share of the waiting work), verify (unchecked), the rest of forget and condense, verify
  (active); what does not fit is
  reported as deferred. The caps keep a wave of candidates from starving the other phases, and no budget is left
  unused while work waits. Size and work of a sleep are those of its first plan; a second plan in the same sleep
  shows what is left or deferred and adds no work.
- **Waiting lines.** A line on `--wait` leaves the plan until it is due; then it comes back once, for a
  note or a drop. So a wait costs no room in the plans before and never counts as work left undone.
- **Self-regulating period.** A sleep is due after seven days, or as soon as a full plan of work waits (open
  inbox lines, unchecked notes and forget candidates add up to the budget) and five hours have passed since the
  last one. The period thus follows the inflow: a quiet week gets one sleep, a busy one several. No sleep is due while one runs; stats, hook and recall then say nothing about it.
- `sleep done` snapshots only the notes verified in this sleep, each with the hash it had when it was verified (a
  later edit stays unchecked); `sleep begin` does the same for an abandoned sleep it replaces. It keeps the old hashes of the rest, so a
  deferred note stays in the plan until a sleep verifies it. It expires due inbox lines the plan showed and
  left open (a line waits at most `inbox_wait_days` from capture; a due line cannot be put on wait again) and reports them apart from the settled ones: an expired line is neither
  settled nor promoted, stays findable (`sb inbox --expired`, `recall --inbox`), can still be settled with `sb inbox resolve`, and is listed with id and text.
  A line with an unreadable date counts as very old, so it is due. It
  warns when more than a quarter of at least ten settled inbox lines were promoted, a sign that the rank
  filter has become too loose. A note restored from `wrong` is unchecked again, even unchanged.
- `sleep baseline` accepts every note in its current form, e.g. after importing an existing collection: it
  writes the hashes of all notes to the snapshot, so verify lists only what changes afterwards. `verified` is
  left alone and still tells which notes were never checked; no sleep is logged. It syncs the index first, and
  edits only that sync found do not count as use. With `--seed DAYS` it also
  gives every note without use one start use today in `.sb/seeds.tsv`; a sync edit of the same or an earlier day yields to it. Without it, an imported note counts only
  its `created` date and is a candidate at once when it is older than `S0`. A seed sets the stability so that
  the note rests T days, with T running from 30 to DAYS, the least recently updated note first (the file keeps the
  weight w = √((T + 1)/(H + 1)) of earlier versions, H = 90, 180 for decisions and derivations). So an import of 1000 notes with `--seed 365` brings about three forget candidates a day instead
  of all of them in the first sleep.

## Review

`sb review` takes stock without changing anything and logs no use, so looking does not keep notes alive. The
report counts notes by project and type, real use (seeds left out, calls during a sleep only as far as
`sleep done` gives them back to the main session; `--since` cuts off older events, for example an import that
wrote its history as use), hubs, unlinked notes and links between projects.
Its fading part forecasts per month how many notes reach their threshold without further use and sets
this against what recent sleeps decided, names the learned `S0` and budget, and warns when candidates pile up
faster than sleeps settle them. A map is optional (`--map` to PNG or SVG, extra `review` with numpy and matplotlib):
an exact t-SNE of an affinity from links and TF-IDF text similarity places related notes close, colour marks
the project, shape the type, size the number of links, and opacity the days a note has left before it fades,
so forget candidates stay faint. Without the extra the report carries a Mermaid mind map instead.

## Rules and integration

The rules exist once, as package data, and `sb rules` prints them; skills and the sleep agent call it, so
rules and tool always match. The plugin ships four short skills, the `sleeper` agent and two hooks. The hooks
call `sb hook`, which is silent unless an edited note fails `check` or a sleep is due.

## Related work

No known project combines these parts. hippo-memory has two stores, a sleep command and decay for coding
sessions; its own tests found that age decay and sleep consolidation did not improve recall. Forgetting here
therefore aims at a small store and a clean search space, and its effect should be measured from the usage
log. CortexGraph promotes short-term entries into Markdown after repeated use, mnemos updates traces on
retrieval, pensyve-core implements ACT-R activation, and basic-memory and the LLM-wiki pattern maintain
Markdown knowledge bases without forgetting. Of the systems we looked at (November 2026), none proposes links
between stored notes pair by pair in a periodic pass with a model judging each pair, deliberately distant pairs
and a decaying memory of rejected pairs; A-MEM links only at insertion time, and SCM draws edges by random walks
without a judgement per pair.
