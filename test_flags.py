import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QTreeWidget, QTreeWidgetItem


def main():
    """Run the manual tree-flag smoke probe outside unittest discovery."""
    app = QApplication.instance() or QApplication(sys.argv)
    tree = QTreeWidget()
    item = QTreeWidgetItem(["test"])
    tree.addTopLevelItem(item)

    def on_changed(*_args):
        print("CHANGED!")

    tree.itemChanged.connect(on_changed)
    item.setFlags(Qt.ItemFlag.ItemIsEditable)
    print("done")
    return app, tree


if __name__ == "__main__":
    main()
