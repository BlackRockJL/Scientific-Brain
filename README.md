# Scientific Brain

A lean second brain for research and scientific computing, maintained by an LLM agent such as Claude Code
and modelled on human memory.

- **Selective:** only knowledge with rank becomes a note (own results, decisions, derivations, assessed
  sources, open questions). Everything else is a one-line inbox entry.
- **Sleep:** a regular pass consolidates the inbox, merges near-duplicates, forgets, verifies and dreams
  (proposes new links).
- **Forgetting:** every use makes a note more stable, the more so the closer it was to fading; notes that are no
  longer used fade into an archive. Nothing is deleted.
- **Nothing to tune:** how long notes rest and how much a sleep takes are learned from the vault's own logs.
- **Review:** `sb review` takes stock of the whole vault, as a report and as a map of the notes.
- **Small:** pure Python ≥ 3.10 and SQLite full-text search; only the map needs numpy and matplotlib.

It has two parts: the command line tool `sb`, which owns the notes, and a Claude Code plugin, which teaches the
agent when to call it.

## Set up with your agent

Give Claude Code this sentence:

> Set up the Scientific Brain from https://github.com/BlackRockJL/Scientific-Brain for me, following the
> section "Set up with your agent" of its README. Put the vault at `~/research-brain`.

The agent then works through these steps:

1. **Prerequisites.** Python ≥ 3.10, git and [pipx](https://pipx.pypa.io) (macOS `brew install pipx`, Debian
   or Ubuntu `sudo apt install pipx`, then `pipx ensurepath` and a new shell).
