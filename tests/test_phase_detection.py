import csv
import tempfile
import unittest
from pathlib import Path

import numpy as np

from app.automation.phase_detector import detect_phase_ranges
from app.automation.detection_profiles import (
    TreatmentProfile,
    default_treatment_profiles,
    load_treatment_profiles,
    save_treatment_profiles,
)
from app.automation.phase_state_machine import (
    DetectionState,
    PhaseDetectionStateMachine,
    PhaseDetectionConfig,
)
from app.automation.signal_features import (
    CycleFeature,
    NoiseProfile,
    QuietInterval,
    resample_extrema_signal,
)


def _recording_with_vibration(start_s=1.0, noise_seed=4):
    sample_rate = 100.0
    times = np.arange(0.0, 10.0, 1.0 / sample_rate)
    rng = np.random.default_rng(noise_seed)
    positions = rng.normal(0.0, 0.002, times.size)

    phase1 = (times >= start_s) & (times < start_s + 2.0)
    phase1_time = times[phase1] - start_s
    positions[phase1] += 0.5 * np.sin(2.0 * np.pi * 2.0 * phase1_time)

    phase2 = (times >= start_s + 2.0) & (times < start_s + 4.0)
    phase2_time = times[phase2] - start_s - 2.0
    phase = 2.0 * np.pi * (2.0 * phase2_time + 0.4 * phase2_time**2)
    envelope = np.minimum(0.5 + 0.2 * phase2_time, 0.9) * np.exp(
        -1.6 * np.maximum(phase2_time - 1.0, 0.0)
    )
    positions[phase2] += envelope * np.sin(phase)
    return times, positions


def _add_vibration(positions, times, start_s):
    phase1 = (times >= start_s) & (times < start_s + 2.0)
    phase1_time = times[phase1] - start_s
    positions[phase1] += 0.5 * np.sin(2.0 * np.pi * 2.0 * phase1_time)

    phase2 = (times >= start_s + 2.0) & (times < start_s + 4.0)
    phase2_time = times[phase2] - start_s - 2.0
    phase = 2.0 * np.pi * (2.0 * phase2_time + 0.4 * phase2_time**2)
    envelope = np.minimum(0.5 + 0.2 * phase2_time, 0.9) * np.exp(
        -1.6 * np.maximum(phase2_time - 1.0, 0.0)
    )
    positions[phase2] += envelope * np.sin(phase)


