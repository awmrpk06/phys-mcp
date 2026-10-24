# Original-Paper Reproduction

The extension must begin with the original paper's protocol. The earlier
60-trial legacy study remains an exploratory, separately defined experiment;
it is not a reproduction of the paper and is not the definitive extension.

## Primary research question

Can the original control plane execute its tested control functions on an
embedded Linux host, and within what latency, memory, CPU and energy envelope?
This is not a contest between Jetson and the author's machine. Reusing the
original workload definitions and control semantics provides continuity with
the paper; matching its numerical timing results is not a feasibility criterion.

Report functional outcomes and absolute embedded costs first. Historical timing
comparisons provide context, not a hardware ranking or an acceptance threshold.
The author's machine specifications help explain cross-study differences but
are not a prerequisite for measuring feasibility on this Jetson.

Feasibility must be qualified by the tested conditions. Successful local and
simulator paths support those deployment cases. Incomplete A6 runs limit claims
about the tested concurrent/distributed configuration. Millisecond-scale overhead
is neither inherently acceptable nor inherently disqualifying; suitability
depends on an explicitly stated application's budget. No such application-wide
budget has yet been established for this study.

## Pinned source

- Original paper: <https://arxiv.org/abs/2605.04256v2>.
- Release: <https://github.com/fischesn/cp2n2/releases/tag/v5.1.1>.
- Commit: `36fbae002b0a9e066416ea9cafbb966a33fe53bb`.
- Checkout: `_temp/cp2n2-v5.1.1`, unchanged detached release.
- Results: [original-paper-20261024](../../evaluation/results/original-paper-20261024).
- [Observed comparisons and reproduction gaps](../../evaluation/results/original-paper-20261024/comparison.md).

The paper reports 25 iterations per local direct/orchestrated path, 15 HTTP
invocations, seven selector cases, four holdouts, 32 seeded weight perturbations,
five fault scenarios, and a 945-request, 30-cell distributed campaign. Those
defaults were retained. Our earlier warmup, thread controls, seed schedule and
paired repetition counts were not injected into these evaluators.

## Environment prerequisite

The initial Python 3.10 environment had CL SDK 0.1.0 and failed upstream test
collection because `cl.sim` was unavailable. The provider's official SDK 1.0.0
requires Python 3.12 or later. An isolated Python 3.12.15 environment was created
under `_temp/cp2n2-python312`, with CL SDK 1.0.0 and MCP 1.27.0 plus the upstream
requirements and numerical/plotting dependencies. All 146 upstream tests passed
there. Exact installed versions are captured in every stage's provenance.

This is a recorded deployment prerequisite, not a claim that the author's
environment used those exact versions. The earlier Python 3.10 failure is
retained alongside the successful test run. No SDK compatibility patches were
applied to make the newer code run on the older interpreter.

## Commands

Acquire the release without replacing this repository:

```bash
git clone --depth 1 --branch v5.1.1 https://github.com/fischesn/cp2n2.git _temp/cp2n2-v5.1.1
git -C _temp/cp2n2-v5.1.1 rev-parse HEAD
```

After preparing the isolated environment, use new output directories:

```bash
_temp/cp2n2-python312/bin/python -m evaluation.reproduce_original tests --output-dir evaluation/results/my-reproduction/tests
_temp/cp2n2-python312/bin/python -m evaluation.reproduce_original core --output-dir evaluation/results/my-reproduction/core
_temp/cp2n2-python312/bin/python -m evaluation.reproduce_original e3-control --output-dir evaluation/results/my-reproduction/e3-control
_temp/cp2n2-python312/bin/python -m evaluation.reproduce_original e3-provider --output-dir evaluation/results/my-reproduction/e3-provider
```

The launcher runs upstream commands from the pinned checkout. It sets only an
isolated results directory, headless plotting, disabled dotenv loading, and the
documented provider-run simulator options. It removes inherited credentials and
provider configuration. The official provider application rejects non-simulator
execution. No physical device or hosted model is executed by this launcher.

## Observed outcomes, October 24

- Upstream tests: 146 passed, unchanged source.
- Original local, HTTP, portability, selector, robustness and fault evaluators
  completed before the aggregate runner failed in A6.
- The full A6 campaign was attempted through both the aggregate runner and its
  standalone entry point. Both aborted on connection disconnect/reset exceptions.
  Their configured matrix and partial service traces are retained. Neither is a
  complete 945-request result; no final A6 matrix is fabricated from partial data.
- A standalone 25-run overhead invocation was recorded to distinguish aggregate
  process state from a fresh evaluator. Both sets of timing observations remain
  visible. Neither matches the paper's reported sub-millisecond local overhead.
- The documented reference-planner E3 control-plane campaign completed. It is a
  deterministic software control, not a reproduction of language-model behavior.
- The official CL SDK application completed 14 trials, including 12 scored and
  two sham trials, with 24 stimulation events and 60 spikes. Online results match
  independently reconstructed HDF5 data. This matches the paper's technical E3
  counts and remains entirely simulator evidence.
- The original 160-decision Minimax AI-Lab campaign was not run: the required
  AI-Lab credential is absent. Gemini, Ollama and the reference planner are not
  substituted for it. The author's exact prompt/oracle fixture and provider setup
  must be confirmed before a model replication.

## Next steps for the embedded extension

1. Lead with the original control functions successfully exercised on the
  embedded host and their measured absolute cost. Preserve both timing runs;
  do not optimize settings to match the publication. Author environment details
  are useful supporting context, not a gate for the embedded study.
2. Share the A6 exception and service logs. Any robustness fix must be a separately
   identified source revision and rerun, not silently presented as the original
  release. Retain the original full-load failures while mapping any additionally
  tested operating limits; a reduced-load success would be a separate condition,
  not a completed reproduction of the original matrix.
3. Treat the original hosted-model campaign as a separate planner-quality
  experiment. It remains not reproduced and must not be replaced by an oracle,
  but its credential requirement does not block a paper scoped to embedded
  control-plane feasibility with deterministic clients.
4. Add resource profiling and sustained operation on the pinned original
  implementation as explicit extensions. Do not reuse the legacy implementation's
  memory/CPU measurements as if they describe v5.1.1. Identify application budgets
  before making application-specific suitability claims. Keep historical
  comparisons secondary to functionality, cost and operating limits.

All reproduction stages have outer checksum manifests, including failed stages.
Provider HDF5 files are local simulator evidence; inspect publication policy and
archive size before sharing them. This run has not overwritten the previous
paper or any of its archived measurements.