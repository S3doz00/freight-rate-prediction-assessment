"""Train on past loads, audit rolling time holdouts, and write submission CSVs.

Only NumPy and pandas are needed for modeling. The supplied score.py needs
matplotlib as specified in requirements.txt.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
START = pd.Timestamp("2025-01-01")


class FeatureBuilder:
    def __init__(self, reference: pd.DataFrame):
        positive_weight = reference.loc[reference["weight"] >= 0, "weight"]
        self.weight_median = float(positive_weight.median())
        self.market_median = float(reference["market_index"].median())
        cities = pd.concat(
            [
                reference[["pickup", "pickup_lat", "pickup_lon"]].rename(
                    columns={"pickup": "city", "pickup_lat": "lat", "pickup_lon": "lon"}
                ),
                reference[["delivery", "delivery_lat", "delivery_lon"]].rename(
                    columns={"delivery": "city", "delivery_lat": "lat", "delivery_lon": "lon"}
                ),
            ],
            ignore_index=True,
        )
        self.city_coordinates = cities.groupby("city")[["lat", "lon"]].median()

    def prepare(self, frame: pd.DataFrame) -> pd.DataFrame:
        d = frame.copy()
        for role in ("pickup", "delivery"):
            for axis in ("lat", "lon"):
                col = f"{role}_{axis}"
                if col not in d:
                    d[col] = d[role].map(self.city_coordinates[axis])
                if d[col].isna().any():
                    raise ValueError(f"Unknown {role} city coordinates: {d.loc[d[col].isna(), role].unique()}")
        d["date"] = pd.to_datetime(d["date"], errors="raise")
        d["day"] = (d["date"] - START).dt.days.astype(float) / 100.0
        d["dow"] = d["date"].dt.dayofweek
        bad_weight = d["weight"].isna() | (d["weight"] < 0)
        d["weight_missing"] = bad_weight.astype(float)
        d["weight"] = d["weight"].mask(bad_weight, self.weight_median)
        d["weight_10k"] = d["weight"] / 10_000.0
        d["distance_1k"] = d["distance"] / 1_000.0
        if "market_index" in d:
            d["market_missing"] = d["market_index"].isna().astype(float)
            d["market_index"] = d["market_index"].fillna(self.market_median)
        return d

    def matrix(self, frame: pd.DataFrame, full: bool, date_trend: bool = True) -> np.ndarray:
        d = self.prepare(frame)
        dist = d["distance_1k"].to_numpy(float)
        weight = d["weight_10k"].to_numpy(float)
        day = d["day"].to_numpy(float)
        cols = [np.ones(len(d)), dist, dist**2, weight, d["weight_missing"].to_numpy(float)]
        for equipment in ("Flatbed", "Reefer"):
            mask = d["equipment"].eq(equipment).to_numpy(float)
            cols.extend([mask, mask * dist])
        for axis, center, scale in (("lat", 38, 10), ("lon", -95, 20)):
            for role in ("pickup", "delivery"):
                geo = (d[f"{role}_{axis}"].to_numpy(float) - center) / scale
                cols.extend([geo, dist * geo])
        cols.append(dist * weight)
        if date_trend:
            cols.extend([day, day**2])
        for dow in range(1, 7):
            cols.append(d["dow"].eq(dow).to_numpy(float))
        if full:
            market = d["market_index"].to_numpy(float)
            quote = d["quote_signal"].to_numpy(float)
            cols.extend([market, d["market_missing"].to_numpy(float), quote, dist * market, dist * quote])
        x = np.column_stack(cols)
        if not np.isfinite(x).all():
            raise ValueError("Nonfinite model features")
        return x


def fit(x: np.ndarray, y: np.ndarray, robust: bool) -> np.ndarray:
    beta = np.linalg.lstsq(x, y, rcond=None)[0]
    if robust:
        for _ in range(20):
            residual = y - x @ beta
            scale = max(1.0, 1.4826 * np.median(np.abs(residual - np.median(residual))))
            weights = np.minimum(1.0, 1.345 * scale / np.maximum(np.abs(residual), 1e-9))
            root = np.sqrt(weights)
            updated = np.linalg.lstsq(x * root[:, None], y * root, rcond=None)[0]
            if np.max(np.abs(updated - beta)) < 1e-6:
                beta = updated
                break
            beta = updated
    return beta


def metrics(y: np.ndarray, pred: np.ndarray) -> dict[str, float]:
    error = pred - y
    return {
        "mae": round(float(np.mean(np.abs(error))), 2),
        "rmse": round(float(np.sqrt(np.mean(error**2))), 2),
        "median_ae": round(float(np.median(np.abs(error))), 2),
        "bias": round(float(np.mean(error)), 2),
    }


def validate(train: pd.DataFrame) -> tuple[list[dict], str]:
    results = []
    for month in ("2025-08", "2025-09", "2025-10"):
        start = pd.Timestamp(f"{month}-01")
        end = start + pd.offsets.MonthBegin(1)
        past = train.loc[train["date"] < start]
        holdout = train.loc[(train["date"] >= start) & (train["date"] < end)]
        builder = FeatureBuilder(past)
        y_train = past["posted_rate"].to_numpy(float)
        y_test = holdout["posted_rate"].to_numpy(float)
        result = {"month": month, "train_rows": len(past), "holdout_rows": len(holdout)}
        for label, full, trend in (("common_no_trend", False, False),
                                   ("full_with_trend", True, True)):
            x_train = builder.matrix(past, full=full, date_trend=trend)
            x_test = builder.matrix(holdout, full=full, date_trend=trend)
            for method, robust in (("ordinary", False), ("huber", True)):
                pred = x_test @ fit(x_train, y_train, robust=robust)
                result[f"{label}_{method}"] = metrics(y_test, pred)
        results.append(result)
        print(month, result)
    candidates = ("common_no_trend_ordinary", "common_no_trend_huber",
                  "full_with_trend_ordinary", "full_with_trend_huber")
    def total_mae(key: str) -> float:
        return sum(r[key]["mae"] * r["holdout_rows"] for r in results)
    selected = min(candidates, key=total_mae)
    return results, selected


def main() -> None:
    train = pd.read_csv(DATA / "train_test.csv", parse_dates=["date"])
    validation = pd.read_csv(DATA / "validation.csv")
    template = pd.read_csv(DATA / "validation_predictions_template.csv")
    december = pd.read_csv(DATA / "december_chart_inputs.csv")
    if train["load_id"].duplicated().any() or validation["load_id"].duplicated().any():
        raise ValueError("Duplicate load_id in source data")
    if len(validation) != len(template) or set(validation["load_id"]) != set(template["load_id"]):
        raise ValueError("Template IDs do not match validation IDs")

    history, selected = validate(train)
    builder = FeatureBuilder(train)
    y = train["posted_rate"].to_numpy(float)
    full = selected.startswith("full")
    trend = full
    robust = selected.endswith("huber")
    x_train = builder.matrix(train, full=full, date_trend=trend)
    x_final = builder.matrix(validation, full=full, date_trend=trend)
    coefficients = fit(x_train, y, robust=robust)
    pred = x_final @ coefficients
    if not np.isfinite(pred).all() or (pred <= 0).any():
        raise ValueError("Invalid validation prediction")
    predictions = pd.DataFrame({"load_id": validation["load_id"], "predicted_rate": np.round(pred, 2)})
    completed = template[["load_id"]].merge(predictions, on="load_id", validate="one_to_one", sort=False)
    completed.to_csv(ROOT / "validation_predictions.csv", index=False)

    # December lacks market index and quote signal. If a full model wins,
    # use the strongest model that can score the fixed December input.
    chart_selected = selected if not full else "common_no_trend_huber"
    x_chart = builder.matrix(december, full=False, date_trend=False)
    if full:
        chart_x_train = builder.matrix(train, full=False, date_trend=False)
        chart_coefficients = fit(chart_x_train, y, robust=True)
    else:
        chart_coefficients = coefficients
    chart_pred = x_chart @ chart_coefficients
    if not np.isfinite(chart_pred).all() or (chart_pred <= 0).any():
        raise ValueError("Invalid December prediction")
    december["predicted_rate"] = np.round(chart_pred, 2)
    december.to_csv(DATA / "december_chart_inputs.csv", index=False)
    report = {"holdouts": history, "selected_model": selected, "chart_model": chart_selected,
              "validation_rows": len(completed), "december_rows": len(december)}
    (ROOT / "validation_metrics.json").write_text(json.dumps(report, indent=2) + "\n")
    print("Selected model:", selected)
    print("Wrote validation_predictions.csv and filled December chart inputs")


if __name__ == "__main__":
    main()
