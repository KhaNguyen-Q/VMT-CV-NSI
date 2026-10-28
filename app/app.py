import sys

if __name__ == "__main__":
    if sys.argv[1:2] == ["--upload-report"]:
        from graph.upload_report import main

        raise SystemExit(main(sys.argv[2:]))

    from PyQt5.QtWidgets import QApplication
    from windows.app_window import AppWindow

    app = QApplication(sys.argv)
    window = AppWindow()
    window.show()
    sys.exit(app.exec_())
