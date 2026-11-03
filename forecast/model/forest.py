"""Correlation-weighted bootstrap random forest.

A standard random forest picks a uniformly random subset of features for each tree (or split).
Here every tree:

  1. draws a bootstrap sample of historical races (rows, with replacement);
  2. measures, *on that bootstrap sample only*, how strongly each factor correlates with the
     actual result (|Spearman rho|, discounted by how often the factor was observed);
  3. turns those correlations into sampling weights  w_j  ~  (coverage_j * |rho_j|)^tau  + floor;
  4. samples its factor subset in proportion to w  (without replacement), and
  5. grows a regression tree on (bootstrap rows x weighted factor subset) to predict the D-R margin.

Because the weights are re-estimated on every bootstrap sample, we get a full *distribution* of
factor weights (not a point estimate) - that is what the "bootstrapped weights" chart shows.
Out-of-bag (OOB) rows give an honest error estimate without a separate hold-out set.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.stats import spearmanr
from sklearn.tree import DecisionTreeRegressor

POLL_FEATURES = ("poll_margin", "poll_n_log", "poll_sd", "poll_quality")


@dataclass
class CorrelationWeightedForest:
    n_trees: int = 400
    max_feature_frac: float = 0.5
    tau: float = 1.0
    floor: float = 0.02
    min_samples_leaf: int = 6
    max_depth: int | None = 12
    mask_poll_frac: float = 0.35     # augment: copies of races with polls hidden -> learns the "unpolled race" path
    random_state: int = 0
    # Factors that only vary by election cycle (economy, generic ballot, midterm). Their correlation is
    # discounted by the number of distinct cycles, not the number of races, so 6 data points can't masquerade
    # as 800. Significance-adjusted |rho| = max(0, |rho| - z/sqrt(n_eff - 3)), n_eff = #cycles for these.
    cycle_features: tuple = ("generic_ballot", "midterm_inparty", "inflation_inparty", "oil_inparty", "unemp_inparty")
    z: float = 1.0

    features_: list = field(default_factory=list)
    trees_: list = field(default_factory=list)       # (tree, feature_idx)
    weights_: np.ndarray | None = None               # (n_trees, n_features) sampling weights
    selected_: np.ndarray | None = None              # (n_trees, n_features) bool
    oob_pred_: np.ndarray | None = None

    # ------------------------------------------------------------------ fit
    def fit(self, X: np.ndarray, y: np.ndarray, feature_names: list[str]):
        rng = np.random.default_rng(self.random_state)
        keep = [j for j in range(X.shape[1]) if np.isfinite(X[:, j]).mean() > 0.05 and np.nanstd(X[:, j]) > 0]
        self.features_ = [feature_names[j] for j in keep]
        X = X[:, keep]
        n, p = X.shape

        # augmentation rows (polls masked)
        poll_idx = [j for j, f in enumerate(self.features_) if f in POLL_FEATURES]
        m = int(self.mask_poll_frac * n)
        aug_src = rng.choice(n, m, replace=False) if m and poll_idx else np.array([], int)
        Xa = X[aug_src].copy()
        for j in poll_idx:
            Xa[:, j] = 0.0 if self.features_[j] == "poll_n_log" else np.nan
        X_all = np.vstack([X, Xa])
        y_all = np.concatenate([y, y[aug_src]])
        orig_of = np.concatenate([np.arange(n), aug_src])   # augmented rows map back to their source race
        N = len(y_all)

        k = max(2, int(round(self.max_feature_frac * p)))
        self.trees_, W, S, R = [], np.zeros((self.n_trees, p)), np.zeros((self.n_trees, p), bool), np.zeros((self.n_trees, p))
        oob_sum, oob_cnt = np.zeros(n), np.zeros(n)
        for t in range(self.n_trees):
            idx = rng.integers(0, N, N)
            w, rho = self._corr_weights(X_all[idx], y_all[idx], return_rho=True)
            R[t] = rho
            feats = rng.choice(p, size=k, replace=False, p=w)
            tree = DecisionTreeRegressor(min_samples_leaf=self.min_samples_leaf, max_depth=self.max_depth,
                                         random_state=int(rng.integers(1 << 31)))
            tree.fit(X_all[idx][:, feats], y_all[idx])
            self.trees_.append((tree, feats))
            W[t], S[t, feats] = w, True
            in_bag = np.zeros(n, bool)
            in_bag[np.unique(orig_of[idx])] = True
            oob = np.where(~in_bag)[0]
            if len(oob):
                oob_sum[oob] += tree.predict(X[oob][:, feats])
                oob_cnt[oob] += 1
        self.weights_, self.selected_, self.rho_ = W, S, R
        self.oob_pred_ = np.where(oob_cnt > 0, oob_sum / np.maximum(oob_cnt, 1), np.nan)
        return self

    def _corr_weights(self, X, y, return_rho=False):
        p = X.shape[1]
        w, rhos = np.zeros(p), np.zeros(p)
        for j in range(p):
            ok = np.isfinite(X[:, j])
            if ok.sum() < 10 or np.nanstd(X[ok, j]) == 0:
                continue
            xj, yj = X[ok, j], y[ok]
            rho = spearmanr(xj, yj).statistic
            if self.features_[j] in self.cycle_features:
                n_eff = len(np.unique(xj))      # evidence = number of elections, not races
                if n_eff < 4:
                    continue
            else:
                n_eff = ok.sum()
            if not np.isfinite(rho):
                continue
            rhos[j] = rho
            adj = max(0.0, abs(rho) - self.z / np.sqrt(max(n_eff - 3, 1)))
            w[j] = (adj * ok.mean()) ** self.tau
        w = w + self.floor
        w = w / w.sum()
        return (w, rhos) if return_rho else w

    # ------------------------------------------------------------------ predict
    def _align(self, X, feature_names):
        col = {f: i for i, f in enumerate(feature_names)}
        return np.column_stack([X[:, col[f]] if f in col else np.full(len(X), np.nan) for f in self.features_])

    def tree_predictions(self, X, feature_names) -> np.ndarray:
        Xa = self._align(X, feature_names)
        return np.column_stack([tree.predict(Xa[:, feats]) for tree, feats in self.trees_])

    def predict(self, X, feature_names):
        tp = self.tree_predictions(X, feature_names)
        return tp.mean(1), tp.std(1)

    # ------------------------------------------------------------------ diagnostics
    def weight_summary(self) -> list[dict]:
        out = []
        mdi = np.zeros(len(self.features_))
        for tree, feats in self.trees_:
            mdi[feats] += tree.feature_importances_
        mdi /= max(mdi.sum(), 1e-9)
        for j, f in enumerate(self.features_):
            w = self.weights_[:, j]
            out.append({"feature": f, "rho": float(self.rho_[:, j].mean()), "cycleLevel": f in self.cycle_features, "mean": float(w.mean()), "p05": float(np.quantile(w, .05)),
                        "p95": float(np.quantile(w, .95)), "selected": float(self.selected_[:, j].mean()),
                        "importance": float(mdi[j]), "hist": np.histogram(w, bins=16, range=(0, max(0.6, w.max())))[0].tolist()})
        return sorted(out, key=lambda r: -r["mean"])

    def tree_sample(self, k=40) -> list[dict]:
        """A few trees for the UI: which factors each tree drew and its depth/leaves."""
        step = max(1, len(self.trees_) // k)
        return [{"i": i, "features": [self.features_[j] for j in feats], "depth": int(tree.get_depth()),
                 "leaves": int(tree.get_n_leaves())} for i, (tree, feats) in list(enumerate(self.trees_))[::step][:k]]


def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))