2. **Install the CLI.** This form follows the releases (see [Update](#update-and-uninstall)); `@v0.2.1`
   instead keeps this version.
   ```bash
   pipx install git+https://github.com/BlackRockJL/Scientific-Brain@refs/tags/v0.2.1
   sb --version
   ```
3. **Create the vault.** `sb init ~/research-brain` (any folder works, see [Where the vault lives](#where-the-vault-lives)).
4. **Tell Claude Code where it is.** Add to `~/.claude/settings.json` (or a project's `.claude/settings.json`)
   with an absolute path, since `~` does not work in JSON:
   ```json
   { "env": { "SB_VAULT": "/absolute/path/to/research-brain" } }
   ```
   An `export` in a shell is not enough, because the hooks run in Claude Code's own environment.
5. **Install the plugin.**
   ```bash
   claude plugin marketplace add BlackRockJL/Scientific-Brain
   claude plugin install scientific-brain@scientific-brain
   ```
   Inside Claude Code: `/plugin marketplace add BlackRockJL/Scientific-Brain`, then
   `/plugin install scientific-brain@scientific-brain`.
6. **Restart Claude Code** so that `SB_VAULT`, `PATH` and the plugin take effect. The agent asks you to do so
   and continues afterwards.
7. **Verify.** `sb stats` prints the counts of the empty vault. A new session starts without a
   "Scientific Brain: …" line; that line appears only when `sb` or the vault cannot be found.
8. **Optional:** add the snippet from [Everyday use](#everyday-use) to `CLAUDE.md`, and
   `pipx inject scientific-brain numpy matplotlib` for the map of [Review](#review).

## Quick start

```bash
sb init ~/research-brain && export SB_VAULT=~/research-brain
sb capture "FTCS diverges at r = 0.52" -p heat -s runs/sweep.csv   # inbox line, prints its id
sb new finding ftcs-stability-limit -p heat --why "own measurement" # note skeleton full of TODO
sb check                                                            # error: unfilled TODO placeholder
```

`sb new` writes a skeleton whose fields and sections read `TODO`. Until the agent or you fill them in,
`sb check` exits with code 1 and names each placeholder: a note counts only once it is complete. Then
`sb recall ftcs stability` finds it. `sb inbox` lists the open inbox lines; the next sleep promotes, keeps or
drops them, and a line that waited too long expires (`sb inbox --expired`).

To import an existing collection of notes, copy them into `notes/<project>/`, run `sb check` and fix what it
reports, then `sb sleep baseline --seed 365`. The notes are accepted as they are, and the seed lets them fade
one by one over that many days instead of all becoming forget candidates at once.

## Everyday use

Once set up, nothing needs to be done by hand. The plugin brings

- the skills **recall** (look up what is known before technical work), **capture** (store what a task taught),
  **sleep** (maintenance) and **review** (take stock), which Claude Code picks by their descriptions;
- a **session-start hook** that stays silent unless `sb` or the vault is missing or a sleep is due: never
  slept yet, more than 7 days since the last sleep, more than 30 open inbox lines, or a full sleep's worth of
  work waiting;
- an **after-edit hook** that checks each note the agent writes and reports schema errors back to it;
- the **sleeper** agent, which runs a due sleep. Other sessions may keep working in the vault meanwhile; their
  use of notes still counts.

Skills fire only when the agent judges them relevant. To make it use the brain on every task, add this to the
`CLAUDE.md` of your home folder or project:

```markdown
## Scientific Brain
- Before technical work, run `sb recall <terms>` and build on what it returns.
- Before finishing a task, store what a later session would need: `sb capture "…" -s <source>`, or a note
  with `sb new … --why` if it has rank. `sb rules` has the full rules.
- When a session starts with "sleep due", run the sleep skill.
```

## Review

```bash
sb review -o review.md --map brain.png
```

The review reads the vault and changes nothing; it does not even count as use. Ask the agent "how is my brain
doing?" and the **review** skill runs it and sums it up. The report holds

- warnings: forget candidates piling up faster than sleeps settle them, vetoed notes that come back too soon,
  overdue inbox lines, notes whose slug prefix belongs to another type, links to missing notes;
- the inventory by project and type, and the archive by reason;
- use per project and the most used notes;
- fading: what sb has learned, the current forget candidates, a forecast per month and what recent sleeps decided;
- structure: hubs, notes without links, links between projects;
- upkeep: unverified notes, open questions, the inbox.

The map (`--map`, PNG or SVG) places related notes close to each other, judged by their links and their text.
Colour marks the project, shape the type, size the number of links, and opacity the relevance: a note used often
and recently is bright, a forget candidate is faint. Without numpy and matplotlib the report carries a Mermaid
mind map instead.

`sb review <project>` looks at one project, `--exclude a,b` leaves projects off the map, and `--since
YYYY-MM-DD` counts only later use, for example after an import that wrote its history as use.

## Dreaming

People recombine what they know while they sleep. So the last phase of every sleep offers a few pairs of notes
that are not linked yet (at most five): related notes from the same and from
different projects, and one deliberately distant pair. The sleeper agent links a pair only when one note changes
how the other is read; no link is the normal outcome. A pair judged unrelated (`sb sleep unrelated <a> <b>
"reason"`) rests like a note that is not used, and longer once rejected again.

## Where the vault lives

The vault is a plain folder that you choose, separate from the code. `sb` uses the folder named by `SB_VAULT`,
otherwise the nearest folder at or above the current directory that contains an `sb.toml`. `sb init <folder>`
creates

```
sb.toml              projects, source roots, limits
notes/<project>/     active notes, one Markdown file each
archive/<project>/   forgotten or superseded notes, restorable
.sb/                 inbox, usage and sleep logs, search index (written by sb only)
```

The vault can be versioned with git; `sb init` writes a `.gitignore` for the search index.

## Update and uninstall

Each sleep first brings the CLI and the plugin up to the newest release tag `vX.Y.Z` (never `HEAD`). This
applies to installs without a pin or pinned as `@refs/tags/vX.Y.Z`; any other pin and editable installs only get
a hint. A new plugin loads after a restart of Claude Code. Switch it off with `auto = false` under `[update]` in
`sb.toml` or with `SB_NO_UPDATE=1`.

By hand: `pipx install --force git+https://github.com/BlackRockJL/Scientific-Brain@refs/tags/<tag>` and
`/plugin marketplace update scientific-brain` in Claude Code.

To remove it: `/plugin uninstall scientific-brain@scientific-brain`, `pipx uninstall scientific-brain`, and the
`SB_VAULT` entry in `settings.json`. The vault stays untouched.

## More

`sb --help` lists all commands, `sb rules` prints the rules for the agent, `examples/vault/` holds a small demo
vault, and [docs/design.md](docs/design.md) explains the design.

## Development

`pip install -e '.[test,review]'` and `pytest`.

A release sets the same version in `pyproject.toml`, `__init__.py`, `plugin/.claude-plugin/plugin.json`, the
`ref` in `.claude-plugin/marketplace.json` and the install line above (a test checks this), tags that commit
`vX.Y.Z` and pushes branch and tag together: `git push --atomic origin main vX.Y.Z`. The marketplace entry
points at the plugin as of that tag, so the plugin changes only with a release.

## License

MIT
