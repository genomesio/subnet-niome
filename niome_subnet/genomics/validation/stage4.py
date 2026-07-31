import json
import numpy as np
import pandas as pd

from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import r2_score, mean_absolute_error
from sklearn.model_selection import KFold

from niome_subnet.utils.settings import (
    CONTRACT_PATH,
    VALID_EXPERIMENTS_PATH,
    STAGE3_DATASET,
    FINAL_REWARD_PATH,
)


def load_json(path):
    with open(path, "r") as f:
        return json.load(f)


def flatten_stage12(data):
    rows = []
    for item in data:
        exp = item["experiment"]
        feat = item["features"]
        rows.append({
            "experiment_id": exp["experiment_id"],
            "mutation": exp["mutation"],
            "cas_system": exp["cas_system"],
            "guideRNA": exp["guideRNA"],
            "start": exp["target_alignment_start"],
            "gc": feat["gc"],
            "distance": feat["distance_to_mutation"],
            "gc_score": feat["gc_score"],
            "dist_score": feat["dist_score"],
            "consistency": feat["consistency"],
            "stage2_score": item["stage2"]["structural_score"],
            "mutation_weight": feat.get("mutation_weight", 1.0),
            "weighted_score": item["stage2"].get("weighted_score", item["stage2"]["structural_score"])
        })
    return pd.DataFrame(rows)


def flatten_stage3(data):
    rows = []
    for item in data:
        rows.append({
            "experiment_id": item["experiment_id"],
            "mutation": item["mutation"],
            "cas_system": item["cas"],
            "gc": item["features"]["gc"],
            "distance": item["features"]["distance"],
            "gc_score": item["features"]["gc_score"],
            "dist_score": item["features"]["dist_score"],
            "consistency": item["features"]["consistency"],
            "energy": item["energy"],
            "mh": int(item["mh"]),
            "outcome": item["outcome"],
            "indel_length": item["indel_length"]
        })
    return pd.DataFrame(rows)


def build_X(df):
    required = ["gc", "distance", "gc_score", "dist_score", "consistency", "energy", "mh"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns in merged dataset: {missing}")
    return df[required]


def build_y(df):
    return pd.DataFrame({
        "is_cut": (df["outcome"] != "no_cut").astype(int),
        "is_hdr": (df["outcome"] == "HDR").astype(int),
        "indel_length": df["indel_length"]
    })


def evaluate(X, y, sample_weight=None, fold_seed=42, n_splits=5):
    n = len(X)
    k = min(n_splits, n) if n > 1 else 1
    kf = KFold(n_splits=max(k, 2), shuffle=True, random_state=fold_seed)
    r2s, maes, residual_stds = [], [], []

    for train_idx, test_idx in kf.split(X):
        X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
        y_train, y_test = y.iloc[train_idx], y.iloc[test_idx]
        sw_train = sample_weight.iloc[train_idx] if sample_weight is not None else None
        sw_test = sample_weight.iloc[test_idx] if sample_weight is not None else None

        model = RandomForestRegressor(n_estimators=200, random_state=42, max_depth=12)
        model.fit(X_train, y_train, sample_weight=sw_train)
        pred = model.predict(X_test)

        r2s.append(r2_score(y_test, pred, sample_weight=sw_test))
        maes.append(mean_absolute_error(y_test, pred, sample_weight=sw_test))
        residual_stds.append(np.std(y_test - pred))

    return {
        "r2_mean": float(np.mean(r2s)),
        "r2_std": float(np.std(r2s)),
        "mae_mean": float(np.mean(maes)),
        "mae_std": float(np.std(maes)),
        "residual_std_mean": float(np.mean(residual_stds)),
        "n_folds": len(r2s)
    }


def normalized_mae(mae_mean, y_full):
    scale = float(np.std(y_full))
    if scale < 1e-9:
        return mae_mean
    return mae_mean / scale


def run_stage4(n_folds: int = 5) -> dict:
    with open(CONTRACT_PATH) as f:
        fold_seed = json.load(f)["seed"]

    stage3 = flatten_stage3(load_json(STAGE3_DATASET))
    stage12 = flatten_stage12(load_json(VALID_EXPERIMENTS_PATH))

    stage12_slim = stage12[["experiment_id", "guideRNA", "start", "stage2_score",
                             "mutation_weight", "weighted_score"]]
    df = stage3.merge(stage12_slim, on="experiment_id", how="inner")

    if len(df) == 0:
        raise ValueError("Merge failed: no matching experiment_id between Stage 3 and Stage 12")

    X = build_X(df)
    y = build_y(df)
    sample_weight = df["mutation_weight"]

    results = {}
    for col in y.columns:
        results[col] = evaluate(X, y[col], sample_weight=sample_weight,
                                fold_seed=fold_seed, n_splits=n_folds)

    avg_r2 = np.mean([v["r2_mean"] for v in results.values()])
    avg_nmae = np.mean([
        normalized_mae(v["mae_mean"], y[col])
        for col, v in results.items()
    ])

    consistency_score = (0.7 * max(avg_r2, 0) + 0.3 * (1 - avg_nmae)) * 100
    total_weighted_score = float(df["weighted_score"].sum())

    if np.isnan(consistency_score):
        consistency_factor = 0.0
    else:
        consistency_factor = max(0.0, min(1.0, consistency_score / 100.0))

    final_reward = total_weighted_score * consistency_factor

    output = {
        "n_valid_experiments": len(df),
        "total_weighted_score": total_weighted_score,
        "consistency_score": float(consistency_score) if not np.isnan(consistency_score) else 0.0,
        "consistency_factor": consistency_factor,
        "final_reward": final_reward,
        "model_results": results
    }

    with open(FINAL_REWARD_PATH, "w") as f:
        json.dump(output, f, indent=2)

    return output
