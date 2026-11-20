"""Dialog for collecting optional metadata to attach to exported reports."""

from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from analysis.treatment_profiles import TREATMENT_PROFILES


_METADATA_FIELDS = (
    "Camera",
    "FPS",
    "Device number",
    "Client Name",
    "Date",
)
_OTHER_PROFILE = "__other__"


class ReportMetadataDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Report details")
        self._inputs = {}

        form = QFormLayout()
        for label in _METADATA_FIELDS:
            field = QLineEdit(self)
            form.addRow(label, field)
            self._inputs[label] = field

        self._treatment_profile = QComboBox(self)
        self._treatment_profile.addItem("Select treatment profile", None)
        for treatment_profile in TREATMENT_PROFILES:
            self._treatment_profile.addItem(treatment_profile, treatment_profile)
        self._treatment_profile.addItem("Other", _OTHER_PROFILE)
        self._treatment_profile.currentIndexChanged.connect(
            self._update_custom_profile_visibility
        )
        self._custom_profile_label = QLabel("Custom treatment profile", self)
        self._custom_treatment_profile = QLineEdit(self)
        self._custom_treatment_profile.setPlaceholderText(
            "Enter a treatment profile"
        )
        form.addRow("Treatment profile", self._treatment_profile)
        form.addRow(self._custom_profile_label, self._custom_treatment_profile)
        self._update_custom_profile_visibility()

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _update_custom_profile_visibility(self):
        is_other = self._treatment_profile.currentData() == _OTHER_PROFILE
        self._custom_treatment_profile.setVisible(is_other)
        self._custom_treatment_profile.setEnabled(is_other)
        self._custom_profile_label.setVisible(is_other)

    def metadata(self):
        metadata = {
            label: self._inputs[label].text().strip()
            for label in _METADATA_FIELDS
        }
        selected_profile = self._treatment_profile.currentData()
        metadata["Treatment profile"] = (
            self._custom_treatment_profile.text().strip()
            if selected_profile == _OTHER_PROFILE
            else selected_profile or ""
        )
        return metadata
