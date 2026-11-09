---
title: Use Crank-Nicolson with Rannacher start-up
type: decision
project: heat
created: 2026-11-04
updated: 2026-11-04
cue: needed when someone proposes another time integrator for the study
why: fixes the scheme for all convergence runs
---

Gist: All convergence runs use Crank-Nicolson with two implicit Euler start-up steps; second order in time is the point of the study.

The study compares observed orders, so the scheme must be second order in time and not step-size limited.

## Rejected
- FTCS: stability needs dt <= 0.5*dx^2/alpha, see [[ftcs-stability-limit]]; runs would be too costly.
- Implicit Euler alone: first order in time hides the spatial error.
- BDF2: second order, but needs a start-up step as well and damps more; no benefit here.

## Links
- [[ftcs-stability-limit]] — why not explicit
- [[cn-oscillations-large-time-step]] — the known weakness
