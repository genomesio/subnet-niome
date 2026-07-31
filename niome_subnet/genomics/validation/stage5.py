import json
import math
from collections import Counter, defaultdict

from niome_subnet.utils.settings import (
    CONTRACT_PATH,
    VALID_EXPERIMENTS_PATH,
    STAGE3_DATASET,
    FINAL_REWARD_PATH,
    DISTRIBUTION_FIDELITY_PATH,
)


def load_json(path):
    with open(path, "r") as f:
        return json.load(f)


def shannon_entropy(counts):
    total = sum(counts)
    if total <= 0:
        return 0.0
    h = 0.0
    for c in counts:
        if c <= 0:
            continue
        p = c / total
        h -= p * math.log2(p)
    return h


def coverage_entropy_ratio(observed_counter, full_support):
    support = list(full_support)
    n = len(support)
    if n <= 1:
        return 1.0
    counts = [observed_counter.get(cat, 0) for cat in support]
    h = shannon_entropy(counts)
    h_max = math.log2(n)
    return h / h_max if h_max > 0 else 1.0


def extract_kmers(seq, k):
    if len(seq) < k:
        return []
    return [seq[i:i + k] for i in range(len(seq) - k + 1)]


def kmer_diversity_entropy_ratio(guides, k=12):
    pool = Counter()
    total = 0
    for g in guides:
        kmers = extract_kmers(g, k)
        pool.update(kmers)
        total += len(kmers)
    if total <= 1:
        return 0.0
    h = shannon_entropy(list(pool.values()))
    h_max = math.log2(total)
    return h / h_max if h_max > 0 else 0.0


def distinct_guide_ratio(guides):
    if not guides:
        return 0.0
    return len(set(guides)) / len(guides)


def geometric_mean(values, eps=1e-9):
    if not values:
        return 0.0
    clipped = [max(v, eps) for v in values]
    log_sum = sum(math.log(v) for v in clipped)
    return math.exp(log_sum / len(clipped))


def jensen_shannon_divergence(counts_p, counts_q):
    total_p = sum(counts_p.values())
    total_q = sum(counts_q.values())
    if total_p == 0 or total_q == 0:
        return None
    categories = set(counts_p) | set(counts_q)
    p = {c: counts_p.get(c, 0) / total_p for c in categories}
    q = {c: counts_q.get(c, 0) / total_q for c in categories}
    m = {c: 0.5 * (p[c] + q[c]) for c in categories}

    def kl(a, b):
        s = 0.0
        for c in categories:
            if a[c] <= 0:
                continue
            s += a[c] * math.log2(a[c] / b[c])
        return s

    return 0.5 * kl(p, m) + 0.5 * kl(q, m)


def wasserstein_1d(sample_p, sample_q, n_quantiles=200):
    if not sample_p or not sample_q:
        return None
    sp = sorted(sample_p)
    sq = sorted(sample_q)

    def quantile(sorted_sample, q):
        idx = min(len(sorted_sample) - 1, max(0, int(round(q * (len(sorted_sample) - 1)))))
        return sorted_sample[idx]

    diffs = []
    for i in range(n_quantiles):
        q = i / (n_quantiles - 1) if n_quantiles > 1 else 0.5
        diffs.append(abs(quantile(sp, q) - quantile(sq, q)))
    return sum(diffs) / len(diffs)


