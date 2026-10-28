# Comparison report: calculation and assessment reference

This reference explains how to reproduce the values and decision rules shown
in the exported comparison report. The same calculation sequence is used for
the internal and client report. **Phase 1 and Phase 2 contribute to the
assessment; Phase 3 and other phase labels are informational.**

The exported report appendix contains the assessment criteria summary table
and a link to the detailed external "Appendix Measurement and Assessment
Method" document. The equations and explanations below are maintained here as
the project's implementation reference; the exported report no longer embeds
this detailed material.

## Assessment rules at a glance

- **FAIL** if any Phase 2 range differs from the Phase 2 baseline mean by at
  least 20 cycles, or if an assessed mean peak-to-peak or period is outside its
  baseline mean ± 3 sample standard deviations.
- **REVIEW** if no failure applies and a Phase 1 cycle count differs from its
  reference by at least 2, or a Phase 2 cycle count differs from its baseline
  mean by at least 20% but less than 20 cycles.
- **INCONCLUSIVE** if no failure applies but required measurements or interval
  coverage are missing or insufficient. This takes precedence over REVIEW.
- **PASS** if required measurements and coverage are sufficient and no FAIL or
  REVIEW condition applies.

The checks and numeric values that lead to these outcomes are described below.
The phase tables contain measurements; the assessment criteria are applied to
the selected Phase 1 and Phase 2 intervals.

## 1. Align intervals between recordings

Times are elapsed seconds from the beginning of each recording. The selected
baseline range is `[t₀, t₁]`. Given the selected offset `Δt`, corresponding
comparison data is read on its own time axis:

```text
comparison time = baseline time − Δt
comparison interval = [t₀ − Δt, t₁ − Δt]
```

The offset is used to align measurements; it is not itself an acceptance
criterion. A range without sufficient overlapping comparison coverage cannot
be fully assessed.

## 2. Detect extrema and count cycles

The comparison window detects local extrema on the **full recording**, then
selects the detected extrema whose times fall inside each range. The prominence
threshold is 5% of the full recording's position range. Detecting on the full
recording makes the threshold independent of how narrowly a user selects an
interval.

Let `Ntop` and `Nbottom` be the numbers of detected tops and bottoms inside a
selected interval:

```text
cycle count = min(Ntop, Nbottom)
```

The smaller count avoids treating an unmatched top or bottom at a range
boundary as an additional full cycle. Each Phase 2 comparison interval is
assessed separately; the total-cycle row that sums displayed intervals is not
the Phase 2 threshold calculation.

## 3. Calculate peak-to-peak values and amplitude

For consecutive opposite extrema with positions `yᵢ₋₁` and `yᵢ`, the
peak-to-peak value is:

```text
pᵢ = |yᵢ − yᵢ₋₁|
amplitudeᵢ = pᵢ / 2
```

Within each interval, if more than two peak-to-peak values are available, each
value is compared with the median of all the *other* values. A value is kept
for amplitude summaries if it is at least half that median. This excludes
values that are likely detection artifacts.

For retained values `p₁ … pₙ`:

```text
mean peak-to-peak = (p₁ + p₂ + … + pₙ) / n
mean amplitude = mean peak-to-peak / 2
```

The Phase 1 table reports mean peak-to-peak and mean amplitude. If multiple
intervals are selected for a phase, the table mean pools all retained
per-cycle values: each interval mean is multiplied by its retained sample
count, and those products are divided by the total retained sample count. The
table does not take an unweighted average of interval means. Phase 2 reports
the maximum retained peak-to-peak value pooled across selected intervals and
half that value as maximum amplitude. Pixel values convert to millimetres using
`1 px = 0.1 mm`.

## 4. Calculate period and frequency

For consecutive detected top times `t₁ … tₘ`, each period is the elapsed time
between two tops:

```text
Tⱼ = tⱼ₊₁ − tⱼ
mean period = (T₁ + T₂ + … + Tₘ₋₁) / (m − 1)
frequencyⱼ = 1 / Tⱼ
mean frequency = (frequency₁ + … + frequencyₘ₋₁) / (m − 1)
```

The mean period is the average of detected per-cycle periods pooled over all
selected intervals, not the interval duration divided by cycle count. The
Phase 1 frequency mean and range are calculated from the pooled per-cycle
frequencies.

## 5. Apply the cycle-count checks

### Phase 1

For each Phase 1 interval, the reference count `E` is the entered non-zero
expected count; when no expected count is entered, `E` is the detected baseline
count in that interval.

```text
count difference = |comparison count − E|
```

A difference of 0 or 1 passes this check. A difference of 2 or more triggers
REVIEW. Phase 1 cycle count does not itself trigger FAIL.

