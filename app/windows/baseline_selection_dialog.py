"""Collect comparison input and statistical baseline settings for report export."""

import os

from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

try:
    from analysis.baseline_registry import (
        get_profile_baselines,
        load_baseline_registry,
        register_baseline,
    )
    from analysis.comparator_config import load_comparator_config
    from analysis.comparator import (
        available_comparison_profiles,
        detect_comparison_csv_format,
    )
    from analysis.treatment_profiles import (
        TREATMENT_PROFILES,
        canonical_treatment_profile,
    )
except ModuleNotFoundError:
    from app.analysis.baseline_registry import (
        get_profile_baselines,
        load_baseline_registry,
        register_baseline,
    )
    from app.analysis.comparator_config import load_comparator_config
    from app.analysis.comparator import (
        available_comparison_profiles,
        detect_comparison_csv_format,
    )
    from app.analysis.treatment_profiles import (
        TREATMENT_PROFILES,
        canonical_treatment_profile,
    )


class BaselineSelectionDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Prepare comparison report")
        self.resize(760, 300)
        self._external_config_path = None

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Choose a raw tracking CSV or a labeled Export ranges CSV, "
                "its treatment profile, and the aggregated baseline.",
                self,
            )
        )

        form = QFormLayout()
        self.comparison_path_input = QLineEdit(self)
        self.comparison_path_input.setPlaceholderText(
            "Raw tracking CSV or labeled Export ranges CSV"
        )
        comparison_browse = QPushButton("Browse…", self)
        comparison_browse.clicked.connect(self._browse_comparison)
        comparison_row = QHBoxLayout()
        comparison_row.addWidget(self.comparison_path_input)
        comparison_row.addWidget(comparison_browse)
        form.addRow("Comparison CSV", comparison_row)

        self.profile_combo = QComboBox(self)
        self.profile_combo.addItem("Select treatment profile", None)
        profiles = list(TREATMENT_PROFILES)
        try:
            registered_profiles = load_baseline_registry()[
                "baseline_references"
            ]
        except ValueError:
            registered_profiles = {}
        for profile in sorted(registered_profiles):
            if profile not in profiles:
                profiles.append(profile)
        for profile in profiles:
            self.profile_combo.addItem(profile, profile)
        self.profile_combo.currentIndexChanged.connect(
            self._load_registered_baselines
        )
        form.addRow("Treatment profile", self.profile_combo)

        self.baseline_combo = QComboBox(self)
        form.addRow("Registered baseline", self.baseline_combo)

        baseline_controls = QHBoxLayout()
        browse_config = QPushButton("Browse for comparator_config.json…", self)
        browse_config.clicked.connect(self._browse_config)
        baseline_controls.addWidget(browse_config)
        baseline_controls.addStretch(1)
        form.addRow("Or choose a config", baseline_controls)

        self.range_method_combo = QComboBox(self)
        self.range_method_combo.addItem("Use config default (P5-P95)", "p5_p95")
        self.range_method_combo.addItem("Mean +/- 2 SD", "2sd")
        self.range_method_combo.addItem("Mean +/- 3 SD", "3sd")
        self.range_method_combo.addItem("IQR fences", "iqr")
        form.addRow("Range selection", self.range_method_combo)

        self.set_default_check = QCheckBox(
            "Set this baseline as the default for this treatment profile",
            self,
        )
        form.addRow("", self.set_default_check)
        layout.addLayout(form)

        self.status_label = QLabel(self)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            parent=self,
        )
        self.buttons.accepted.connect(self._accept_selection)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._load_registered_baselines()

    def _browse_comparison(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select comparison tracking CSV",
            self.comparison_path_input.text().strip(),
            "CSV files (*.csv);;All files (*)",
        )
        if path:
            self.comparison_path_input.setText(path)
            try:
                input_format = detect_comparison_csv_format(path)
                if input_format == "range_export":
                    profiles = available_comparison_profiles(path)
                    for profile in profiles:
                        if self.profile_combo.findData(profile) < 0:
                            self.profile_combo.addItem(profile, profile)
                    selected_profile = self.profile_combo.currentData()
                    if len(profiles) == 1:
                        index = self.profile_combo.findData(profiles[0])
                        self.profile_combo.setCurrentIndex(index)
                    elif selected_profile not in profiles:
                        self.profile_combo.setCurrentIndex(0)
                    self.status_label.setText(
                        f"Detected Export ranges CSV with {len(profiles)} "
                        "treatment profile(s)."
                    )
                else:
                    self.status_label.setText(
                        "Detected raw tracking CSV; phase ranges are handled "
                        "separately in the comparison window."
                    )
            except (OSError, ValueError, UnicodeError) as exc:
                self.status_label.setText(
                    f"Could not validate comparison CSV: {exc}"
                )

    def _load_registered_baselines(self, *_args):
        profile = self.profile_combo.currentData()
        self.baseline_combo.clear()
        if not profile:
            self.baseline_combo.addItem("Select a treatment profile", None)
            self.status_label.setText("Select a treatment profile.")
            return

        try:
            default_path, available = get_profile_baselines(profile)
        except ValueError as exc:
            self.baseline_combo.addItem("Registry unavailable", None)
            self.status_label.setText(str(exc))
            return
        if not available:
            self.baseline_combo.addItem("No registered baseline", None)
            self.status_label.setText(
                "No baseline is registered for this profile. Browse to a "
                "comparator_config.json from a completed analysis run."
            )
        else:
            self.status_label.setText(
                f"{len(available)} registered baseline reference(s) found."
            )
        for entry in available:
            name = entry.get("name") or os.path.basename(
                os.path.dirname(entry["config_path"])
            )
            self.baseline_combo.addItem(name, entry["config_path"])
        if self._external_config_path:
            loaded = load_comparator_config(self._external_config_path)
            if any(
                canonical_treatment_profile(name)
                == canonical_treatment_profile(profile)
                for name in loaded["treatment_profiles"]
            ):
                index = self.baseline_combo.findData(self._external_config_path)
                if index < 0:
                    self.baseline_combo.addItem(
                        os.path.basename(
                            os.path.dirname(self._external_config_path)
                        ),
                        self._external_config_path,
                    )
        if default_path:
            index = self.baseline_combo.findData(default_path)
            if index >= 0:
                self.baseline_combo.setCurrentIndex(index)
        if self._external_config_path:
            index = self.baseline_combo.findData(self._external_config_path)
            if index >= 0:
                self.baseline_combo.setCurrentIndex(index)

    def _browse_config(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select comparator configuration",
            "",
            "Comparator JSON (comparator_config.json);;JSON files (*.json);;All files (*)",
        )
        if not path:
            return
        try:
            loaded = load_comparator_config(path)
        except (OSError, ValueError, KeyError) as exc:
            self.status_label.setText(str(exc))
            return

        profile = self.profile_combo.currentData()
        configured_profiles = list(loaded["treatment_profiles"])
        canonical_profiles = [
            canonical_treatment_profile(name) for name in configured_profiles
        ]
        if profile and canonical_treatment_profile(profile) in canonical_profiles:
            selected_profile = canonical_treatment_profile(profile)
        elif len(canonical_profiles) == 1:
            selected_profile = canonical_profiles[0]
        else:
            self._external_config_path = os.path.abspath(path)
            self.status_label.setText(
                "Choose a profile that exists in this comparator config."
            )
            for configured_profile in canonical_profiles:
                if self.profile_combo.findData(configured_profile) < 0:
                    self.profile_combo.addItem(
                        configured_profile,
                        configured_profile,
                    )
            return

        index = self.profile_combo.findData(selected_profile)
        if index < 0:
            self.profile_combo.addItem(selected_profile, selected_profile)
            index = self.profile_combo.count() - 1
        self.profile_combo.setCurrentIndex(index)

        config_path = os.path.abspath(path)
        self._external_config_path = config_path
        self._load_registered_baselines()
        index = self.baseline_combo.findData(config_path)
        if index < 0:
            self.baseline_combo.addItem(
                os.path.basename(os.path.dirname(path)),
                config_path,
            )
            index = self.baseline_combo.count() - 1
        self.baseline_combo.setCurrentIndex(index)
        self.status_label.setText("Comparator configuration selected.")

    def _accept_selection(self):
        if not self.comparison_path_input.text().strip():
            self.status_label.setText("Select a comparison CSV to continue.")
            return
        if not self.profile_combo.currentData():
            self.status_label.setText("Select a treatment profile to continue.")
            return
        if not self.selected_config_path():
            self.status_label.setText(
                "Select a registered baseline or browse for a comparator configuration."
            )
            return
        self.accept()

    def selected_config_path(self):
        path = self.baseline_combo.currentData()
        return os.path.abspath(path) if path else None

    def selection(self):
        profile = canonical_treatment_profile(self.profile_combo.currentData())
        config_path = self.selected_config_path()
        if self.set_default_check.isChecked():
            register_baseline(
                profile,
                config_path,
                run_name="Custom baseline",
                set_default=True,
            )
        return {
            "comparison_csv": os.path.abspath(
                self.comparison_path_input.text().strip()
            ),
            "comparison_format": detect_comparison_csv_format(
                self.comparison_path_input.text().strip()
            ),
            "config_path": config_path,
            "range_selection_method": self.range_method_combo.currentData(),
            "set_default": self.set_default_check.isChecked(),
            "treatment_profile": profile,
        }
