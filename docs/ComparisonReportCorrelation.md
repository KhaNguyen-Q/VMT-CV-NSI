# Understanding the comparison report's correlation scores

The comparison report uses correlation to summarize how similarly the baseline
and comparison waveforms move over time. It is a measure of shared movement,
not a pass/fail score.

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
have a high score. Correlation also does not by itself say whether amplitudes,
periods, or other measurements meet a target; those results are reported
separately.

## How the overall score is formed

The report combines phase-level correlations into one overall score. When
multiple selected ranges have the same recognized phase label, their paired
measurements are combined for that phase's correlation.

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

## Formulas used

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
