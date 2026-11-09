---
title: FTCS stays stable up to a mesh ratio of one
type: finding
project: heat
created: 2026-10-30
updated: 2026-11-02
cue: was needed when choosing the explicit time step
sources: [runs/ftcs-sweep/summary.csv]
why: first guess before the sweep
superseded_by: [ftcs-stability-limit]
archived: 2026-11-02
archived_why: superseded
---

Gist: Assumed that FTCS is stable up to r = 1; the sweep showed divergence already at r = 0.52.
