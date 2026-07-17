import sys
from PyQt6.QtWidgets import QApplication, QMainWindow, QTreeWidgetItem
from PyQt6.QtCore import Qt, QTimer
from project_manager import ProjectManager
from mode_writing import WritingModeWidget

app = QApplication(sys.argv)
mw = QMainWindow()
pm = ProjectManager()
wm = WritingModeWidget(pm)
mw.setCentralWidget(wm)
mw.show()

wm.wpm.writing_root_path = 'dummy'
item = QTreeWidgetItem(['old'])
item.setData(0, Qt.ItemDataRole.UserRole, 'old.txt')
item.setData(0, Qt.ItemDataRole.UserRole + 1, False)
wm.binder_tree.addTopLevelItem(item)
wm.binder_tree.setCurrentItem(item)

def do_edit():
    print('do_edit')
    wm.start_rename_item(item)

def finish_edit():
    print('finish_edit')
    # Use focusWidget to simulate user typing and pressing enter
    editor = app.focusWidget()
    print('editor:', type(editor))
    if editor:
        editor.setText('new')
        # Simulate Enter key press
        from PyQt6.QtGui import QKeyEvent
        event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
        app.sendEvent(editor, event)

def kill():
    sys.stdout.flush()
    app.quit()

QTimer.singleShot(100, do_edit)
QTimer.singleShot(500, finish_edit)
QTimer.singleShot(1000, kill)

app.exec()
