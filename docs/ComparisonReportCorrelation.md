# Understanding the comparison report's correlation scores

The comparison report uses correlation to summarize how similarly the baseline
and comparison waveforms move over time. It measures shared movement; it does
not replace checks of cycle count, amplitude, and period.

## Device assessment in exported reports

The exported internal and client reports include a baseline-based device
assessment. It uses the selected phase offset to align the comparison for
waveform calculations; the offset is not itself an acceptance requirement.

The assessment uses the selected **Phase 1** and **Phase 2** ranges. **Phase 3**
and **Other** ranges remain in the report but do not affect the assessment.
Phase 1 cycle counts are compared with the non-zero **Phase 1 expected cycles**
value, or with the detected cycle count in that baseline range when the value is
zero. A difference of 0 or 1 cycle does not require review; a difference of 2
or more requires review. The app detects
baseline extrema from the tracking CSV; loading a tagged baseline peak-to-peak
CSV supplies those extrema instead.

Phase 2 comparison cycle counts are evaluated against the mean and sample
standard deviation of the detected cycle counts in the selected Phase 2
baseline ranges. A difference of at least 20 cycles from the baseline mean
fails. A smaller count more than 2 standard deviations from the mean can
require review. At least two selected baseline ranges are needed to calculate
the standard deviation. If the standard deviation is zero, the comparison
count must equal the baseline mean to avoid review. The **Expected cycles**
entry is not used for the Phase 2 assessment; it can still determine the
smoothing applied to a Phase 2 range.

## Device assessment logic

For each phase, the mean comparison peak-to-peak amplitude and mean period are
checked against the baseline measurements. The allowed interval
for each metric is the baseline mean plus or minus **3 sample standard
deviations**. At least two baseline measurements are required to calculate a
standard deviation.

Waveform highlights, including sustained position differences and unusually
different individual cycle amplitudes, are diagnostic only. They do not trigger
PASS, REVIEW, or FAIL and are not shown as assessment rows in exported phase
tables. The app's **Amplitude sensitivity (σ)** control affects the visual
cycle-amplitude highlights only.

Pearson correlations are timing-sensitive diagnostics only. They are reported
for reference but do not affect PASS, REVIEW, or FAIL. This separates
sample-by-sample rise/fall alignment from the interval measurements used for
the device assessment.

The overall status is determined as follows:

- **PASS** — all assessed interval measurements meet their criteria, with no
  review-level cycle-count findings.
- **REVIEW** — at least one Phase 1/Phase 2 cycle-count deviation needs review,
  and no assessed criterion fails.
- **FAIL** — a Phase 2 cycle-count difference is at least 20 cycles, or a
  mean-amplitude or mean-period criterion fails.
- **INCONCLUSIVE** — there is no definite failure, but required data is missing
  or insufficient to assess every required measurement. For example, the report
  needs a comparison series and overlapping coverage. When Phase 2 is assessed,
  it also needs at least two selected Phase 2 baseline ranges for the cycle-count
  SD. Each assessed phase also needs at least two baseline amplitude and period
  measurements.

When both review-level results and unresolved required data exist without a
definite failure, the status is **INCONCLUSIVE**. The internal report puts
failure or review reasons near the top of its summary and retains the
measurement tables and numeric supporting data.

The client report opens with a status-specific quality assurance summary.
**PASS** says the device is working as intended; **REVIEW** says the results
may benefit from additional review; **FAIL** describes the results as
non-conforming; and **INCONCLUSIVE** explains that more information is needed.
The internal report's PASS assessment summary uses one concise reassurance
sentence rather than listing every passing measurement. REVIEW lists only
review-triggering findings, and FAIL lists only criteria that failed.
Both report versions show phase-specific measurement tables. A measurement
boxplot is shown for Phase 1 only; Phase 2 and Phase 3 boxplots are omitted as
redundant. Waveform graphs are shown for phases other than Phase 3. An appendix
in both reports explains the interval, amplitude, period, standard deviation,
cycle count, and assessment rules in more detail.

The report's time values are elapsed seconds from the beginning of each
recording, not calendar date/time. Selected start/end marker times and duration
are printed to six decimal places. The comparison interval is represented on
its own recording timeline after applying the selected time offset.

