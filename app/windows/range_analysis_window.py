"""User interface for selecting range CSVs and running statistical analysis."""

import os

from PyQt5.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QComboBox,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from PyQt5.QtCore import Qt

from analysis.loader import load_range_csvs
from analysis.runner import run_all_analyses


TREATMENT_PROFILES = (
    "Treatment profile 1",
    "Treatment profile 2",
    "Treatment profile 3",
)


class RangeAnalysisWindow(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlag(Qt.Window, True)
        self.setWindowTitle("Range CSV Statistical Analysis")
        self.resize(850, 600)
        self._csv_paths = []
        self._output_dir = ""

        self.file_list = QListWidget(self)
        self.treatment_profile_combo = QComboBox(self)
        self.treatment_profile_combo.addItem("Select treatment profile", None)
        for treatment_profile in TREATMENT_PROFILES:
            self.treatment_profile_combo.addItem(
                treatment_profile, treatment_profile
            )
        self.output_label = QLabel("Output folder: not selected", self)
        self.status_label = QLabel(
            "Select exported range CSV files or a folder containing them.", self
        )

        add_files_button = QPushButton("Add CSV files…", self)
        add_files_button.clicked.connect(self._add_files)
        add_folder_button = QPushButton("Add folder…", self)
        add_folder_button.clicked.connect(self._add_folder)
        clear_button = QPushButton("Clear files", self)
        clear_button.clicked.connect(self._clear_files)
        select_output_button = QPushButton("Choose output folder…", self)
        select_output_button.clicked.connect(self._choose_output)
        run_button = QPushButton("Run all analyses", self)
        run_button.clicked.connect(self._run_analysis)

        input_controls = QHBoxLayout()
        input_controls.addWidget(add_files_button)
        input_controls.addWidget(add_folder_button)
        input_controls.addWidget(clear_button)

        treatment_profile_controls = QHBoxLayout()
        treatment_profile_controls.addWidget(QLabel("Treatment profile:", self))
        treatment_profile_controls.addWidget(self.treatment_profile_combo)

        layout = QVBoxLayout(self)
        layout.addLayout(treatment_profile_controls)
        layout.addWidget(QLabel("Input CSV files:", self))
        layout.addLayout(input_controls)
        layout.addWidget(self.file_list)
        layout.addWidget(select_output_button)
        layout.addWidget(self.output_label)
        layout.addWidget(run_button)
        layout.addWidget(self.status_label)

    def _add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select exported range CSV files",
            "",
            "CSV files (*.csv)",
        )
        self._append_paths(paths)

    def _add_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select folder containing exported range CSV files"
        )
        if folder:
            try:
                paths = [
                    os.path.join(folder, name)
                    for name in sorted(os.listdir(folder))
                    if name.lower().endswith(".csv")
                    and os.path.isfile(os.path.join(folder, name))
                ]
            except OSError as exc:
                QMessageBox.warning(self, "Select folder", str(exc))
                return
            self._append_paths(paths)

    def _append_paths(self, paths):
        for path in paths:
            normalized = os.path.normcase(os.path.abspath(path))
            if normalized not in {
                os.path.normcase(os.path.abspath(existing))
                for existing in self._csv_paths
            }:
                self._csv_paths.append(path)
                self.file_list.addItem(path)

    def _clear_files(self):
        self._csv_paths.clear()
        self.file_list.clear()
        self.status_label.setText("Input file list cleared.")

    def _choose_output(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Choose analysis output folder", self._output_dir
        )
        if folder:
            self._output_dir = folder
            self.output_label.setText(f"Output folder: {folder}")

    def _run_analysis(self):
        if not self._csv_paths:
            QMessageBox.information(self, "Analysis", "Add at least one CSV file.")
            return
        treatment_profile = self.treatment_profile_combo.currentData()
        if treatment_profile is None:
            QMessageBox.information(
                self, "Analysis", "Select a treatment profile before running analysis."
            )
            return
        if not self._output_dir:
            QMessageBox.information(
                self, "Analysis", "Choose an output folder before running analysis."
            )
            return

        run_dir = os.path.join(self._output_dir, f"{treatment_profile} output")
        if os.path.exists(run_dir):
            QMessageBox.information(
                self,
                "Analysis folder exists",
                "Choose a new folder name so existing analysis results are not overwritten.",
            )
            return

        try:
            master_df, file_report = load_range_csvs(self._csv_paths)
            outputs = run_all_analyses(master_df, run_dir, file_report=file_report)
        except (OSError, ValueError, UnicodeError) as exc:
            QMessageBox.warning(self, "Analysis failed", str(exc))
            self.status_label.setText("Analysis failed.")
            return

        loaded_files = int(
            (file_report["Summary rows loaded"] > 0).sum()
        )
        self.status_label.setText(
            f"Loaded {int(file_report['Summary rows loaded'].sum())} event summaries "
            f"and {int(file_report['Cycle-detail rows loaded'].sum())} cycle details "
            f"from {loaded_files} files. "
            f"Saved results to {run_dir}"
        )
        QMessageBox.information(
            self,
            "Analysis complete",
            f"Saved all three analyses:\n{run_dir}\n\n"
            f"CSV tables: {len(outputs['csv_files'])}\n"
            f"Visualizations: {len(outputs['plots'])}",
        )
