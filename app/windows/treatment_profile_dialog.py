"""Profile library editor for automatic phase detection."""

from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

try:
    from automation.detection_profiles import (
        TreatmentProfile,
        default_treatment_profiles,
    )
except ModuleNotFoundError:
    from app.automation.detection_profiles import (
        TreatmentProfile,
        default_treatment_profiles,
    )


class TreatmentProfileDialog(QDialog):
    def __init__(self, profiles, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Manage treatment profiles")
        self.resize(680, 430)
        self._profiles = list(profiles)
        self._selected_profile_index = None

        self.profile_list = QListWidget()
        self.profile_list.currentRowChanged.connect(self._select_profile)
        for profile in self._profiles:
            self.profile_list.addItem(profile.name)

        self.name_input = QLineEdit()
        self.category_input = QComboBox()
        self.category_input.setEditable(True)
        self.category_input.addItems(
            ["Automatic", "0.4 mm", "0.7 mm", "1.1 mm", "Custom"]
        )
        self.min_snr_input = self._double_input(0.1, 100.0, 0.1)
        self.min_cycles_input = QSpinBox()
        self.min_cycles_input.setRange(2, 1000)
        self.frequency_tolerance_input = self._double_input(0.01, 5.0, 0.05)
        self.amplitude_tolerance_input = self._double_input(0.01, 5.0, 0.05)
        self.periodicity_input = self._double_input(0.0, 1.0, 0.05)
        self.frequency_increase_input = self._double_input(1.01, 10.0, 0.05)
        self.transition_cycles_input = QSpinBox()
        self.transition_cycles_input.setRange(1, 1000)
        self.quiet_hysteresis_input = self._double_input(1.01, 100.0, 0.1)

        form = QFormLayout()
        form.addRow("Profile name", self.name_input)
        form.addRow("Expected amplitude category", self.category_input)
        form.addRow("Minimum signal-to-noise ratio", self.min_snr_input)
        form.addRow("Minimum consecutive cycles", self.min_cycles_input)
        form.addRow("Frequency stability tolerance (CV)", self.frequency_tolerance_input)
        form.addRow("Amplitude stability tolerance (CV)", self.amplitude_tolerance_input)
        form.addRow("Periodicity threshold", self.periodicity_input)
        form.addRow("Frequency increase ratio", self.frequency_increase_input)
        form.addRow("Sustained transition cycles", self.transition_cycles_input)
        form.addRow("Quiet hysteresis multiple", self.quiet_hysteresis_input)

        self.new_button = QPushButton("New")
        self.new_button.clicked.connect(self._new_profile)
        self.duplicate_button = QPushButton("Duplicate")
        self.duplicate_button.clicked.connect(self._duplicate_profile)
        self.delete_button = QPushButton("Delete")
        self.delete_button.clicked.connect(self._delete_profile)
        profile_actions = QHBoxLayout()
        profile_actions.addWidget(self.new_button)
        profile_actions.addWidget(self.duplicate_button)
        profile_actions.addWidget(self.delete_button)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self._accept_changes)
        buttons.rejected.connect(self.reject)

        left_layout = QVBoxLayout()
        left_layout.addWidget(QLabel("Profiles"))
        left_layout.addWidget(self.profile_list)
        left_layout.addLayout(profile_actions)
        right_layout = QVBoxLayout()
        right_layout.addLayout(form)
        right_layout.addStretch(1)
        layout = QHBoxLayout()
        layout.addLayout(left_layout, 1)
        layout.addLayout(right_layout, 2)

        outer_layout = QVBoxLayout(self)
        outer_layout.addLayout(layout)
        outer_layout.addWidget(buttons)
        if self._profiles:
            self.profile_list.setCurrentRow(0)

    @staticmethod
    def _double_input(minimum, maximum, step):
        field = QDoubleSpinBox()
        field.setDecimals(3)
        field.setRange(minimum, maximum)
        field.setSingleStep(step)
        return field

    def profiles(self):
        return list(self._profiles)

    def _commit_current_profile(self, show_error=True):
        if self._selected_profile_index is None:
            return True
        values = {
            "name": self.name_input.text().strip(),
            "expected_amplitude_category": self.category_input.currentText().strip(),
            "min_snr": self.min_snr_input.value(),
            "min_cycles": self.min_cycles_input.value(),
            "frequency_tolerance": self.frequency_tolerance_input.value(),
            "amplitude_tolerance": self.amplitude_tolerance_input.value(),
            "periodicity_threshold": self.periodicity_input.value(),
            "frequency_increase_ratio": self.frequency_increase_input.value(),
            "sustained_frequency_cycles": self.transition_cycles_input.value(),
            "quiet_hysteresis_multiple": self.quiet_hysteresis_input.value(),
        }
        try:
            profile = TreatmentProfile.from_dict(values)
            for index, existing in enumerate(self._profiles):
                if (
                    index != self._selected_profile_index
                    and existing.name.casefold() == profile.name.casefold()
                ):
                    raise ValueError(f"A profile named '{profile.name}' already exists")
            if (
                self._profiles[self._selected_profile_index].name == "Auto Detect"
                and profile.name != "Auto Detect"
            ):
                raise ValueError("The built-in Auto Detect profile cannot be renamed")
        except ValueError as exc:
            if show_error:
                QMessageBox.warning(self, "Treatment profile", str(exc))
            return False

        old_name = self._profiles[self._selected_profile_index].name
        self._profiles[self._selected_profile_index] = profile
        self.profile_list.item(self._selected_profile_index).setText(profile.name)
        if old_name == "Auto Detect":
            self.name_input.setText("Auto Detect")
        return True

    def _select_profile(self, row):
        if self._selected_profile_index is not None and row != self._selected_profile_index:
            if not self._commit_current_profile():
                self.profile_list.blockSignals(True)
                self.profile_list.setCurrentRow(self._selected_profile_index)
                self.profile_list.blockSignals(False)
                return
        self._selected_profile_index = row if row >= 0 else None
        if self._selected_profile_index is None:
            return
        profile = self._profiles[row]
        self.name_input.setText(profile.name)
        self.category_input.setCurrentText(profile.expected_amplitude_category)
        self.min_snr_input.setValue(profile.min_snr)
        self.min_cycles_input.setValue(profile.min_cycles)
        self.frequency_tolerance_input.setValue(profile.frequency_tolerance)
        self.amplitude_tolerance_input.setValue(profile.amplitude_tolerance)
        self.periodicity_input.setValue(profile.periodicity_threshold)
        self.frequency_increase_input.setValue(profile.frequency_increase_ratio)
        self.transition_cycles_input.setValue(profile.sustained_frequency_cycles)
        self.quiet_hysteresis_input.setValue(profile.quiet_hysteresis_multiple)

        is_auto = profile.name == "Auto Detect"
        self.name_input.setEnabled(not is_auto)
        for field in (
            self.category_input,
            self.min_snr_input,
            self.min_cycles_input,
            self.frequency_tolerance_input,
            self.amplitude_tolerance_input,
            self.periodicity_input,
            self.frequency_increase_input,
            self.transition_cycles_input,
            self.quiet_hysteresis_input,
        ):
            field.setEnabled(not is_auto)
        self.delete_button.setEnabled(not is_auto)

    def _new_profile(self):
        if not self._commit_current_profile():
            return
        base = next(
            (
                profile
                for profile in default_treatment_profiles()
                if profile.expected_amplitude_category == "0.4 mm"
            )
        )
        profile = TreatmentProfile.from_dict(
            {
                **base.to_dict(),
                "name": "New treatment profile",
            }
        )
        self._profiles.append(profile)
        self.profile_list.addItem(profile.name)
        self.profile_list.setCurrentRow(len(self._profiles) - 1)
        self.name_input.setFocus()
        self.name_input.selectAll()

    def _duplicate_profile(self):
        if not self._commit_current_profile():
            return
        if self._selected_profile_index is None:
            return
        source = self._profiles[self._selected_profile_index]
        name = f"{source.name} copy"
        existing_names = {profile.name.casefold() for profile in self._profiles}
        suffix = 2
        while name.casefold() in existing_names:
            name = f"{source.name} copy {suffix}"
            suffix += 1
        duplicate = TreatmentProfile.from_dict({**source.to_dict(), "name": name})
        self._profiles.append(duplicate)
        self.profile_list.addItem(duplicate.name)
        self.profile_list.setCurrentRow(len(self._profiles) - 1)
        self.name_input.setFocus()
        self.name_input.selectAll()

    def _delete_profile(self):
        if self._selected_profile_index is None:
            return
        if self._profiles[self._selected_profile_index].name == "Auto Detect":
            return
        row = self._selected_profile_index
        del self._profiles[row]
        self._selected_profile_index = None
        self.profile_list.takeItem(row)
        self.profile_list.setCurrentRow(min(row, len(self._profiles) - 1))

    def _accept_changes(self):
        if not self._commit_current_profile():
            return
        if not any(profile.name == "Auto Detect" for profile in self._profiles):
            QMessageBox.warning(
                self,
                "Treatment profile",
                "The built-in Auto Detect profile cannot be removed.",
            )
            return
        self.accept()
