---
title: Observed order of convergence
type: concept
project: _general
created: 2026-11-04
updated: 2026-11-04
cue: needed when a study speaks of "order" and it is unclear which one is meant
why: the term is used in several notes and must be consistent
---

Gist: The observed order p is the slope of the error over the mesh width on a log-log scale, measured on a refinement sequence, not the formal order of the scheme.

It is always determined with the refinement rule from [[couple-time-step-to-mesh]] and, for smooth data, differs
from the formal order only by the measurement uncertainty. Computing it without an exact solution:
[[richardson-error-estimate]].
