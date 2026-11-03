"""Two-part model: an anchor margin + a correlation-weighted forest that learns corrections to it.

Trees predict in steps, so a forest asked to predict the margin directly can't extend a straight
line. Instead the forest learns how far the *anchor* tends to be off and in which direction:

  anchor  = weighted poll average                                  (polled race)
          = a*state_lean + b*generic_ballot + c*midterm + d          (unpolled race; OLS on history)
  margin  = anchor + shrink * forest_correction(features, anchor)

`shrink` is picked by leave-one-cycle-out CV (0 = trust the anchor, 1 = trust the forest fully).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..features import FEATURES, to_matrix
from .forest import CorrelationWeightedForest

BASE_COLS = ["state_lean", "generic_ballot"]
MODEL_FEATURES = FEATURES + ["anchor"]


def _hide_polls(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d[["poll_margin", "poll_sd", "poll_quality"]] = np.nan
    d["poll_n_log"] = 0.0
    return d


class ElectionModel:
    def __init__(self, n_trees=400, shrink=0.75, mask_poll_frac=0.35, seed=0, **forest_kw):
        self.n_trees, self.shrink, self.mask_poll_frac, self.seed = n_trees, shrink, mask_poll_frac, seed
        self.forest_kw = forest_kw

    # ---- anchor
    def _base_design(self, df):
        return np.column_stack([df[c].astype(float).fillna(0).values for c in BASE_COLS] + [np.ones(len(df))])

    def anchor(self, df: pd.DataFrame) -> np.ndarray:
        lin = self._base_design(df) @ self.coef_
        pm = df["poll_margin"].astype(float)
        return np.where(pm.notna(), pm.fillna(0).values, lin)

    def _X(self, df):
        d = df.copy()
        d["anchor"] = self.anchor(df)
        return to_matrix(d[MODEL_FEATURES].to_dict("records"), MODEL_FEATURES)

    # ---- fit / predict
    def fit(self, df: pd.DataFrame):
        y = df["margin_actual"].values
        self.coef_ = np.linalg.lstsq(self._base_design(df), y, rcond=None)[0]
        rng = np.random.default_rng(self.seed)
        aug = _hide_polls(df.iloc[rng.choice(len(df), int(self.mask_poll_frac * len(df)), replace=False)])
        full = pd.concat([df, aug], ignore_index=True)
        X = self._X(full)
        resid = full["margin_actual"].values - self.anchor(full)
        self.forest_ = CorrelationWeightedForest(n_trees=self.n_trees, random_state=self.seed, mask_poll_frac=0,
                                                 **self.forest_kw).fit(X, resid, MODEL_FEATURES)
        return self

    def tree_margins(self, df) -> np.ndarray:
        """(n_races, n_trees) margin implied by each individual tree."""
        corr = self.forest_.tree_predictions(self._X(df), MODEL_FEATURES)
        return self.anchor(df)[:, None] + self.shrink * corr

    def predict(self, df):
        tm = self.tree_margins(df)
        return tm.mean(1), tm.std(1)

    def correction(self, df) -> np.ndarray:
        return self.forest_.predict(self._X(df), MODEL_FEATURES)[0]
