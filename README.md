# Freight Rate Prediction Assessment

This repository trains a freight rate model, evaluates it with forward monthly holdouts, predicts the 12,000 final loads, and fills the fixed December chart input. The assessment datasets are not published here; place the provided files in `data/` before running the code.

## Required input files

```text
data/train_test.csv
data/validation.csv
data/validation_predictions_template.csv
data/december_chart_inputs.csv
```

The supplied files may use hyphens rather than underscores in their names. Rename them to the paths above. The December CSV should initially have an empty `predicted_rate` column.

## Run

Use Python 3.10 or newer:

```bash
python -m pip install -r requirements.txt
python train_predict.py
python score.py --predictions validation_predictions.csv --december-predictions data/december_chart_inputs.csv
python make_report.py
```

`train_predict.py` writes `validation_predictions.csv` with exactly `load_id,predicted_rate` in template order, fills all 31 December rows, and writes local holdout metrics to `validation_metrics.json`. The provided scorer checks both CSVs and creates `scorer_results/candidate_december.png`; it does not reveal the hidden final score. `make_report.py` inserts that chart into `report.docx`.

## Validation and model

The labeled data spans January through October 2025, while the final rows are in November and December. To reflect that order, the script trains on past data and holds out August, September, and October in turn. It compares ordinary and Huber regression using two feature sets. The selected model is the common-feature Huber fit, with a pooled holdout MAE of $104.37 across 14,282 holdout rows. Monthly MAEs were $99.49, $104.73, and $108.82.

The selected features include distance, equipment, weight, origin and destination coordinates, day of week, and interactions. Missing or negative weights are replaced with the median positive weight from the training portion and flagged. Coordinates allow predictions for city names unseen in training. The optional market-index, quote-signal, and extrapolated date-trend features did not improve the forward holdouts. Huber fitting limits the influence of extreme posted rates.

The December input lacks coordinates; the script fills them from the labeled city records. The same selected model produces the final and December predictions. Only the day of week changes for the fixed December load.

## Files

- `train_predict.py`: feature preparation, rolling validation, model selection, and output generation.
- `score.py`: supplied scorer, converted from the provided RTF copy to executable Python text.
- `make_report.py`: generates a DOCX report after the scorer creates its chart.
- `requirements.txt`: Python dependencies.

The final validation labels remain private to the assessment evaluator.
