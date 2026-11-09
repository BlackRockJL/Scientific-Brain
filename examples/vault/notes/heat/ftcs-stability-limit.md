---
title: FTCS blows up above a mesh ratio of one half
type: finding
project: heat
created: 2026-11-02
updated: 2026-11-02
cue: needed when choosing the time step of an explicit scheme
sources: [runs/ftcs-sweep/summary.csv, src/ftcs.py]
why: own sweep, confirms the textbook limit and gives the failure size
verified: 2026-11-05
---

Gist: FTCS for the heat equation is stable for r = alpha*dt/dx^2 <= 0.5 and already diverges at r = 0.52.

In the sweep (N = 64) the maximum error was 3.3e-4 at r = 0.50 and 2.8e+12 at r = 0.52. The limit is sharp, so
no safety margin is needed below 0.5, and none helps above it. Cost follows: dt = 0.5*dx^2/alpha, which is why
[[scheme-choice]] moves to an implicit scheme.

## Links
- [[scheme-choice]] — the consequence of this limit