### Phase 2

Use the detected baseline count `cᵢ` for each selected Phase 2 baseline range to
calculate the reference mean:

```text
baseline mean count = (c₁ + c₂ + … + cᵣ) / r
baseline sample SD = sqrt(Σ(cᵢ − baseline mean count)² / (r − 1))
count difference = |comparison interval count − baseline mean count|
percent difference = 100 × count difference / |baseline mean count|
```

Apply the thresholds to **each comparison interval**:

- Difference of **20 cycles or more**: FAIL.
- Otherwise, a difference of **20% or more** of the baseline mean: REVIEW.
- Otherwise: PASS for this check.

The baseline sample standard deviation may be displayed in the report table,
but it does not determine the current Phase 2 cycle-count outcome. At least
two selected baseline ranges are required to establish the Phase 2 reference.
The Phase 2 **Expected cycles** entry does not set this assessment reference.
If the baseline mean count is zero, a zero difference passes; a non-zero
difference triggers REVIEW unless it is at least 20 cycles, which triggers
FAIL.

For displayed table deltas, `Δ = comparison value − baseline value`, and
`percent difference = 100 × Δ / |baseline value|` when the baseline value is
non-zero.

## 6. Assess mean peak-to-peak and mean period

For Phase 1, assessment calculations use the per-cycle measurements that are
within the boxplot fences. For each measurement set:

```text
IQR = Q₃ − Q₁
lower fence = Q₁ − 1.5 × IQR
upper fence = Q₃ + 1.5 × IQR
```

For each assessed phase and metric, let `xᵢ` be the baseline measurements
remaining after that Phase 1 filtering (Phase 2 uses all its measurements).
Calculate baseline mean `μB`, baseline sample standard deviation `sB`, and
comparison mean `μC`:

```text
μB = (x₁ + … + xₙ) / n
sB = sqrt(Σ(xᵢ − μB)² / (n − 1))
allowed range = [μB − 3sB, μB + 3sB]
```

The corresponding comparison values are averaged by the same per-cycle
arithmetic-mean method to obtain `μC`. If `μC` is below the lower limit or
above the upper limit, that metric fails. At least two baseline values and one
comparison value are needed for each metric. The two assessed metrics are mean
peak-to-peak and mean period; individual values are not independently checked
against the ±3 SD limit.

## 7. Resolve the overall status

The status is selected in this order:

1. If any assessed criterion fails, overall status is **FAIL**.
2. Otherwise, if required data or coverage is incomplete, overall status is
   **INCONCLUSIVE** (even if another check would trigger REVIEW).
3. Otherwise, if any Phase 1 or Phase 2 cycle-count check triggers review,
   overall status is **REVIEW**.
4. Otherwise, overall status is **PASS**.

The exported phase tables show measurements and an assessment-role description;
they do not assign independent phase-level PASS/FAIL results. Review and
inconclusive summaries list only review-triggering findings or unresolved
requirements rather than every measurement that met its limit.

## Additional reported information

Pearson correlations describe whether paired waveform values rise and fall
together. They are informational and do not affect PASS, REVIEW, or FAIL.
Phase 1 boxplots show per-cycle amplitude and period distributions; circular
markers identify values beyond the 1.5×IQR fences. Correlation scores and
boxplot outlier counts are not separate assessment criteria.

These rules describe the current report implementation and are not a substitute
for externally approved device-specific acceptance standards. Update this
reference when assessment logic or report presentation changes.

## What Pearson correlation means

For each phase, the report pairs measurements from the two recordings at the
same times. The comparison data is lined up using the selected time offset, and
is interpolated when it does not have a measurement at exactly the same time as
the baseline. Only paired measurements within the selected phase ranges and
within the recordings' overlapping time coverage are used.

Pearson correlation describes whether the two sets of paired measurements rise
and fall together:

- **+1** means they move together very consistently.
- **0** means there is no clear straight-line relationship in how they move.
- **-1** means one tends to rise when the other falls.

For example, two waveforms that reach their peaks and valleys at similar times
will usually have a positive correlation. A high score does **not** mean the
waveforms have the same size: one can move farther than the other and still
have a high score. A low score can also result when the same cycles occur at
different times, including due to capture timing. Therefore, correlation is
reported as a timing diagnostic, not as a measure of interval-level similarity
or an assessment criterion.



### Pairing samples in time

The report uses each baseline time that falls within the selected range and
the time coverage of both recordings. With a comparison time offset, it looks
up the comparison at:

```text
comparison time = baseline time - time offset
```

If that time falls between two comparison measurements, it estimates the
value by straight-line (linear) interpolation between them.

This produces pairs of values: one baseline value and one comparison value
for each matched time.
