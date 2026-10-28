"""Dialog for collecting optional metadata to attach to exported reports."""

from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QVBoxLayout,
)


_METADATA_FIELDS = (
    "Camera",
    "FPS",
    "Treatment profile",
    "Device number",
    "Client Name",
)


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

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def metadata(self):
        return {
            label: self._inputs[label].text().strip()
            for label in _METADATA_FIELDS
        }