class PhaseDetectionTests(unittest.TestCase):
    def test_auto_profile_detects_low_and_high_amplitude_baseline_files(self):
        project_root = Path(__file__).resolve().parents[1]
        expected = {
            "0.4csv.csv": ("0.4 mm", 4),
            "Baseline0.7.csv": ("0.7 mm", 6),
            "Baseline1.1.csv": ("1.1 mm", 4),
        }
        for filename, (category, expected_event_count) in expected.items():
            with self.subTest(filename=filename):
                with (project_root / "data" / "csv" / filename).open(
                    newline="", encoding="utf-8"
                ) as handle:
                    rows = list(csv.DictReader(handle))
                times = np.asarray([float(row["time_s"]) for row in rows])
                positions = np.asarray([float(row["y_px"]) for row in rows])

                result = detect_phase_ranges(times, positions)

                self.assertEqual(len(result.events), expected_event_count)
                self.assertTrue(result.candidate_regions)
                self.assertEqual(
                    result.profile_metadata["expected_amplitude_category"],
                    category,
                )
                self.assertEqual(len(result.phase_ranges), expected_event_count * 3)
                self.assertTrue(result.diagnostics)
                self.assertIn("candidate_cycles", result.diagnostics[0])
                self.assertIn("frequency_stability", result.diagnostics[0])
                self.assertIn("amplitude_stability", result.diagnostics[0])
                self.assertIn("rms_energy", result.diagnostics[0])
                self.assertIn("signal_to_noise_ratio", result.diagnostics[0])
                self.assertIn("rejection_reasons", result.diagnostics[0])

    def test_auto_profile_detects_tagged_extrema_csv(self):
        project_root = Path(__file__).resolve().parents[1]
        with (project_root / "data" / "csv" / "0.4secondrun.csv").open(
            newline="", encoding="utf-8"
        ) as handle:
            rows = list(csv.DictReader(handle))
        times = np.asarray([float(row["time_s"]) for row in rows])
        positions = np.asarray([float(row["position_px"]) for row in rows])

        uniform_times, uniform_positions = resample_extrema_signal(times, positions)
        result = detect_phase_ranges(uniform_times, uniform_positions)

        self.assertEqual(len(result.events), 4)
        self.assertEqual(len(result.phase_ranges), 12)
        self.assertEqual(
            result.profile_metadata["expected_amplitude_category"], "0.4 mm"
        )

    def test_user_selected_profile_changes_qualification_parameters(self):
        profiles = default_treatment_profiles()
        selected = next(profile for profile in profiles if profile.name == "0.4 mm")
        times, positions = _recording_with_vibration()

        result = detect_phase_ranges(
            times,
            positions,
            profile=selected,
            profiles=profiles,
        )

        self.assertEqual(
            result.profile_metadata["detection_mode"],
            "User-selected treatment profile",
        )
        self.assertEqual(result.profile_metadata["selected_profile"], "0.4 mm")
        self.assertEqual(
            result.profile_metadata["parameters_used"]["minimum_candidate_cycles"],
            selected.min_cycles,
        )
        self.assertTrue(result.events)

    def test_profile_library_round_trips_user_defined_names(self):
        profiles = default_treatment_profiles()
        custom = TreatmentProfile.from_dict(
            {
                **profiles[1].to_dict(),
                "name": "Customer Calibration",
                "expected_amplitude_category": "0.4 mm",
            }
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "profiles.json"
            save_treatment_profiles(path, [profiles[0], custom])
            loaded = load_treatment_profiles(path)

        self.assertEqual(
            [profile.name for profile in loaded],
            ["Auto Detect", "Customer Calibration"],
        )

    def test_detects_initial_and_terminal_events_in_baseline_recording(self):
        recording_path = (
            Path(__file__).resolve().parents[1]
            / "data"
            / "csv"
            / "Baseline1.1.csv"
        )
        with recording_path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        times = np.asarray([float(row["time_s"]) for row in rows])
        positions = np.asarray([float(row["y_px"]) for row in rows])

        result = detect_phase_ranges(times, positions)

        self.assertEqual(len(result.events), 4)
        self.assertEqual(
            [phase["label"] for phase in result.phase_ranges],
            ["Phase 1", "Phase 2", "Phase 3"] * 4,
        )
        self.assertAlmostEqual(result.events[0]["vibration_start_s"], 1.288, delta=0.1)
        self.assertAlmostEqual(result.events[-1]["vibration_start_s"], 7.771, delta=0.1)
        self.assertAlmostEqual(result.events[-1]["phase3_end_s"], times[-1])

    def test_first_candidate_can_qualify_at_the_recording_start(self):
        machine = PhaseDetectionStateMachine(
            NoiseProfile(
                rms=1.0,
                peak_to_peak=8.0,
                standard_deviation=1.0,
                average_frequency_hz=0.0,
            ),
            PhaseDetectionConfig(),
        )
        for index in range(5):
            start = 0.5 + index * 0.1
            machine.process_cycle(
                CycleFeature(
                    start_s=start,
                    end_s=start + 0.1,
                    frequency_hz=10.0,
                    amplitude=18.0,
                    rms=6.0,
                    periodicity_strength=1.0,
                )
            )

        self.assertEqual(machine.state, DetectionState.PHASE_1)
        self.assertEqual(machine.current_event.start_s, 0.5)

    def test_terminal_quiet_tail_finalizes_phase_three(self):
        machine = PhaseDetectionStateMachine(
            NoiseProfile(
                rms=1.0,
                peak_to_peak=4.0,
                standard_deviation=1.0,
                average_frequency_hz=0.0,
            ),
            PhaseDetectionConfig(),
        )
        cycles = [
            CycleFeature(
                start_s=1.0 + index * 0.5,
                end_s=1.5 + index * 0.5,
                frequency_hz=2.0,
                amplitude=10.0,
                rms=7.0,
                periodicity_strength=1.0,
            )
            for index in range(5)
        ]
        cycles.extend(
            CycleFeature(
                start_s=3.5 + index / 3.0,
                end_s=3.5 + (index + 1) / 3.0,
                frequency_hz=3.0,
                amplitude=10.0,
                rms=7.0,
                periodicity_strength=1.0,
            )
            for index in range(3)
        )
        for cycle in cycles:
            machine.process_cycle(cycle)

        terminal_rest = QuietInterval(start_s=4.5, end_s=4.8)
        machine.process_quiet_interval(terminal_rest)
        events = machine.finish(4.9, terminal_quiet_interval=terminal_rest)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].phase3_start_s, 4.5)
        self.assertEqual(events[0].end_s, 4.9)
        self.assertEqual(machine.state, DetectionState.PHASE_3)

    def test_detects_compatible_phase_ranges_for_report_workflow(self):
        times, positions = _recording_with_vibration()

        result = detect_phase_ranges(times, positions)

        self.assertEqual(
            [phase["label"] for phase in result.phase_ranges],
            ["Phase 1", "Phase 2", "Phase 3"],
        )
        self.assertTrue(all(phase["smooth"] is False for phase in result.phase_ranges))
        self.assertTrue(all(phase["expected_cycles"] == 0 for phase in result.phase_ranges))
        self.assertAlmostEqual(result.phase_ranges[0]["start_s"], 1.12, delta=0.15)
        self.assertGreater(
            result.phase_ranges[0]["end_s"], result.phase_ranges[0]["start_s"]
        )
        self.assertGreater(
            result.phase_ranges[1]["end_s"], result.phase_ranges[1]["start_s"]
        )
        self.assertAlmostEqual(result.phase_ranges[2]["end_s"], times[-1], places=2)
        self.assertEqual(len(result.events), 1)
        self.assertGreater(result.events[0]["phase2_start_s"], result.events[0]["vibration_start_s"])
        self.assertGreater(result.events[0]["phase3_start_s"], result.events[0]["phase2_start_s"])
        self.assertAlmostEqual(
            result.events[0]["total_duration_s"],
            result.events[0]["vibration_end_s"]
            - result.events[0]["vibration_start_s"],
        )

    def test_detects_repeated_events_without_overlapping_phase_ranges(self):
        sample_rate = 100.0
        times = np.arange(0.0, 17.0, 1.0 / sample_rate)
        rng = np.random.default_rng(4)
        positions = rng.normal(0.0, 0.002, times.size)
        _add_vibration(positions, times, 1.0)
        _add_vibration(positions, times, 9.0)

        result = detect_phase_ranges(times, positions)

        self.assertEqual(len(result.events), 2)
        self.assertEqual(len(result.candidate_regions), 2)
        self.assertEqual(
            {candidate["candidate_region"] for candidate in result.diagnostics},
            {1, 2},
        )
        self.assertEqual(
            [phase["label"] for phase in result.phase_ranges],
            ["Phase 1", "Phase 2", "Phase 3"] * 2,
        )
        self.assertEqual(
            result.phase_ranges[2]["end_s"],
            result.phase_ranges[3]["start_s"],
        )

    def test_does_not_classify_noise_as_vibration(self):
        rng = np.random.default_rng(12)
        times = np.arange(0.0, 8.0, 0.01)
        positions = rng.normal(0.0, 0.002, times.size)

        result = detect_phase_ranges(times, positions)

        self.assertEqual(result.phase_ranges, [])
        self.assertEqual(result.events, [])
        self.assertEqual(result.candidate_regions, [])


if __name__ == "__main__":
    unittest.main()
