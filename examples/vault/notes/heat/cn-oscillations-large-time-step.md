---
title: Crank-Nicolson oscillates at large time steps
type: finding
project: heat
created: 2026-11-03
updated: 2026-11-03
cue: important when the time step is large and the initial data are not smooth
sources: [runs/ftcs-sweep/summary.csv, docs/heat-notes.txt]
why: own measurement; oscillations appear that FTCS does not show
---

Gist: Crank-Nicolson is unconditionally stable but barely damps high-frequency modes for r > 5; discontinuous initial data then oscillate visibly.

At r = 20 the amplification factor of the highest mode stays close to -1; the solution oscillates with a factor
of -0.9 per step. Remedies: a few implicit Euler start-up steps (Rannacher) or coupling the time step to the
smoothness of the data, see [[couple-time-step-to-mesh]]. The area under the error curve grows with the jump
height of the initial data, not with the mesh resolution.

## Links
- [[couple-time-step-to-mesh]] — the chosen remedy
- [[ftcs-stability-limit]] — the counterpart for the explicit scheme
