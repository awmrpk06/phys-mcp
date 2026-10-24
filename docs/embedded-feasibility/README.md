# Embedded Results and Reproduction Guide

Paper writing is deferred. This release publishes experiment evidence and guides,
not a revised manuscript. Older paper files are historical working drafts and
must not be used as the current interpretation of the October 24 results.

- [Machine-readable v5.1.1 results](v511-results.json)
- [Original-protocol method](original-protocol.md) and [resource method](upstream-resource-protocol.md)
- [Original-protocol archive](../../evaluation/results/original-paper-20261024)
- [Profiled resource archive](../../evaluation/results/upstream-profile-20261024)
- [Latency comparison and reproduction gaps](../../evaluation/results/original-paper-20261024/comparison.md)
- [CPU, memory, temperature and power report](../../evaluation/results/upstream-profile-20261024/profile-report.md)

## Version boundary

The canonical current project is [fischesn/cp2n2](https://github.com/fischesn/cp2n2).
The October 24 experiments evaluate unmodified v5.1.1 at
`36fbae002b0a9e066416ea9cafbb966a33fe53bb`. Original-protocol timing runs and
externally profiled runs are kept separate. The five-minute, 10 Hz workload is
explicitly labeled as an extension using the upstream task factory and maintenance
sequence. The earlier v3.0-line experiments remain in the October 19 archives and
are not pooled with the newer implementation's measurements.

## Figures and tables

1. [Latency comparison](figures/v511-latency-comparison.png): original publication,
  unprofiled aggregate Jetson run, and unprofiled standalone run, separately shown.
2. [Sustained workload cost](figures/v511-sustained-cost.png): CPU, RSS/PSS, and
  invocation latency over all 300 seconds, including growth and spikes.
3. [Platform traces](figures/v511-platform-traces.png): temperatures, input power,
  and GPU activity, without attributing platform-wide signals solely to CP2N2.

Tables are independently exported as CSV:
[outcomes](v511-outcome-table.csv), [latency](v511-comparison-table.csv),
[resources](v511-resource-table.csv), and [first/last-minute changes](v511-growth-table.csv).
All figures also have vector PDF counterparts. [results-manifest.json](results-manifest.json)
lists the exported data, figures, tables and input archive hashes.

## Verify and Export Results

From the repository root with Python 3.12 or later and the numerical/plotting
dependencies installed:

```bash
python -m pip install -r requirements.txt
python -m evaluation.reproduce_original summarize --output-dir evaluation/results/original-paper-20261024
python -m evaluation.summarize_profiles evaluation/results/upstream-profile-20261024
python -m evaluation.upstream_paper export-results
python -m pytest -q
```

The summarizers verify archived checksums before recomputing reports. The
`export-results` command verifies all 14 stage archives and their implementation
identity, then writes data tables and figures only. It does not generate or edit
the manuscript and does not run new experiments or remote services. Original and
Jetson latency means remain separate, without invented confidence intervals.

To use separately archived results, select the input directories explicitly:

```bash
python -m evaluation.upstream_paper export-results \
  --reproduction-dir evaluation/results/original-paper-20261024 \
  --profile-dir evaluation/results/upstream-profile-20261024
```

## Run New Experiments

Prepare a separate interpreter and the immutable source release. These commands
are for a fresh setup; do not overwrite an existing checkout or environment.

```bash
git clone --depth 1 --branch v5.1.1 https://github.com/fischesn/cp2n2.git _temp/cp2n2-v5.1.1
git -C _temp/cp2n2-v5.1.1 rev-parse HEAD
python3.12 -m venv _temp/cp2n2-python312
source _temp/cp2n2-python312/bin/activate
python -m pip install -r _temp/cp2n2-v5.1.1/requirements.txt numpy scipy matplotlib psutil 'cl-sdk==1.0.0' 'mcp==1.27.0'
```

The checkout must resolve to the commit above; the launcher rejects other or
modified tracked sources. The simulator-capable SDK requires Python 3.12 or
later. Python 3.10 with SDK 0.1.0 failed on missing `cl.sim`; that initial failure
is retained. Every stage's provenance records the actual package versions for
stricter environment reconstruction.

Run from this repository's root with the new interpreter active:

```bash
python -m evaluation.reproduce_original tests --output-dir evaluation/results/my-run/tests
python -m evaluation.reproduce_original core --output-dir evaluation/results/my-run/core
python -m evaluation.reproduce_original e3-control --output-dir evaluation/results/my-run/e3-control
python -m evaluation.reproduce_original e3-provider --output-dir evaluation/results/my-run/e3-provider
python -m evaluation.reproduce_original overhead --profile --timeout-s 90 --output-dir evaluation/results/my-profile/overhead
python -m evaluation.reproduce_original sustained --profile --duration-s 300 --rate-hz 10 --timeout-s 360 --output-dir evaluation/results/my-profile/sustained-300s-10hz
```

Each output directory must be new. The sustained command is a separately labeled
extension, not part of the original 25-iteration protocol. The profile controller
requires Jetson `tegrastats`, monitors descendants, and enforces thermal,
telemetry-freshness, disk and timeout guards without altering host power mode.
See the detailed protocols for all stage choices and measurement boundaries.

Nonzero exit codes and incomplete runs are results to preserve, not invitations
to delete an archive and retry in place. The full A6 runner currently aborts on
connection errors on this setup; do not claim a complete 945-request campaign.
The hosted Minimax study was not run and is not replaced by the reference planner.
Do not copy credentials into the isolated checkout; the launcher disables dotenv
loading and removes inherited provider secrets.

## Scope

The original 25-iteration local timing protocol is compared contextually with the
publication. The sustained experiment completed 3,000 requests in five minutes
but shows growing memory, CPU and latency; successful execution is not proof of
an indefinite operating lifetime. Platform power includes background activity.
The full distributed attempts remain incomplete, and the hosted model campaign
is not replicated. No physical PNN, smartwatch, one-hour soak or memory-cap sweep
was evaluated. The study is about embedded feasibility, not Jetson superiority.

## Publishing safety

Local environment files, private keys, arbitrary HDF5 files and virtual
environments are excluded. Two small native recordings from the explicitly
software-only October 24 E3 provider runs are included to keep those result
archives complete. Their filenames, simulator evidence labels and SHA-256 values
must match the staged result records and stage manifests. These are not recordings
of physical tissue or user experiments.

`scripts/audit_publication.py` checks the current Git index for excluded paths,
known credential patterns and the narrow simulator-recording exception without
printing secret values.
It does not certify old Git history. Any credential previously exposed in history
must be revoked/rotated; removing a file in a new commit cannot retract it.

AI assistance was used for code, analysis tooling and guides. Future authors
should independently review the evidence and its limitations before writing the
paper. No paper is submitted or accepted by publishing these results.