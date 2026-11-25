# Statistical Analysis Package

This document describes the files in `app/analysis/`, how they work together,
and how the statistical-analysis workflow fits into the application.

## Purpose and scope

The analysis package works on **exported range CSV files**. It does not analyze
video frames or raw waveform samples. The range exporter in
`app/windows/range_csv_export.py` produces phase-summary and cycle-detail rows;
the analysis workflow loads those rows, calculates descriptive summaries,
creates plots and reference-boundary tables, and generates a comparator
configuration.

The generated ranges and comparator configuration are reference material.
This package does not compare a new measurement against a baseline, assign
warning/failure status, calculate a score, or decide pass/fail.

## Package files

| File | Responsibility |
|---|---|
| [`__init__.py`](../app/analysis/__init__.py) | Marks this directory as the `analysis` Python package. Its docstring describes the package; it contains no analysis logic. |
| [`treatment_profiles.py`](../app/analysis/treatment_profiles.py) | Defines built-in treatment-profile names and normalizes known spelling/punctuation variants to canonical names. |
| [`loader.py`](../app/analysis/loader.py) | Defines the supported measurement columns, reads and validates range-export CSVs, normalizes profile names, and builds a per-file ingestion report. |
| [`statistics.py`](../app/analysis/statistics.py) | Calculates descriptive statistics, derives reference boundaries from those statistics, and writes statistics/range CSVs. |
| [`plots.py`](../app/analysis/plots.py) | Creates and saves boxplots, histograms, and device-mean/error-bar plots. |
| [`comparator_config.py`](../app/analysis/comparator_config.py) | Reads the three range tables and creates an editable `comparator_config.json` describing available profiles, analyses, measurements, and default range selection. |
| [`runner.py`](../app/analysis/runner.py) | Orchestrates the analyses, statistics, range tables, plots, shared input files, and comparator configuration. It is the package's main workflow module. |

## How it fits into the application

The usual UI entry point is
[`RangeAnalysisWindow`](../app/windows/range_analysis_window.py). The user
selects one or more exported range CSVs, selects a treatment profile, chooses
an output folder and run name, and starts the analysis. The window calls
`load_range_csvs()` and then `run_all_analyses()`.

The overall data flow is:

```text
Exported range CSVs
        |
        v
loader.load_range_csvs()
  - validate/read rows
  - normalize treatment-profile names
  - return master DataFrame and file report
        |
        v
runner.run_all_analyses()
  - validate and normalize analysis data
  - summarize phase and device groups
  - derive device variability from device means
  - save statistics, ranges, and plots
  - generate comparator_config.json
        |
        v
Analysis output folder
```

The UI handles user input and displays errors/status. The package modules
perform the data processing and file generation. `run_analysis()` is also
available for callers that want to run one named analysis; the UI's normal
workflow runs all three analyses together.

## Input data and loading

### Expected CSV fields

`loader.py` uses `RANGE_EXPORT_COLUMNS` from
`app/windows/range_csv_export.py` as the export schema. The fields required to
load a file are:

- `Vibration`, `Phase`, `Record Type`, `Device number`, and `Treatment profile`
- The eight measurements in `NUMERIC_COLUMNS`:
  `Duration (s)`, `High Peak (mm)`, `Low Peak (mm)`, `Peak-to-Peak (mm)`,
  `Amplitude (mm)`, `Cycle Period (s)`, `Frequency (Hz)`, and `Cycle Count`

Cycle-detail summaries can also include `Cycle Start (s)` and `Cycle End (s)`.
These are added to the measurements used for cycle-detail analysis by
`CYCLE_DETAIL_NUMERIC_COLUMNS`.

### `load_range_csvs(paths)`

For each supplied path, the loader:

1. Opens the CSV as UTF-8 with an optional byte-order mark and checks that the
   required columns are present. A file with missing columns is reported as
   skipped.
2. Accepts rows whose `Record Type` is `Phase Summary` or `Cycle Detail`
   (case-insensitive). Other row types are counted as skipped.
3. Strips surrounding whitespace from exported fields and normalizes the
   treatment-profile name with `canonical_treatment_profile()`.
4. Skips rows without a vibration, phase, device number, or treatment profile.
5. Keeps valid summary and detail rows together in the returned master
   DataFrame. It adds `Source File`, containing the input file's basename.
6. Returns a second DataFrame with one report row per input file, including
   summary/detail counts, skipped-row counts, missing/invalid summary numeric
   value counts, and a load status.

Missing or non-finite numeric values in a summary row are counted in the
report; they do not by themselves cause that row to be discarded. The runner
converts measurement columns to numeric values, coercing invalid entries to
missing values before calculating statistics. File-level I/O, CSV, or encoding
errors are recorded in the report. If no valid `Phase Summary` row is loaded
from all input files, the loader raises `ValueError`, since the analyses
require at least one such row.

