---
name: sleep
description: Use when sb prints "sleep due", or when the user asks to consolidate, clean up or maintain the Scientific Brain. Runs one sleep through the sleeper agent.
allowed-tools: Bash(sb *)
---

1. Start the `scientific-brain:sleeper` agent with the task "Run one sleep of the Scientific Brain". Do not run the sleep
   in the main conversation; it would fill the context.
2. While it runs, do not write to the vault from the main conversation.
3. Relay its one-line report to the user. If it reports deferred items or a promotion-rate warning,
   mention them in that line.
