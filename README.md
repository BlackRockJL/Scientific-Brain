# Scientific Brain

A lean second brain for research and scientific computing, maintained by an LLM agent such as Claude Code
and modelled on human memory.

- **Selective:** only knowledge with rank becomes a note (own results, decisions, derivations, assessed
  sources, open questions). Everything else is a one-line inbox entry.
- **Sleep:** a pass, weekly or as often as work arrives, consolidates the inbox, merges near-duplicates, forgets, verifies and dreams (proposes new links).
- **Forgetting:** usage is logged; notes that are no longer used fade into an archive. Nothing is deleted.
- **Small and fast:** pure Python ≥ 3.10, no dependencies (`tomli` is pulled in below 3.11), SQLite full-text search.

It has two parts: the command line tool `sb`, which owns the notes, and a Claude Code plugin, which teaches the
agent when to call it.

## Set up with your agent

Give Claude Code this sentence:

> Set up the Scientific Brain from https://github.com/BlackRockJL/Scientific-Brain for me, following the
> section "Set up with your agent" of its README. Put the vault at `~/research-brain`.

The agent then works through these steps:

1. **Prerequisites.** Python ≥ 3.10 (`python3 --version`), git and [pipx](https://pipx.pypa.io). pipx is not
   part of Python; on macOS `brew install pipx`, on Debian or Ubuntu `sudo apt install pipx`, then
   `pipx ensurepath`. `sb` is on the `PATH` only in a new shell, so open one (or `exec $SHELL`) before step 2
   if `sb` is not found.
2. **Install the CLI.**
   ```bash
   pipx install git+https://github.com/BlackRockJL/Scientific-Brain@refs/tags/v0.2.1
   sb --version
   ```
   This form follows the releases (see [Update and uninstall](#update-and-uninstall)); `@v0.2.1` instead
   keeps this version.
3. **Create the vault.** `sb init ~/research-brain` (any folder works; see [Where the vault lives](#where-the-vault-lives)).
4. **Tell Claude Code where it is.** Add to `~/.claude/settings.json` (or a project's `.claude/settings.json`),
   with an absolute path (`~` does not work in JSON), e.g. `/home/you/research-brain` or
   `/Users/you/research-brain`:
   ```json
   { "env": { "SB_VAULT": "/absolute/path/to/research-brain" } }
   ```
   An `export` in a shell is not enough, because the hooks run in Claude Code's own environment. `sb` must also
   be on that `PATH`; `pipx ensurepath` takes care of that for new sessions.
5. **Install the plugin.** From the shell:
   ```bash
   claude plugin marketplace add BlackRockJL/Scientific-Brain
   claude plugin install scientific-brain@scientific-brain
   ```
   Inside Claude Code the same is `/plugin marketplace add BlackRockJL/Scientific-Brain` and
   `/plugin install scientific-brain@scientific-brain`.
6. **Restart Claude Code** so that `SB_VAULT`, `PATH` and the plugin take effect. An agent cannot restart
   Claude Code itself: it asks the user to do so and continues afterwards.
7. **Verify.** `sb stats` prints the counts of the empty vault. A new session in an empty vault starts without
   a "Scientific Brain: …" line; that line appears when `sb` or the vault cannot be found, and "sleep due: …"
   appears once a sleep is due (see [Everyday use](#everyday-use)).
8. **Optional:** add the snippet from [Everyday use](#everyday-use) to `CLAUDE.md`.
9. **Note:** self-update is on by default and follows release tags only; `[update] auto = false` in `sb.toml`
   switches it off (see [Update and uninstall](#update-and-uninstall)).

## Quick start

```bash
sb init ~/research-brain && export SB_VAULT=~/research-brain
sb capture "FTCS diverges at r = 0.52" -p heat -s runs/sweep.csv   # inbox line, prints its id
sb new finding ftcs-stability-limit -p heat --why "own measurement" # note skeleton full of TODO
sb check                                                            # error: unfilled TODO placeholder
```

`sb new` writes a skeleton whose fields and sections read `TODO`; the agent, or you, fills them in. Until then
`sb check` exits with code 1 and names each placeholder with its line number, which is intended: a note counts only once it is
complete. After filling it in:

```bash
sb check
sb recall ftcs stability
```

`sb capture` prints only the id of the new inbox line (e.g. `7433e1`); `sb inbox` lists the open lines, and the
next sleep promotes, keeps or drops them. A line that waited too long expires; `sb sleep done` reports it, and
`sb inbox --expired` lists those lines.

To import an existing collection of notes:

1. copy the notes into `notes/<project>/` of the vault;
2. `sb check` and fix what it reports;
3. `sb sleep baseline --seed 365` (any number of days): the notes are accepted as they are, so the first sleeps
   verify only what changes from then on. Without a usage history every older note would otherwise be a forget
   candidate at once; the seed lets them fade one by one over that many days.

`baseline` brings the search index up to date itself, so a vault that was used before the import is no problem.

## Dreaming

People recombine what they know while they sleep; a note that nothing links to stays a filing-cabinet entry.
So the fifth phase of every sleep offers a few pairs of notes that are not linked yet (at most `dream_pairs`,
default 5, 0 switches it off; they cost none of the sleep budget). The tool proposes the candidates
mechanically: related notes from different projects and from the same project, seeded by new, changed and
active notes, and one deliberately distant pair (a concept or derivation against a finding or question from
another project that shares at most one key word). Duplicates, linked pairs and notes that already carry the
maximum number of links are left out, and each note appears in one pair per sleep. The sleeper agent then judges
every pair on its own and links it only when one note changes how the other is read. There is no quota: no link
is the normal outcome. A pair judged unrelated (`sb sleep unrelated <a> <b> "reason"`) is locked and returns after
a cooldown that fades like note activation (`h_unrelated`, default 90 days); a pair rejected again later stays out
much longer. `sb sleep done` reports how many pairs were linked, rejected or left open.

## Everyday use

Once set up, nothing needs to be done by hand. The plugin brings

- the skills **recall** (look up what is known before technical work), **capture** (store what a task taught)
  and **sleep** (maintenance), which Claude Code picks by their descriptions;
- a **session-start hook** that stays silent unless `sb` or the vault is missing or a sleep is due. A sleep is
  due when the vault has notes and has never slept, when the last sleep is more than 7 days ago, or when more
  than 30 inbox lines are open (also when enough work has piled up since the last sleep);
- an **after-edit hook** that checks each note the agent writes and reports schema errors back to it;
- the **sleeper** agent, which runs a sleep when one is due. Other sessions may keep working in the vault while
  it sleeps; their use of notes still counts.

Skills fire only when the agent judges them relevant. To make it use the brain on every task, add this to the
`CLAUDE.md` of your home folder or project:

```markdown
## Scientific Brain
- Before technical work, run `sb recall <terms>` and build on what it returns.
- Before finishing a task, store what a later session would need: `sb capture "…" -s <source>`, or a note
  with `sb new … --why` if it has rank. `sb rules` has the full rules.
- When a session starts with "sleep due", run the sleep skill.
```

## Where the vault lives

The vault is a plain folder that you choose, separate from the code, much like a git repository. `sb` finds it
in this order:

1. the folder named by `SB_VAULT`;
2. otherwise the nearest folder at or above the current directory that contains an `sb.toml`.

`sb init <folder>` creates this layout:

```
sb.toml              projects, source roots, limits
notes/<project>/     active notes, one Markdown file each
archive/<project>/   forgotten or superseded notes, restorable
.sb/                 inbox, usage and sleep logs, search index (written by sb only)
```

The vault can be versioned with git; `sb init` writes a `.gitignore` for the search index.

## Update and uninstall

Self-update is on by default (`auto = true` under `[update]` in `sb.toml`): each sleep starts by bringing
both parts up to the newest release tag `vX.Y.Z` of the repository (never `HEAD`): the CLI with
`pipx install --force git+<repository>@refs/tags/<tag>` for a pipx install, the plugin with `claude plugin marketplace update`
and `claude plugin update`. The plugin follows the same switch. The marketplace entry points at the plugin as of
the current release tag (`ref`), so the plugin, its hooks included, changes only with a release, never with a
commit on the default branch. A new plugin loads after a restart of Claude Code.
Only an install without a pin or pinned as `@refs/tags/vX.Y.Z` (the form sb uses itself) follows the releases.
Any other pin, `@vX.Y.Z` included, and developer (editable) installs are never touched; they only get a hint.
Without a release tag in the repository only a hint appears. Switch it off with `auto = false` or with
`SB_NO_UPDATE=1`.

By hand: `pipx install --force git+https://github.com/BlackRockJL/Scientific-Brain@refs/tags/<tag>` and
`/plugin marketplace update scientific-brain` in Claude Code.

A release sets the same version in `pyproject.toml`, `__init__.py`, `plugin/.claude-plugin/plugin.json`, the
`ref` in `.claude-plugin/marketplace.json` and the install line above (a test checks this), then tags that
commit `vX.Y.Z` and pushes branch and tag together: `git push --atomic origin main vX.Y.Z`.

A `sb rename` is all or nothing: if any note cannot be read or written, nothing is changed.

To remove it: `/plugin uninstall scientific-brain@scientific-brain`, `pipx uninstall scientific-brain`, and the
`SB_VAULT` entry in `settings.json`. The vault is a plain folder and stays untouched.

## More

`sb --help` lists all commands, `sb rules` prints the rules for the agent, and `examples/vault/` holds a small
demo vault. The design is explained in [docs/design.md](docs/design.md).

## Development

`pip install -e '.[test]'` and `pytest`.

## License

MIT
