import sys

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QMainWindow, QTreeWidgetItem

from mode_writing import WritingModeWidget
from project_manager import ProjectManager


def main():
    """Run the interactive rename probe outside unittest discovery."""
    app = QApplication.instance() or QApplication(sys.argv)
    window = QMainWindow()
    project_manager = ProjectManager()
    writing_mode = WritingModeWidget(project_manager)
    window.setCentralWidget(writing_mode)
    window.show()

    writing_mode.wpm.writing_root_path = "dummy"
    item = QTreeWidgetItem(["old"])
    item.setData(0, Qt.ItemDataRole.UserRole, "old.txt")
    item.setData(0, Qt.ItemDataRole.UserRole + 1, False)
    writing_mode.binder_tree.addTopLevelItem(item)
    writing_mode.binder_tree.setCurrentItem(item)

    def do_edit():
        print("do_edit")
        writing_mode.start_rename_item(item)

    def finish_edit():
        print("finish_edit")
        editor = app.focusWidget()
        print("editor:", type(editor))
        if editor:
            editor.setText("new")
            event = QKeyEvent(
                QKeyEvent.Type.KeyPress,
                Qt.Key.Key_Return,
                Qt.KeyboardModifier.NoModifier,
            )
            app.sendEvent(editor, event)

    def stop():
        sys.stdout.flush()
        app.quit()

    QTimer.singleShot(100, do_edit)
    QTimer.singleShot(500, finish_edit)
    QTimer.singleShot(1000, stop)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
