---
title: Choose the time step proportional to the mesh width
type: decision
project: heat
created: 2026-11-05
updated: 2026-11-05
cue: needed when a convergence study refines the mesh
why: fixes the refinement rule for all runs
---

Gist: At every refinement dt = c*dx, not dt ~ dx^2; the combined scheme is then second order and the cost stays moderate.

With c = 0.5, r is close to 8 at N = 64, in the low-oscillation range of the scheme from [[scheme-choice]].
The observed order follows [[observed-convergence-order]].

## Rejected
- dt ~ dx^2: too expensive, the cost grows with N^3 instead of N^2.
- A fixed time step: the time error dominates from N = 64 on and hides the spatial order.
