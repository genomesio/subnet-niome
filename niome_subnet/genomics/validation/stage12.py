import json
import math
import pickle
import hashlib
from pathlib import Path
from collections import defaultdict
from Bio import SeqIO
from Bio.Seq import Seq

from niome_subnet.utils.settings import (
    HBB_REFERENCE_PATH,
    CONTRACT_PATH,
    CHR11_PATH,
    MINER_SUBMISSION_PATH,
    VALID_EXPERIMENTS_PATH,
    INVALID_EXPERIMENTS_PATH,
    KMER_CACHE_DIR,
)


def build_kmer_index(seq, k=12):
    index = defaultdict(list)
    for i in range(len(seq) - k):
        index[seq[i:i + k]].append(i)
    return index


def _hash_kmer_inputs(seq, k):
    h = hashlib.md5()
    h.update(seq.encode())
    h.update(str(k).encode())
    return h.hexdigest()


def load_or_build_kmer_index(seq, k=12):
    import os
    os.makedirs(KMER_CACHE_DIR, exist_ok=True)
    cache_key = _hash_kmer_inputs(seq, k)
    cache_file = f"{KMER_CACHE_DIR}/kmer_{cache_key}.pkl"
    if os.path.exists(cache_file):
        with open(cache_file, "rb") as f:
            return pickle.load(f)
    index = build_kmer_index(seq, k)
    with open(cache_file, "wb") as f:
        pickle.dump(index, f)
    return index


REGION_ENERGY_OFFSETS = {
    "5UTR_or_upstream": 0.05,
    "exon1": 0.03,
    "exon2": 0.03,
    "exon3": 0.03,
    "intron1": -0.03,
    "intron2": -0.03,
    "3UTR": 0.02,
}


def load_chr11(path):
    for record in SeqIO.parse(path, "fasta"):
        return str(record.seq)
    raise ValueError("chr11 not found")


def gc_content(seq):
    return sum(b in "GC" for b in seq) / max(1, len(seq))


def hamming(a, b):
    return sum(x != y for x, y in zip(a, b))


def reverse_complement(seq):
    return str(Seq(seq).reverse_complement())


def check_pam(seq, start, L, cas, strand):
    if start < 0 or start + L >= len(seq):
        return False, "out_of_bounds"

    if cas == "Cas9":
        if strand == "+":
            if start + L + 3 > len(seq):
                return False, "out_of_bounds"
            pam = seq[start+L:start+L+3]
        elif strand == "-":
            if start < 3:
                return False, "out_of_bounds"
            pam = reverse_complement(seq[start-3:start])
        else:
            return False, "invalid_strand"
        return (len(pam) == 3 and pam[1:] == "GG"), "ok"

    if cas == "Cas12a":
        if strand == "+":
            if start < 4:
                return False, "out_of_bounds"
            pam = seq[start-4:start]
        elif strand == "-":
            if start + L + 4 > len(seq):
                return False, "out_of_bounds"
            pam = reverse_complement(seq[start+L:start+L+4])
        else:
            return False, "invalid_strand"
        return (len(pam) == 4 and pam[:3] == "TTT"), "ok"

    return False, "invalid_cas"


def stage1(exp, seq, mutation_map, contract):
    guide = exp["guideRNA"]
    cas = exp["cas_system"]
    start = exp["target_alignment_start"]
    mutation = exp["mutation"]

    if mutation not in contract["active_mutations"]:
        return 0.0, "mutation_not_allowed"

    contract_cell_type = contract.get("cell_type")
    if contract_cell_type is not None and exp.get("cell_type") != contract_cell_type:
        return 0.0, "cell_type_mismatch"

    if len(guide) not in (20, 23):
        return 0.0, "invalid_length"

    if exp.get("target_alignment_end") != start + len(guide):
        return 0.0, "invalid_alignment_end"

    if start < 0 or start + len(guide) >= len(seq):
        return 0.0, "out_of_bounds"

    pam_ok, pam_status = check_pam(seq, start, len(guide), cas, exp.get("strand"))
    if not pam_ok:
        return 0.0, f"pam_{pam_status}"

    target = seq[start:start+len(guide)]
    if exp["strand"] == "+":
        mm = hamming(guide, target)
    elif exp["strand"] == "-":
        mm = hamming(reverse_complement(guide), target)
    else:
        return 0.0, "invalid_strand"

    if mm > contract["rules"]["max_mismatches"]:
        return 0.0, "too_many_mismatches"

    if contract["rules"].get("proximity_gate", False):
        max_dist = contract["rules"]["base_padding"]
        dist_to_target = abs(start - mutation_map[mutation])
        if dist_to_target > max_dist:
            return 0.0, "mutation_too_far"

    return 1.0, "ok"


def offtarget_uniqueness(guide, cas, kmer_index):
    seed = guide[-12:] if cas == "Cas9" else guide[:12]
    hits = len(kmer_index.get(seed, []))
    if hits == 0:
        return 1.0
    elif hits <= 5:
        return 0.7
    elif hits <= 20:
        return 0.4
    else:
        return 0.1


