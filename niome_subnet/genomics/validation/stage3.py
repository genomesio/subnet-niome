import json
import random
import hashlib
import math
from collections import defaultdict

from niome_subnet.utils.settings import (
    CONTRACT_PATH,
    VALID_EXPERIMENTS_PATH,
    STAGE3_DATASET,
    STAGE3_SUMMARY_PATH,
)


REGION_ENERGY_OFFSETS = {
    "5UTR_or_upstream": 0.05,
    "exon1": 0.03,
    "exon2": 0.03,
    "exon3": 0.03,
    "intron1": -0.03,
    "intron2": -0.03,
    "3UTR": 0.02,
}


def experiment_seed(round_seed, submitted_exp):
    key = "|".join(str(x) for x in (
        round_seed,
        submitted_exp["mutation"],
        submitted_exp["cas_system"],
        submitted_exp["guideRNA"],
        submitted_exp["target_alignment_start"],
        submitted_exp.get("strand"),
    ))
    digest = hashlib.sha256(key.encode()).hexdigest()
    return int(digest, 16) % (2 ** 32)


def extract_features(exp):
    feat = exp["features"]
    return {
        "gc": feat["gc"],
        "distance": feat["distance_to_mutation"],
        "gc_score": feat["gc_score"],
        "dist_score": feat["dist_score"],
        "consistency": feat["consistency"],
        "cell_type_accessibility": feat.get("cell_type_accessibility", 1.0),
        "mutation_weight": feat.get("mutation_weight", 1.0),
        "region_energy_offset": feat.get("region_energy_offset", 0.0)
    }


def sequence_energy(features):
    gc = features["gc"]
    dist = features["distance"]
    accessibility = features["cell_type_accessibility"]
    region_offset = features.get("region_energy_offset", 0.0)
    return max(0.0, min(1.0,
        accessibility * (
            1.8 * gc +
            0.6 * math.exp(-dist / 1500) +
            region_offset
        )
    ))


def microhomology_trigger(features, rng):
    gc = features["gc"]
    repeat_bias = gc * (1 - gc)
    p_mh = min(0.6, repeat_bias * 2.2)
    return rng.random() < p_mh


def cut_probability(cas, energy):
    base = 0.86 if cas == "Cas9" else 0.78
    return min(0.99, max(0.4, base + 0.18 * energy))


def repair_mode(cas, energy, mh, rng):
    hdr_base = 0.32 if cas == "Cas9" else 0.24
    hdr = hdr_base + 0.35 * energy
    mh_nhej = 0.30 if mh else 0.12
    blunt = 0.35
    total = hdr + mh_nhej + blunt
    r = rng.random() * total
    if r < hdr:
        return "HDR"
    elif r < hdr + mh_nhej:
        return "MH_NHEJ"
    else:
        return "BLUNT_NHEJ"


def sample_indel_length(mode, rng):
    if mode == "HDR":
        return 0
    if mode == "MH_NHEJ":
        return max(1, int(rng.gammavariate(2.2, 2.8)))
    if mode == "BLUNT_NHEJ":
        return max(1, int(rng.expovariate(0.6)))
    return 1


def pearson_corr(xs, ys):
    n = len(xs)
    if n < 2:
        return 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    denom = math.sqrt(var_x * var_y)
    return cov / denom if denom > 0 else 0.0


def group_by_mutation(results):
    by_mut = defaultdict(list)
    for r in results:
        by_mut[r["mutation"]].append(r)
    breakdown = {}
    for mutation, group in by_mut.items():
        n = len(group)
        breakdown[mutation] = {
            "mutation_weight": group[0]["mutation_weight"],
            "n": n,
            "cut_rate": sum(1 for r in group if r["outcome"] != "no_cut") / n,
            "mean_energy": sum(r["energy"] for r in group) / n,
            "mean_indel_length": sum(r["indel_length"] for r in group) / n
        }
    return dict(sorted(breakdown.items(), key=lambda kv: kv[1]["mutation_weight"], reverse=True))


def simulate(exp, round_seed):
    features = extract_features(exp)
    experiment_id = exp["experiment"]["experiment_id"]
    cas = exp["experiment"]["cas_system"]
    mutation = exp["experiment"]["mutation"]
    rng = random.Random(experiment_seed(round_seed, exp["experiment"]))
    energy = sequence_energy(features)
    mh = microhomology_trigger(features, rng)
    cut_p = cut_probability(cas, energy)

    if rng.random() > cut_p:
        return {
            "experiment_id": experiment_id,
            "mutation": mutation,
            "mutation_weight": features["mutation_weight"],
            "cas": cas,
            "outcome": "no_cut",
            "indel_length": 0,
            "features": features,
            "energy": energy,
            "mh": mh
        }

    mode = repair_mode(cas, energy, mh, rng)
    indel = sample_indel_length(mode, rng)
    return {
        "experiment_id": experiment_id,
        "mutation": mutation,
        "mutation_weight": features["mutation_weight"],
        "cas": cas,
        "outcome": mode,
        "indel_length": indel,
        "features": features,
        "energy": energy,
        "mh": mh
    }


def run_stage3(seed=None) -> tuple[list, dict]:
    with open(CONTRACT_PATH) as f:
        contract = json.load(f)
    round_seed = seed if seed is not None else contract["seed"]

    with open(VALID_EXPERIMENTS_PATH) as f:
        experiments = json.load(f)

    results = []
    for exp in experiments:
        results.append(simulate(exp, round_seed))

    total = len(results)
    from collections import Counter
    outcome_counts = Counter(r["outcome"] for r in results)
    indels = [r["indel_length"] for r in results]
    energies = [r["energy"] for r in results]
    weights = [r["mutation_weight"] for r in results]
    cuts = [0.0 if r["outcome"] == "no_cut" else 1.0 for r in results]

    summary = {
        "n": total,
        "cut_rate": 1 - outcome_counts["no_cut"] / total if total else 0.0,
        "mean_indel_length": sum(indels) / max(1, len(indels)),
        "mean_energy": sum(energies) / max(1, len(energies)),
        "outcomes": dict(outcome_counts),
        "mutation_weight_breakdown": group_by_mutation(results),
        "weight_correlations": {
            "weight_vs_cut": pearson_corr(weights, cuts),
            "weight_vs_energy": pearson_corr(weights, energies),
            "weight_vs_indel_length": pearson_corr(weights, indels)
        }
    }

    with open(STAGE3_DATASET, "w") as f:
        json.dump(results, f, indent=2)

    with open(STAGE3_SUMMARY_PATH, "w") as f:
        json.dump(summary, f, indent=2)

    return results, summary
