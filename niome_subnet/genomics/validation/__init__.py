import json

from niome_subnet.genomics.model import MinerScore
from niome_subnet.genomics.validation.stage12 import run_stage12
from niome_subnet.genomics.validation.stage3 import run_stage3
from niome_subnet.genomics.validation.stage4 import run_stage4
from niome_subnet.genomics.validation.stage5 import run_stage5
from niome_subnet.utils.settings import CONTRACT_PATH


def _parse_seeds(raw) -> list[int]:
    """The task seed is a comma-joined string of round seeds, e.g. "122,321,431"."""
    return [int(s) for s in str(raw).split(",") if s.strip() != ""]


def benchmark_submission(cell_types: dict, uid: int) -> MinerScore:
    with open(CONTRACT_PATH) as f:
        seeds = _parse_seeds(json.load(f)["seed"])

    # Stage 12 is seed-independent, so run it once; stages 3-5 are re-run per seed.
    run_stage12(cell_types)

    finals = []
    for seed in seeds:
        run_stage3(seed=seed)
        run_stage4(seed=seed)
        finals.append(run_stage5())

    def avg(key):
        return sum(f[key] for f in finals) / len(finals)

    return MinerScore(
        uid=uid,
        breakdown={
            "n_valid_experiments": int(round(avg("n_valid_experiments"))),
            "total_weighted_score": avg("total_weighted_score"),
            "consistency_score": avg("consistency_score"),
            "consistency_factor": avg("consistency_factor"),
            "distribution_fidelity_score": avg("distribution_fidelity_score"),
            "distribution_fidelity_factor": avg("distribution_fidelity_factor"),
        },
        final_score=avg("final_score"),
        log=""
    )
