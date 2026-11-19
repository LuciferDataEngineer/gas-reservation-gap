"""Observational estimate: export-plant gas and the Melbourne morning price.

The dashboard never uses these method names. INSIGHTS.md does.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dowhy import CausalModel
from econml.dml import LinearDML
from sklearn.linear_model import LinearRegression


def _frame(daily: pd.DataFrame) -> pd.DataFrame:
    df = daily.dropna(
        subset=["lng_tj", "price_6am", "hdd_mel", "total_demand_tj", "longford_tj"]
    ).copy()
    df["winter"] = df["gas_date"].dt.month.isin([6, 7, 8]).astype(int)
    return df


def estimate(daily: pd.DataFrame) -> dict:
    df = _frame(daily)
    if len(df) < 200:
        raise SystemExit(f"not enough overlapping price days ({len(df)})")
    np.random.seed(17)
    causes = ["hdd_mel", "total_demand_tj", "longford_tj", "winter"]
    model = CausalModel(
        data=df,
        treatment="lng_tj",
        outcome="price_6am",
        common_causes=causes,
    )
    estimand = model.identify_effect(proceed_when_unidentifiable=True)
    linear = model.estimate_effect(
        estimand,
        method_name="backdoor.linear_regression",
        method_params={"fit_intercept": True},
    )
    per_tj = float(linear.value)
    refutes = []
    for name, kwargs in (
        (
            "placebo",
            {
                "method_name": "placebo_treatment_refuter",
                "placebo_type": "permute",
                "num_simulations": 5,
            },
        ),
        ("random_cause", {"method_name": "random_common_cause", "num_simulations": 5}),
        (
            "subset",
            {"method_name": "data_subset_refuter", "subset_fraction": 0.8, "num_simulations": 5},
        ),
    ):
        ref = model.refute_estimate(estimand, linear, **kwargs)
        refutes.append({"name": name, "new_effect": float(ref.new_effect), "summary": str(ref)})

    y = df["price_6am"].to_numpy()
    t = df["lng_tj"].to_numpy()
    w = df[causes].to_numpy()
    dml = LinearDML(
        model_y=LinearRegression(),
        model_t=LinearRegression(),
        discrete_treatment=False,
        random_state=17,
        cv=3,
    )
    dml.fit(y, t, W=w)
    marginal = np.asarray(dml.const_marginal_effect()).reshape(-1)
    low, high = dml.const_marginal_effect_interval(alpha=0.05)
    low_a = np.asarray(low).reshape(-1)
    high_a = np.asarray(high).reshape(-1)
    dml_per_tj = float(marginal[0])
    ci = (float(low_a[0]), float(high_a[0]))
    per_100 = per_tj * 100
    crosses_zero = ci[0] <= 0 <= ci[1]
    if abs(per_100) < 0.05 or crosses_zero:
        plain = (
            "Once the cold, Victorian demand and Longford supply are taken into account, "
            "days when the export plants took more gas did not come with a clearly higher "
            "or lower Melbourne morning price."
        )
    elif per_100 > 0:
        plain = (
            f"Holding the cold, Victorian demand and Longford supply steady, an extra 100 TJ "
            f"at the export plants lined up with a Melbourne morning price about "
            f"${per_100:.2f} per GJ higher."
        )
    else:
        plain = (
            f"Holding the cold, Victorian demand and Longford supply steady, an extra 100 TJ "
            f"at the export plants lined up with a Melbourne morning price about "
            f"${abs(per_100):.2f} per GJ lower."
        )
    winter = df[df["winter"] == 1]
    return {
        "rows": int(len(df)),
        "from": df["gas_date"].min().date().isoformat(),
        "to": df["gas_date"].max().date().isoformat(),
        "linear_per_tj": per_tj,
        "linear_per_100tj": per_100,
        "dml_per_tj": dml_per_tj,
        "dml_ci95_per_tj": ci,
        "ci_crosses_zero": crosses_zero,
        "placebo_new_effect": refutes[0]["new_effect"],
        "random_cause_new_effect": refutes[1]["new_effect"],
        "subset_new_effect": refutes[2]["new_effect"],
        "winter_price": float(winter["price_6am"].mean()) if len(winter) else None,
        "hdd_price_corr": float(df["hdd_mel"].corr(df["price_6am"])),
        "plain": plain,
    }
