# Embedded Feasibility Workshop Draft

- [Paper PDF](paper.pdf)
- [Readable Markdown paper](paper.md)
- [Editable manuscript source](paper-source.md)
- [Machine-readable results](paper-results.json) and [latency table](latency-table.csv)
- [Frozen study configuration](paper-campaign.json)
- [Method, pilot history, and limitations](protocol.md)
- [Repeated-study raw archive](../../evaluation/results/embedded/workshop-study-20261019)
- [Sampler-calibration raw archive](../../evaluation/results/embedded/sampler-calibration-20261019)

This is a repository-published working draft, not a submitted, accepted, or
peer-reviewed paper. Authorship, affiliations, venue, publication overlap and
final claims need approval with the original author before submission.

## Version boundary

The canonical current project is [fischesn/cp2n2](https://github.com/fischesn/cp2n2).
This study benchmarks the local phys-MCP v3.0-line snapshot based on
`d836cedff1306a1f640059ce4a5abbed0eb07b89`, with evaluated source hashes recorded in
the archive. It does **not** benchmark upstream v5.1.1 or the current upstream
head. Upstream was checked on October 19, 2026 at
`d43427e33447dce6bd4441e5b1d086dc6a84fc3a`. Updated control-plane mechanisms require
a new pinned-version experiment, not relabeling the existing measurements.

## Rebuild the paper

From the repository root, using Python 3.10 and the recorded dependencies:

```bash
python -m pip install -r requirements.txt
python -m pip install -r docs/embedded-feasibility/requirements-paper.txt
python -m evaluation.evaluate_tegra_resources --verify evaluation/results/embedded/workshop-study-20261019
python -m evaluation.evaluate_tegra_resources --verify evaluation/results/embedded/sampler-calibration-20261019
python -m evaluation.workshop_paper build
python -m pytest -q
```

The builder verifies both archives, checks runtime-source hashes, calculates
paired estimates from trial observations, writes figures/tables, substitutes only
known measured values into the source, and creates a searchable four-page PDF.
Its generated outputs and source files are listed in `paper-manifest.json`.
No new experiments or remote service calls occur during the build.

To repeat the study on a Jetson, choose a **new** output directory:

```bash
python -m evaluation.evaluate_tegra_resources \
  --campaign docs/embedded-feasibility/paper-campaign.json \
  --output-dir evaluation/results/embedded/my-repeated-study --timeout-s 90
python -m evaluation.workshop_paper build \
  --study-dir evaluation/results/embedded/my-repeated-study
```

The default paper builder checks runtime-source identity against the included
baseline. Changing the implementation requires revising the study protocol and
manuscript rather than bypassing that check. Do not mix new observations into a
published raw archive or overwrite the historical data.

## Scope

The new study has 60 fresh-process trials: 48 latency trials and 12 resource
trials. The latter last six seconds at 100 requests/s with three-second idle
windows before and after. Statistical intervals use six paired trial blocks,
not individual requests as independent replications. Platform power includes
background activity and cannot establish control-plane-only energy use.

No physical PNN, smartwatch, new upstream release, wide-area network, one-hour
soak, contention sweep or memory-cap campaign was evaluated. Existing calibration
and smoke archives remain exploratory and are not pooled into the repeated study.

## Publishing safety

Local environment files, private keys, raw HDF5 recordings and virtual
environments are excluded. `scripts/audit_publication.py` checks the current Git
index for excluded paths and known credential patterns without printing values.
It does not certify old Git history. Any credential previously exposed in history
must be revoked/rotated; removing a file in a new commit cannot retract it.

AI assistance was used for code, analysis tooling, and drafting. Reviewers and
eventual authors should independently check the source data, claims and required
venue disclosures.