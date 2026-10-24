# Original-paper protocol reproduction on Jetson

Status: partial reproduction, with failures retained. Not a replacement for the original evidence or a completed confirmation.

Pinned unmodified upstream: `36fbae002b0a9e066416ea9cafbb966a33fe53bb` (CP2N2 v5.1.1).

## Original versus observed local overhead

All numbers are mean added wall-clock milliseconds, using the original 25 iterations per path. The standalone experiment was run after the aggregate campaign failed; both observations are retained, not pooled or selectively substituted.

| Backend | Original paper | Jetson aggregate run | Jetson standalone run |
| --- | ---: | ---: | ---: |
| chemical | 0.361 | 6.930 | 9.509 |
| wetware | 0.194 | 6.425 | 8.440 |
| edge | 0.189 | 5.846 | 9.968 |

These observations do not reproduce the publication's sub-millisecond values. They are not a hardware-only slowdown estimate: the paper's exact measured-code lineage, interpreter and dependency environment need to be reconciled with the author.

## Executed stages

- core: exit 1; unchanged upstream source: True; logs and checksum manifest retained.
- distributed-standalone: exit 1; unchanged upstream source: True; logs and checksum manifest retained.
- e3-control: exit 0; unchanged upstream source: True; logs and checksum manifest retained.
- e3-provider: exit 0; unchanged upstream source: True; logs and checksum manifest retained.
- overhead-standalone: exit 0; unchanged upstream source: True; logs and checksum manifest retained.
- tests: exit 2; unchanged upstream source: True; logs and checksum manifest retained.
- tests-python312: exit 0; unchanged upstream source: True; logs and checksum manifest retained.

## Conformance

- static_priority: 6/7 expected selections.
- constraint_based: 6/7 expected selections.
- random_admissible: 6/7 expected selections.
- weighted_comparison: 7/7 expected selections.
- cp2n2_lexicographic: 7/7 expected selections.
- Matching accuracy: 1.0; fault expectation rate: 1.0.
- CL SDK Simulator: 14 trials, 24 stimulations, 60 spikes; online/offline match: True.
- The initial Python 3.10 test run failed on missing cl.sim; the isolated Python 3.12 environment passed all 146 upstream tests.

## Gaps

- distributed: Both unchanged full-default attempts aborted with connection-reset/disconnect exceptions; no complete 945-request matrix or final upstream manifest.
- hosted_model: Not run: original AI-Lab credential is unavailable. Reference planner is not a substitute for the 160-call Minimax campaign.
- environment: Python 3.12.15 and CL SDK 1.0.0 needed for cl.sim; original authors' exact interpreter/dependency/hardware configuration not established.
- timing: Released code yields materially higher overhead here than the publication. Hardware-only attribution is unsupported; author reconciliation required.

Verified 141 reproduction archive files. No original evaluator, task, seed, iteration count, client matrix, timeout profile, or recovery policy was patched.
