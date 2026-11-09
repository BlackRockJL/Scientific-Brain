---
title: Does the observed order survive a graded mesh?
type: question
project: heat
created: 2026-11-06
updated: 2026-11-06
cue: needed before extending the study to graded meshes near a boundary layer
why: the choice of scheme assumes a uniform mesh
---

Gist: Unknown whether Crank-Nicolson keeps second order on a smoothly graded spatial mesh; the three-grid formula assumes uniform refinement.

## Depends on
- [[scheme-choice]] — the scheme was chosen for uniform meshes
- [[richardson-error-estimate]] — its refinement assumption would need to change