### `treatment_profiles.py`

`TREATMENT_PROFILES` lists the built-in profile names:

- `C2-(Left, Middle, Right)`
- `T1-(Left, Middle, Right)`
- `LUQ-RUQ`

`canonical_treatment_profile(value)` removes punctuation and spaces when
checking known names, so common formatting variants of a built-in profile are
combined under its canonical name. Unknown/custom profile names are preserved
after trimming whitespace; they are not rejected.

## Analysis orchestration

`runner.py` defines the three analysis names and output folder names:

- **Phase Analysis**
- **Device-Level Analysis**
- **Device-Variability Analysis**
- `Statisticsoutput`, `Rangesoutput`, `Input_files`, and `visualization`

### Preparation and grouping

Before analysis, `_analysis_rows()` copies the master DataFrame. If older or
custom input data lacks `Record Type` or `Vibration`, it supplies `Phase
Summary` or `All Vibrations`, respectively. It verifies there is at least one
phase-summary row and ensures cycle-detail measurement columns exist and are
numeric (invalid values become missing).

`_summarize_records_by_vibration()` processes `Phase Summary` and `Cycle
Detail` rows separately. For each record type, it calculates:

1. An aggregate with `Vibration = "All Vibrations"`, grouped by the requested
   analysis dimensions.
2. Additional groups split by the original `Vibration` label.

Only groups with data produce rows. Consequently, vibration-specific rows may
be absent for some devices or phases, and each row's `n` reflects the
available values for that group.

### Phase Analysis

Groups measurements by `Treatment profile` and `Phase`. It includes both
`Phase Summary` and `Cycle Detail` rows in its statistics and range tables.
Summary rows use the eight standard measurements; detail rows also include
cycle start and end times. The CSV identifies each row's record type and
vibration grouping.

Plots use phase-summary rows only. For each standard measurement, the runner
requests a boxplot and a histogram grouped by treatment profile and phase.

### Device-Level Analysis

Groups by `Treatment profile`, `Device number`, and `Phase`, again retaining
separate statistics for phase-summary and cycle-detail records and for
`All Vibrations` and individual vibrations.

It also creates a device-mean plot for each standard measurement, using the
phase-summary, `All Vibrations` rows. Each point is a device's mean for a
profile/phase; the plotted error bar uses that device group's standard
deviation.

### Device-Variability Analysis

This analysis describes the distribution of **device means**, not the
distribution of every event pooled together. It takes the per-device
statistics from Device-Level Analysis and, for each treatment profile,
vibration grouping, record type, phase, and measurement, summarizes the
devices' `mean` values. Each contributing device therefore contributes one
value, regardless of how many events it had.

These summaries use population standard deviation (`ddof=0`). The resulting
statistics and reference ranges are written to the device-variability files.
For plots, the runner selects phase-summary, `All Vibrations` device means,
pivots them into one row per profile/device/phase, and saves boxplots and
histograms grouped by profile and phase.

## Statistics and reference ranges

### `statistics.py`: descriptive summaries

`summarize_values()` summarizes one numeric series;
`summarize_groups()` applies the same calculation to each requested DataFrame
group and each requested measurement. Both use `_statistics()`:

- Values are converted to numeric, invalid entries are discarded, and `n`
  counts the remaining values.
- It calculates mean, standard deviation, median, mode, minimum, maximum,
  5th/95th percentiles (`p05`, `p95`), first/third quartiles (`q25`, `q75`),
  IQR (`q75 - q25`), and coefficient of variation (`cv_pct`).
- Standard deviation is the sample standard deviation by default. The
  population option uses population standard deviation. A sample with fewer
  than two values has no sample standard deviation.
- NumPy's percentile calculation is used. Mode ties are returned as
  semicolon-separated values; if there is no repeated value, mode is missing.
- `cv_pct` is standard deviation divided by the absolute mean, multiplied by
  100. It is missing if the standard deviation is unavailable or the mean is
  zero.
- If a group has no valid values for a measurement, `n` is zero and its
  statistics are missing.

`save_statistics()` writes a DataFrame to CSV, creates its parent directory
when needed, omits the DataFrame index, and represents missing values as
blank cells.

### Derived reference boundaries

`generate_ranges()` copies each statistics row and adds the following
lower/upper columns:

| Added columns | Calculation |
|---|---|
| `range_p05_lower`, `range_p95_upper` | Copies the row's `p05` and `p95` values. |
| `range_2sd_lower`, `range_2sd_upper` | `mean - 2 × std`, `mean + 2 × std`. |
| `range_3sd_lower`, `range_3sd_upper` | `mean - 3 × std`, `mean + 3 × std`. |
| `range_iqr_lower`, `range_iqr_upper` | `q25 - 1.5 × iqr`, `q75 + 1.5 × iqr`. |

