import sys
from PyQt6.QtWidgets import QApplication, QTreeWidget, QTreeWidgetItem
from PyQt6.QtCore import Qt

app = QApplication(sys.argv)
tree = QTreeWidget()
item = QTreeWidgetItem(['test'])
tree.addTopLevelItem(item)

def on_changed(*args):
    print('CHANGED!')

tree.itemChanged.connect(on_changed)
item.setFlags(Qt.ItemFlag.ItemIsEditable)
print('done')
