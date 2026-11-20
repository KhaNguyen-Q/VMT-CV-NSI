--------------------------------------------------
COMPARATOR CONFIGURATION GENERATION
--------------------------------------------------

Purpose

Generate a centralized comparison configuration from
the baseline range datasets.

The configuration file does not replace the baseline
range datasets.

The baseline range datasets remain the authoritative
reference data used during comparison.

The configuration file defines how those baseline
datasets are interpreted.

Input

    phase_population_ranges.csv

    device_level_ranges.csv

    device_variability_ranges.csv

Output

    comparator_config.json

Configuration Content

    • Enabled analyses
    • Enabled measurements
    • Range selection method
    • Warning thresholds
    • Failure thresholds
    • Scoring rules
    • Analysis weighting

Generation Defaults

    • Write one comparator_config.json containing a separate
      treatment_profiles entry for each profile found in the
      three range datasets.
    • Enable analyses and measurements present in that profile's
      range data.
    • Set range_selection_method to "p5_p95".
    • Leave warning thresholds, failure thresholds, scoring
      rules, and analysis weighting unset (null) for editing.
    • Include the range dataset filenames for each analysis.

--------------------------------------------------
FUTURE COMPARISON WORKFLOW
--------------------------------------------------

Future comparison workflows shall:

    1. Load a processed CSV.
    2. Identify the treatment profile.
    3. Load comparator_config.json.
    4. Load the corresponding baseline range datasets.
    5. Compare measurements against the configured
       statistical boundaries.
    6. Generate comparison results and summary metrics.

The baseline generation stage produces statistical
reference datasets only.

Comparison scoring, warning classification, pass/fail
evaluation, and reporting are performed by separate
comparison workflows.