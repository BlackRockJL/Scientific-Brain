#!/bin/sh
# Forwards a Claude Code hook event to `sb hook`; without the CLI only session-start says so.
if ! command -v sb >/dev/null 2>&1; then
  [ "$1" = session-start ] && echo "Scientific Brain: 'sb' is not on PATH, the plugin is inactive (see README, 'Set up with your agent')."
  exit 0
fi
exec sb hook "$1"
