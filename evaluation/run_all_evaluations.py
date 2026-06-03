"""Run the full evaluation suite and refresh result artefacts."""

from __future__ import annotations

try:
    from .evaluate_externalized_backend import evaluate as evaluate_externalized_backend
    from .evaluate_failure_campaign import evaluate as evaluate_failure_campaign
    from .evaluate_matching import evaluate as evaluate_matching
    from .evaluate_matching_baselines import evaluate as evaluate_matching_baselines
    from .evaluate_overhead import evaluate as evaluate_overhead
    from .evaluate_portability import evaluate as evaluate_portability
except ImportError:
    from evaluate_externalized_backend import evaluate as evaluate_externalized_backend
    from evaluate_failure_campaign import evaluate as evaluate_failure_campaign
    from evaluate_matching import evaluate as evaluate_matching
    from evaluate_matching_baselines import evaluate as evaluate_matching_baselines
    from evaluate_overhead import evaluate as evaluate_overhead
    from evaluate_portability import evaluate as evaluate_portability


def main() -> None:
    evaluate_overhead()
    evaluate_portability()
    evaluate_matching()
    evaluate_matching_baselines()
    evaluate_failure_campaign()
    evaluate_externalized_backend()
    print("All evaluation artefacts refreshed.")


if __name__ == "__main__":
    main()
