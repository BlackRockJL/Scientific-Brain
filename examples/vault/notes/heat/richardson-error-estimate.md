---
title: Observed order from three grids without an exact solution
type: derivation
project: heat
created: 2026-11-05
updated: 2026-11-05
cue: needed when the exact solution is unknown or the reference solution is itself numerical
sources: [src/convergence.py]
why: own derivation of the three-grid formula including its assumption
---

Gist: With u_h = u + C*h^p + o(h^p) on grids h, h/2, h/4, the order is p = log2((u_h - u_h/2)/(u_h/2 - u_h/4)); no exact solution is needed.

Take solutions on three nested grids. Then u_h - u_h/2 = C*h^p*(1 - 2^-p) and u_h/2 - u_h/4 = C*(h/2)^p*(1 - 2^-p).
Their ratio is 2^p, so p follows from the logarithm. Assumption: the leading term dominates, which fails for
non-smooth data, see [[cn-oscillations-large-time-step]].

## Links
- [[observed-convergence-order]] — the quantity this computes