def stage2(cell_types, exp, seq, mutation_map, contract, kmer_index):
    guide = exp["guideRNA"]
    cas = exp["cas_system"]
    start = exp["target_alignment_start"]
    mutation = exp["mutation"]
    mut = mutation_map[mutation]

    gc = gc_content(guide)
    dist = abs(start - mut)

    gc_score = max(0.0, 1.0 - abs(gc - 0.5) * 2)
    base_padding = contract["rules"]["base_padding"]
    dist_score = math.exp(-dist / base_padding)
    consistency = 1.0 if dist < base_padding else 0.3

    base_structural_score = 0.625 * gc_score + 0.375 * dist_score
    offtarget_factor = offtarget_uniqueness(guide, cas, kmer_index)
    structural_score = base_structural_score * offtarget_factor

    mutation_weight = contract.get("mutation_weights", {}).get(mutation, 1.0)
    weighted_score = structural_score * mutation_weight

    cell_type = contract.get("cell_type")
    accessibility = cell_types.get(cell_type, {}).get("accessibility", 1.0)

    mutation_region = contract.get("mutation_regions", {}).get(mutation)
    region_energy_offset = REGION_ENERGY_OFFSETS.get(mutation_region, 0.0)

    return structural_score, {
        "gc": gc,
        "distance": dist,
        "gc_score": gc_score,
        "dist_score": dist_score,
        "consistency": consistency,
        "offtarget_factor": offtarget_factor,
        "mutation_weight": mutation_weight,
        "weighted_score": weighted_score,
        "cell_type": cell_type,
        "cell_type_accessibility": accessibility,
        "mutation_region": mutation_region,
        "region_energy_offset": region_energy_offset
    }


def truncate_submission(submission, contract):
    """Cap the submission at max_experiments rows with unique experiment_ids, and persist the cut.

    Two separate things have to be bounded here, because stage4 joins stage3 to stage12 on
    experiment_id and sums weighted_score over the result:

    - Row count. Every later stage reads this file and scores whatever it finds, so an
      oversized submission would be benchmarked in full.
    - experiment_id uniqueness. A repeated id turns that inner join into a cross product, so
      a submission already inside the cap can still report an n_valid_experiments and a
      total_weighted_score many times the allowed size (250 rows sharing 50 ids scores 1250).

    Cutting the file itself means the whole pipeline — and the submission archived afterwards —
    sees only the capped set.
    """
    kept = []
    seen_ids = set()
    max_experiments = contract["rules"].get("max_experiments")

    for exp in submission:
        if max_experiments is not None and len(kept) >= max_experiments:
            break
        experiment_id = exp.get("experiment_id")
        if not isinstance(experiment_id, str) or not experiment_id.strip():
            continue
        if experiment_id in seen_ids:
            continue
        seen_ids.add(experiment_id)
        kept.append(exp)

    if len(kept) == len(submission):
        return submission

    with open(MINER_SUBMISSION_PATH, "w") as f:
        json.dump(kept, f, indent=2)
    return kept


def run_stage12(cell_types: dict, offtarget_flank: int = 50000) -> tuple[list, list]:
    reference = json.load(open(HBB_REFERENCE_PATH))
    contract = json.load(open(CONTRACT_PATH))
    submission = truncate_submission(json.load(open(MINER_SUBMISSION_PATH)), contract)

    seq = load_chr11(CHR11_PATH)
    mutation_map = reference["mutation_map"]

    win_start = reference["gene_region"]["start"]
    win_end = reference["gene_region"]["end"]
    idx_start = max(0, win_start - offtarget_flank)
    idx_end = min(len(seq), win_end + offtarget_flank)
    kmer_index = load_or_build_kmer_index(seq[idx_start:idx_end], k=12)

    valid_experiments = []
    invalid_experiments = []
    seen_valid_keys = set()

    for exp in submission:
        s1, reason = stage1(exp, seq, mutation_map, contract)

        if s1 == 1.0:
            dup_key = (
                exp["cas_system"], exp["target_alignment_start"],
                exp.get("strand"), exp["guideRNA"]
            )
            if dup_key in seen_valid_keys:
                s1, reason = 0.0, "duplicate_experiment"
            else:
                seen_valid_keys.add(dup_key)

        if s1 == 0.0:
            invalid_experiments.append({
                "experiment": exp,
                "stage1_pass": False,
                "reason": reason
            })
            continue

        s2, s2_info = stage2(cell_types, exp, seq, mutation_map, contract, kmer_index)

        valid_experiments.append({
            "experiment": exp,
            "features": {
                "gc": s2_info["gc"],
                "distance_to_mutation": s2_info["distance"],
                "gc_score": s2_info["gc_score"],
                "dist_score": s2_info["dist_score"],
                "consistency": s2_info["consistency"],
                "offtarget_factor": s2_info["offtarget_factor"],
                "mutation_weight": s2_info["mutation_weight"],
                "cell_type": s2_info["cell_type"],
                "cell_type_accessibility": s2_info["cell_type_accessibility"],
                "mutation_region": s2_info["mutation_region"],
                "region_energy_offset": s2_info["region_energy_offset"]
            },
            "stage1": {"valid": True},
            "stage2": {
                "structural_score": s2,
                "weighted_score": s2_info["weighted_score"]
            }
        })

    with open(VALID_EXPERIMENTS_PATH, "w") as f:
        json.dump(valid_experiments, f, indent=2)

    with open(INVALID_EXPERIMENTS_PATH, "w") as f:
        json.dump(invalid_experiments, f, indent=2)

    return valid_experiments, invalid_experiments