Thus the Ranges CSV reuses the same statistics row rather than re-reading the
raw events. P5/P95 are percentile bounds; 2-SD/3-SD are mean-based bounds; the
IQR values are Tukey-style outlier fences, not the quartile interval itself.
The source statistics, including median, remain in the Ranges CSV, but median
does not calculate any of these added bounds. Missing standard deviation or
quartile data yields missing corresponding bounds. `save_ranges()` validates
the expected statistics columns, generates the boundaries, and writes the
result with the same CSV behavior as `save_statistics()`.

These are descriptive reference boundaries. They are not, by themselves,
validated tolerances or pass/fail limits.

## Plots

`plots.py` uses Matplotlib's non-interactive `Agg` backend so it can write
images without opening plot windows. It creates output directories as needed,
skips plots when there is no usable data, and returns the paths of images it
saves. `_safe_name()` converts measurement labels into filename-safe strings.

- `save_group_boxplots()` makes one boxplot per standard measurement, grouped
  by the supplied columns. It displays means and omits groups with no valid
  values for that measurement.
- `save_group_histograms()` makes one histogram per standard measurement,
  drawing each supplied group as a separate distribution. It adds a legend
  when at most eight groups have data.
- `save_device_mean_plots()` makes one plot per standard measurement. Points
  are device means, error bars are device standard deviations, and the x-axis
  identifies treatment profile, device, and phase.

The runner supplies analysis-specific directories and filename prefixes;
the plot helpers do not decide what an analysis group means.

## Comparator configuration

`generate_comparator_config(output_dir, ranges_dir=None)` reads:

- `phase_population_ranges.csv`
- `device_level_ranges.csv`
- `device_variability_ranges.csv`

For each file it checks for `Treatment profile`, `Measurement`,
`range_p05_lower`, and `range_p95_upper`. It collects the profiles and
measurements present, then writes `comparator_config.json` in `output_dir`.

The JSON has schema version 1 and one entry per treatment profile. Each entry
contains relative paths to the three range files, enabled-analysis flags,
available measurements per analysis, and a default
`range_selection_method` of `p5_p95`. Warning thresholds, failure thresholds,
scoring rules, and analysis weighting are initialized to `null` for later
editing. The function raises an error for missing required columns, rows
without a profile or measurement, or an absence of profiles. It generates
configuration only; it does not perform any comparisons or scoring.

## Runner entry points and output

### `run_all_analyses(master_df, output_dir, file_report=None)`

This is the normal all-in-one entry point. It validates the input, creates the
output directories, saves one shared copy of the master dataset and optional
ingestion report, calculates the device summary once for reuse, runs all
three analyses, and finally generates the comparator configuration.

The resulting folder layout is:

```text
<output_dir>/
├── Input_files/
│   ├── master_dataset.csv
│   └── input_files.csv              # when a file report was supplied
├── Statisticsoutput/
│   ├── phase_population_statistics.csv
│   ├── device_level_statistics.csv
│   └── device_variability_statistics.csv
├── Rangesoutput/
│   ├── phase_population_ranges.csv
│   ├── device_level_ranges.csv
│   └── device_variability_ranges.csv
├── visualization/
│   ├── phase_analysis/
│   ├── device_level_analysis/
│   └── device_variability_analysis/
└── comparator_config.json
```

It returns a dictionary with output paths, per-analysis results, generated CSV
and plot paths, directory paths, and the comparator-config path.

### `run_analysis(master_df, analysis, output_dir, file_report=None)`

This lower-level entry point runs one of the three named analyses. An unknown
analysis name raises `ValueError`. It writes the shared master dataset and
optional file report directly into `output_dir`; the selected analysis's
statistics, ranges, and plots are also written using that directory as the
default output location. Its plots go under `visualizations/` (plural), while
the all-analyses entry point explicitly uses `visualization/` (singular). If
Device-Variability Analysis is run without a precomputed device summary, the
runner also writes the intermediate device-level statistics and ranges. This
single-analysis entry point does not generate the comparator configuration.
The UI normally uses `run_all_analyses()` instead.

The runner validates that the master dataset is non-empty and contains at
least one phase-summary row. It does not enforce the UI's rule against
re-using an existing run folder; the UI checks that before calling the
runner.

## Important boundaries

- The package consumes already-exported measurements; it does not extract
  cycles or positions from video.
- `Phase Summary` and `Cycle Detail` are analyzed separately. Detail rows can
  contribute cycle start/end statistics; they are not treated as summary
  events.
- `All Vibrations` is an additional aggregate, not a replacement for
  vibration-specific rows.
- Device variability summarizes per-device means so that devices with
  different event counts are not weighted by those event counts.
- Range tables add statistical boundaries; they do not set clinical or
  operational acceptance criteria.
- Comparator configuration is prepared for later use. Comparison decisions,
  scoring, warnings, failures, and reporting belong to a separate workflow.