Peak-to-peak amplitude is the absolute position difference between consecutive
opposite extrema. The report averages accepted amplitudes; when more than two
are present, values below half the median of the other amplitudes are excluded
as likely detection artifacts. The maximum is the largest individual cycle
amplitude remaining after that filter, pooled across selected intervals, not
an average. Phase 2's table shows this maximum and omits mean amplitude for
readability, but the existing overall assessment still uses mean amplitude
and period.

Cycle period is the time difference between consecutive detected top extrema.
The mean period is the average of those individual differences, not the
selected interval duration divided by the cycle count. The app's reported cycle
count is the smaller of the number of detected tops and bottoms inside the
selected interval. Phase 1 frequency is the average of per-cycle reciprocal
periods; the table also gives the minimum-to-maximum per-cycle frequency range.

Standard deviation is the sample standard deviation: the square root of the
sum of squared differences from the mean divided by `n - 1`. It is calculated
only when at least two measurements are available. Phase 1 boxplots show
per-cycle amplitude and period distributions. Their circular outlier markers
are values beyond 1.5 times the interquartile range from the quartiles, and the
report's boxplot-outlier counts use that same rule. Boxplots and waveform
highlights are descriptive diagnostics only; neither determines the overall
assessment. Phase tables show whether each phase contributes to the overall
assessment, but do not assign independent phase-level PASS/FAIL results.
When the report status is REVIEW or INCONCLUSIVE, the summary lists only
review-triggering findings or unresolved requirements—not measurements that
were within their limits.

These rules are the app's current report criteria, not a substitute for
externally approved device-specific acceptance standards. Update this section
whenever the implementation's assessment or report presentation changes.

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

## How the overall score is formed

The report combines phase-level correlations into one overall diagnostic score. When
multiple selected ranges have the same recognized phase label, their paired
measurements are combined for that phase's correlation. This weighted overall
score is informational and is not used by the device assessment.

Before combining phases, the report converts each correlation to a Fisher-z
value. This is a mathematical rescaling that makes correlations behave more
suitably when they are averaged, especially near -1 and +1. It then takes a
weighted average using the report's fixed phase weights and converts the result
back to the familiar -1 to +1 correlation scale.

The phase weights are:

| Phase | Weight in the overall score |
|-------|------------------------------|
| Phase I | 3 |
| Phase II | 2 |
| Phase III | 0 |

So Phase I has more influence than Phase II, and Phase III is left out of the
overall score. The weights are phase priorities; they are not based on how long
a phase lasts or how many samples it contains. Other or unrecognized phase
labels do not contribute to the overall score.


If there are not enough paired measurements, the values do not vary, or no
positive-weight phase can be scored, a correlation may be unavailable.


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

### Pearson correlation for a phase

First, the report calculates the average baseline value and the average
comparison value.

Then it calculates Pearson correlation:

```text
correlation =
  sum((baseline value - average baseline)
      * (comparison value - average comparison))
  / sqrt(
      sum((baseline value - average baseline)^2)
      * sum((comparison value - average comparison)^2)
    )
```

Here, each sum is taken across all matched times in the phase. The numerator
gets larger when the two recordings are above or below their own averages at
the same times. The denominator scales the result so it is between -1 and +1.
If there are fewer than two pairs or either set of values does not vary, the
report leaves the correlation unavailable.

### Fisher-z weighted overall score

For each eligible phase, the report converts its correlation to Fisher-z:

```text
phase Fisher-z = 0.5 * ln((1 + phase correlation) / (1 - phase correlation))
```

The overall score is the weighted average of those z values, converted back to
the correlation scale:

```text
overall Fisher-z =
  sum(phase weight * phase Fisher-z) / sum(phase weights)

overall correlation = tanh(overall Fisher-z)
```

The weights are 3 for Phase I, 2 for Phase II, and 0 for Phase III.
Correlations of exactly -1 or +1 are moved an extremely small amount inward
before the Fisher-z step, because that transformation is infinite at those
endpoints. A zero-weight phase and a phase with an unavailable correlation are
excluded from both sums.