def compute_distribution_fidelity(stage12_valid, stage3_results, contract, k=12):
    active_mutations = contract["active_mutations"]
    cas_systems = contract["rules"].get("cas_systems", ["Cas9", "Cas12a"])
    strands = ["+", "-"]

    n = len(stage12_valid)
    if n == 0:
        return {
            "n_valid_experiments": 0,
            "distribution_fidelity_score": 0.0,
            "note": "no valid experiments"
        }

    mutation_counts = Counter(e["experiment"]["mutation"] for e in stage12_valid)
    cas_counts = Counter(e["experiment"]["cas_system"] for e in stage12_valid)
    strand_counts = Counter(e["experiment"].get("strand") for e in stage12_valid)
    joint_counts = Counter(
        (e["experiment"]["mutation"], e["experiment"]["cas_system"], e["experiment"].get("strand"))
        for e in stage12_valid
    )
    joint_support = [
        (m, c, s) for m in active_mutations for c in cas_systems for s in strands
    ]

    mutation_ratio = coverage_entropy_ratio(mutation_counts, active_mutations)
    cas_ratio = coverage_entropy_ratio(cas_counts, cas_systems)
    strand_ratio = coverage_entropy_ratio(strand_counts, strands)
    joint_ratio = coverage_entropy_ratio(joint_counts, joint_support)

    guides = [e["experiment"]["guideRNA"] for e in stage12_valid]
    kmer_ratio = kmer_diversity_entropy_ratio(guides, k=k)
    guide_ratio = distinct_guide_ratio(guides)

    distribution_fidelity_score = geometric_mean(
        [mutation_ratio, cas_ratio, strand_ratio, joint_ratio, kmer_ratio, guide_ratio]
    )

    MIN_PER_CAS = 5
    by_cas_repair = defaultdict(Counter)
    by_cas_indel = defaultdict(list)
    for r in stage3_results:
        by_cas_repair[r["cas"]][r["outcome"]] += 1
        by_cas_indel[r["cas"]].append(r["indel_length"])

    cas_shift = {"insufficient_data": True}
    present_cas = [c for c in cas_systems if sum(by_cas_repair[c].values()) >= MIN_PER_CAS]
    if len(present_cas) >= 2:
        c1, c2 = present_cas[0], present_cas[1]
        cas_shift = {
            "insufficient_data": False,
            "compared": [c1, c2],
            "repair_mode_jensen_shannon_divergence": jensen_shannon_divergence(by_cas_repair[c1], by_cas_repair[c2]),
            "indel_length_wasserstein_distance": wasserstein_1d(by_cas_indel[c1], by_cas_indel[c2])
        }

    return {
        "n_valid_experiments": n,
        "mutation_coverage_entropy_ratio": mutation_ratio,
        "cas_system_coverage_entropy_ratio": cas_ratio,
        "strand_coverage_entropy_ratio": strand_ratio,
        "joint_coverage_entropy_ratio": joint_ratio,
        "kmer_diversity_entropy_ratio": kmer_ratio,
        "distinct_guide_ratio": guide_ratio,
        "distribution_fidelity_score": distribution_fidelity_score,
        "cas_specific_shift_diagnostic": cas_shift,
        "coverage_detail": {
            "mutation_counts": dict(mutation_counts),
            "cas_system_counts": dict(cas_counts),
            "strand_counts": dict(strand_counts)
        }
    }


def run_stage5(k: int = 12) -> dict:
    stage12_valid = load_json(VALID_EXPERIMENTS_PATH)
    stage3_results = load_json(STAGE3_DATASET)
    contract = load_json(CONTRACT_PATH)
    stage4 = load_json(FINAL_REWARD_PATH)

    summary = compute_distribution_fidelity(stage12_valid, stage3_results, contract, k=k)

    with open(DISTRIBUTION_FIDELITY_PATH, "w") as f:
        json.dump(summary, f, indent=2)

    distribution_fidelity_factor = max(0.0, min(1.0, summary.get("distribution_fidelity_score", 0.0)))
    final_score = stage4["total_weighted_score"] * stage4["consistency_factor"] * distribution_fidelity_factor

    return {
        "n_valid_experiments": stage4.get("n_valid_experiments"),
        "total_weighted_score": stage4["total_weighted_score"],
        "consistency_score": stage4.get("consistency_score"),
        "consistency_factor": stage4["consistency_factor"],
        "distribution_fidelity_score": summary.get("distribution_fidelity_score"),
        "distribution_fidelity_factor": distribution_fidelity_factor,
        "final_score": final_score
    }
