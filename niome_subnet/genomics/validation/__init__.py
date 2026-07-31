from niome_subnet.genomics.model import MinerScore
from niome_subnet.genomics.validation.stage12 import run_stage12
from niome_subnet.genomics.validation.stage3 import run_stage3
from niome_subnet.genomics.validation.stage4 import run_stage4
from niome_subnet.genomics.validation.stage5 import run_stage5


def benchmark_submission(cell_types: dict, uid: int) -> MinerScore:
    run_stage12(cell_types)
    run_stage3()
    run_stage4()
    final = run_stage5()

    return MinerScore(
        uid=uid,
        breakdown={
            "n_valid_experiments": final["n_valid_experiments"],
            "total_weighted_score": final["total_weighted_score"],
            "consistency_score": final["consistency_score"],
            "consistency_factor": final["consistency_factor"],
            "distribution_fidelity_score": final["distribution_fidelity_score"],
            "distribution_fidelity_factor": final["distribution_fidelity_factor"],
        },
        final_score=final["final_score"],
        log=""
    )
