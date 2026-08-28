import copy
import os
import re
import unicodedata
from contextlib import nullcontext

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QSplitter, QTreeWidget, QTreeWidgetItem, QTextEdit, QLabel,
    QComboBox, QToolButton, QFrame, QMenu, QMessageBox,
    QLineEdit, QDialog, QListWidget, QListWidgetItem, QAbstractItemView,
    QToolBar, QSizePolicy, QTextBrowser, QTabWidget, QFileDialog
)
from PyQt6.QtGui import QAction, QShortcut, QKeySequence, QPixmap, QPainter, QIcon, QFont
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QThread
from PyQt6 import sip

from writing_backup import HistoryViewerDialog
from binder_order import (
    canonical_manuscript_children,
    canonical_root_children,
    canonical_root_storage_name,
)
from project_paths import LocalProjectPathError, normalize_local_entry_name


class WritingTreeMixin:
    FIXED_ROOT_NODES = (
        ("📚 원고", "메인/원고"),
        ("👤 캐릭터", "메인/캐릭터"),
        ("📖 설정집", "메인/설정집"),
        ("📝 메모장", "메인/메모장"),
        ("🗺️ 스토리 플롯", "메인/스토리 플롯"),
        ("🌊 흐름 정리", "메인/흐름정리"),
        ("🔍 복선", "메인/복선"),
        ("📌 장소", "메인/장소"),
        ("🗑️ 휴지통", "메인/휴지통"),
    )

    def _binder_project_root(self):
        root = getattr(self.wpm, "writing_root_path", None)
        return os.path.dirname(root) if root else None

    def _create_binder_item(self, parent_rel_path, base_name, is_folder):
        """Create one binder item through the journalled UUID transaction."""
        from project_creation_v1 import create_item_at_path

        project_root = self._binder_project_root()
        if not project_root:
            return None
        identity = create_item_at_path(
            project_root, parent_rel_path, base_name, is_folder
        )
        return identity["nodes"][-1]["legacy_path"].rsplit("/", 1)[-1]

    def _create_binder_volume(self):
        """Create the next 권 and its 25 화 as one journalled transaction."""
        from binder_order import MANUSCRIPT_ROOT_PATH
        from project_creation_v1 import create_volume

        project_root = self._binder_project_root()
        if not project_root:
            return None
        identity = create_volume(project_root)

        prefix = f"{MANUSCRIPT_ROOT_PATH}/"
        highest = None
        for node in identity["nodes"]:
            path = node["legacy_path"]
            if node["kind"] != "folder" or not path.startswith(prefix):
                continue
            name = path[len(prefix):]
            if "/" in name or not name.endswith("권") or not name[:-1].isdigit():
                continue
            if highest is None or int(name[:-1]) > int(highest[:-1]):
                highest = name
        return highest

    def _show_temporary_invalid_name_message(self):
        message_box = QMessageBox(self)
        message_box.setWindowTitle("이름 변경")
        message_box.setIcon(QMessageBox.Icon.Warning)
        message_box.setText("지원하지 않는 파일명 입니다.")
        message_box.setStandardButtons(QMessageBox.StandardButton.Ok)
        message_box.setModal(False)
        message_box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        message_box.show()

        active_boxes = getattr(self, "_temporary_name_message_boxes", None)
        if active_boxes is None:
            active_boxes = []
            self._temporary_name_message_boxes = active_boxes
        active_boxes.append(message_box)

        def close_message():
            try:
                message_box.close()
            except RuntimeError:
                pass
            if message_box in active_boxes:
                active_boxes.remove(message_box)

        QTimer.singleShot(2000, close_message)
        return message_box

    def _finish_item_name_edit(self, item, is_folder):
        item_flags = (
            Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsDragEnabled
            | Qt.ItemFlag.ItemIsEnabled
        )
        if is_folder:
            item_flags |= Qt.ItemFlag.ItemIsDropEnabled
        try:
            previous_signal_state = self.binder_tree.blockSignals(True)
        except (AttributeError, RuntimeError):
            previous_signal_state = None
        try:
            item.setFlags(item_flags)
        finally:
            if previous_signal_state is not None:
                try:
                    self.binder_tree.blockSignals(previous_signal_state)
                except RuntimeError:
                    pass

    @classmethod
    def _normalize_fixed_root_order(cls, root_order):
        normalized = []
        seen = set()
        for name in canonical_root_children(root_order):
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(name)
        return normalized

    def _apply_saved_root_order(self, root_order):
        """Apply logical 메인 child order while keeping fixed root invariants."""
        preferred = {
            canonical_root_storage_name(name).casefold(): index
            for index, name in enumerate(root_order or [])
        }
        items = [
            self.binder_tree.takeTopLevelItem(0)
            for _ in range(self.binder_tree.topLevelItemCount())
        ]

        def sort_key(index_and_item):
            original_index, item = index_and_item
            rel_path = str(
                item.data(0, Qt.ItemDataRole.UserRole) or ""
            ).replace("\\", "/")
            if rel_path == "메인/원고":
                return (-1, 0, original_index)
            if rel_path == "메인/휴지통":
                return (2, 0, original_index)
            names = {
                canonical_root_storage_name(item.text(0)).casefold(),
                canonical_root_storage_name(
                    os.path.basename(rel_path)
                ).casefold(),
            }
            positions = [preferred[name] for name in names if name in preferred]
            if positions:
                return (0, min(positions), original_index)
            return (1, original_index, original_index)

        for _index, item in sorted(enumerate(items), key=sort_key):
            self.binder_tree.addTopLevelItem(item)

    def _schedule_remote_tree_refresh(self):
        """Reload the binder only after every inline editor is safely closed."""
        self._remote_tree_refresh_pending = True
        if getattr(self, "_remote_tree_refresh_scheduled", False):
            return
        self._remote_tree_refresh_scheduled = True
        QTimer.singleShot(0, self._flush_remote_tree_refresh)

    def _flush_remote_tree_refresh(self):
        self._remote_tree_refresh_scheduled = False
        if not getattr(self, "_remote_tree_refresh_pending", False):
            return
        if getattr(self, "_tree_item_creation_active", False):
            return
        try:
            from PyQt6.QtWidgets import QAbstractItemView
            if self.binder_tree.state() == QAbstractItemView.State.EditingState:
                self._remote_tree_refresh_scheduled = True
                QTimer.singleShot(50, self._flush_remote_tree_refresh)
                return
        except (AttributeError, RuntimeError):
            return
        self._remote_tree_refresh_pending = False
        self.load_tree_data()

    def _finish_tree_item_creation(self, item):
        try:
            item.setData(0, Qt.ItemDataRole.UserRole + 4, False)
        except RuntimeError:
            pass
        pending_items = getattr(self, "_pending_tree_creation_items", [])
        self._pending_tree_creation_items = [
            pending for pending in pending_items if pending is not item
        ]
        if getattr(self, "_tree_creation_item", None) is item:
            self._tree_creation_item = None
        self._tree_item_creation_active = bool(self._pending_tree_creation_items)
        if (
            not self._tree_item_creation_active
            and getattr(self, "_remote_tree_refresh_pending", False)
        ):
            self._schedule_remote_tree_refresh()

    def _commit_tree_item_creation(self, item):
        try:
            is_pending = bool(
                item and item.data(0, Qt.ItemDataRole.UserRole + 4)
            )
        except RuntimeError:
            is_pending = False
        if not is_pending:
            return False
        try:
            rel_path = item.data(0, Qt.ItemDataRole.UserRole)
            is_folder = bool(item.data(0, Qt.ItemDataRole.UserRole + 1))
        except RuntimeError:
            return False
        sync_manager = getattr(self, "sync_manager", None)
        if not hasattr(getattr(self, "wpm", None), "project_settings"):
            if rel_path and not is_folder and sync_manager is not None:
                sync_manager.record_path_change(rel_path, rel_path)
            self._finish_tree_item_creation(item)
            self.save_tree_order()
            return True
        try:
            mutation_gate = (
                sync_manager.local_structure_mutation()
                if sync_manager is not None else nullcontext()
            )
            with mutation_gate:
                operations = []
                if rel_path and sync_manager is not None:
                    if is_folder:
                        operations = sync_manager.record_path_change(
                            rel_path, rel_path, retry=False
                        )
                    else:
                        operation = sync_manager.record_created_document(
                            rel_path, retry=False
                        )
                        if operation:
                            operations = [operation]
                self._finish_tree_item_creation(item)
                tree_order = WritingTreeMixin._current_tree_order_snapshot(self)
                if operations and any(
                    operation.get("contract_structure_intents")
                    for operation in operations
                    if isinstance(operation, dict)
                ):
                    sync_manager.queue_contract_path_change_with_order(
                        operations, tree_order, retry=False
                    )
                elif operations:
                    self.defer_tree_order_until_operations(
                        operations, tree_order=tree_order
                    )
                else:
                    WritingTreeMixin._persist_tree_order(
                        self, tree_order, retry=False
                    )
            if sync_manager is not None:
                sync_manager.retry_pending_syncs()
            return True
        except Exception:
            if sync_manager is not None and rel_path:
                sync_manager.record_structure_recovery(
                    rel_path, rel_path, "CREATE_DURABILITY_FAILED"
                )
            self._finish_tree_item_creation(item)
            if hasattr(self, "load_tree_data"):
                self.load_tree_data()
            return False

    def _finalize_current_tree_creation(self):
        item = getattr(self, "_tree_creation_item", None)
        if item is not None:
            self._commit_tree_item_creation(item)

    def on_tree_editor_closed(self, *_args):
        """Finish a new-item transaction even when inline editing is cancelled."""
        editor = _args[0] if _args else None
        item = getattr(self, "_tree_creation_item", None)
        if item is None:
            item = self.binder_tree.currentItem()

        def finish_if_needed():
            self._commit_tree_item_creation(item)

        def finish_flags_after_editor_destruction(*_destroyed_args):
            try:
                is_folder = bool(
                    item.data(0, Qt.ItemDataRole.UserRole + 1)
                )
                self._finish_item_name_edit(item, is_folder)
            except (AttributeError, RuntimeError):
                pass

        QTimer.singleShot(0, finish_if_needed)
        try:
            if editor is not None:
                editor.destroyed.connect(
                    lambda *_destroyed_args: QTimer.singleShot(
                        0, finish_flags_after_editor_destruction
                    )
                )
            else:
                QTimer.singleShot(50, finish_flags_after_editor_destruction)
        except RuntimeError:
            QTimer.singleShot(50, finish_flags_after_editor_destruction)

    def load_tree_data(self):
        """로컬 폴더를 스캔하여 트리에 동적으로 노드를 생성합니다."""
        self.binder_tree.blockSignals(True)
        self.binder_tree.clear()


        # 고정 노드 맵핑
        self.root_nodes = dict(self.FIXED_ROOT_NODES)

        tree_order = self.wpm.project_settings.get("tree_order", {})
        root_order = self._normalize_fixed_root_order(
            tree_order.get("<root>", [])
        )

        sorted_root_keys = list(self.root_nodes.keys())
        root_positions = {
            canonical_root_storage_name(name): index
            for index, name in enumerate(root_order)
        }
        # '📚 원고'는 무조건 최상단(-1)으로 고정, 나머지는 공통 저장 이름의 순서를 따름
        sorted_root_keys.sort(
            key=lambda name: (
                -1
                if name == "📚 원고"
                else root_positions.get(
                    canonical_root_storage_name(name), 999
                )
            )
        )

        for name in sorted_root_keys:
            relative_path = self.root_nodes[name]
            item = QTreeWidgetItem(self.binder_tree, [name])
            item.setData(0, Qt.ItemDataRole.UserRole, relative_path)

            if relative_path and self.wpm.writing_root_path:
                full_path = os.path.join(self.wpm.writing_root_path, relative_path)
                if os.path.exists(full_path):
                    self._populate_tree_level(item, full_path, relative_path)

        # 추가 커스텀 최상위 폴더 및 문서 스캔
        main_path = os.path.join(self.wpm.writing_root_path, "메인") if self.wpm.writing_root_path else ""
        if main_path and os.path.exists(main_path):
            try:
                # Derived from FIXED_ROOT_NODES so a renamed root cannot show up
                # twice. A legacy 메인/플롯 folder is deliberately not listed here:
                # it then surfaces as a custom root instead of vanishing.
                fixed_root_names = {
                    path.split("/", 1)[1] for _, path in self.FIXED_ROOT_NODES
                }
                for d in os.listdir(main_path):
                    if d not in fixed_root_names:
                        full_path = os.path.join(main_path, d)
                        rel_path = f"메인/{d}"

                        if os.path.isdir(full_path):
                            item = QTreeWidgetItem(self.binder_tree, [d])
                            item.setData(0, Qt.ItemDataRole.UserRole, rel_path)
                            # Custom root items must carry the same type marker
                            # as lazily populated children.  Without it the
                            # inline rename handler treats a reloaded empty
                            # folder as an unknown item and silently ignores
                            # the edit; the next refresh then shows the old disk
                            # name again before tree-order can be enqueued.
                            item.setData(
                                0, Qt.ItemDataRole.UserRole + 1, True
                            )
                            item.setIcon(0, self._get_emoji_icon("📁"))
                            self._populate_tree_level(item, full_path, rel_path)
                        elif full_path.endswith(".txt"):
                            display_text = d[:-4]
                            item = QTreeWidgetItem(self.binder_tree, [display_text])
                            item.setData(0, Qt.ItemDataRole.UserRole, rel_path)
                            item.setData(
                                0, Qt.ItemDataRole.UserRole + 1, False
                            )
                            if os.path.getsize(full_path) == 0:
                                item.setIcon(0, self._get_empty_page_icon())
                            else:
                                item.setIcon(0, self._get_emoji_icon("📝"))
            except Exception:
                pass

        # Saved orders from older builds and newly discovered custom roots may
        # otherwise leave items below trash. Repair the invariant on every load.
        self._apply_saved_root_order(root_order)
        self.binder_tree.ensure_trash_at_bottom()

        if "expanded_folders" not in self.wpm.project_settings:
            for i in range(self.binder_tree.topLevelItemCount()):
                item = self.binder_tree.topLevelItem(i)
                item.setExpanded(True)
                self.on_tree_item_expanded(item)
        else:
            self.restore_tree_state()

        # This creates a scrollable blank area after the last root item, making
        # it easy to right-click there without targeting an existing item.
        self.binder_tree.add_bottom_spacer()
        self.binder_tree.blockSignals(False)

    def _get_emoji_icon(self, emoji_text):
        from PyQt6.QtGui import QPixmap, QPainter, QIcon
        pixmap = QPixmap(20, 20)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        from PyQt6.QtGui import QFont
        font = QFont("Segoe UI Emoji", 12)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, emoji_text)
        painter.end()
        return QIcon(pixmap)

    def _get_empty_page_icon(self):
        from PyQt6.QtGui import QPixmap, QPainter, QIcon, QColor, QPen
        from PyQt6.QtCore import Qt
        pixmap = QPixmap(20, 20)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        pen = QPen(QColor("#cccccc"))
        pen.setWidth(1)
        painter.setPen(pen)
        painter.setBrush(QColor("#ffffff"))

        # 12x16 픽셀 크기의 아무 무늬 없는 백지 그리기 (오른쪽 위 모서리는 살짝 접힌 효과를 주어도 되지만 깔끔하게 직사각형으로 처리)
        painter.drawRect(4, 2, 12, 16)

        painter.end()
        return QIcon(pixmap)

    def _populate_tree_level(self, parent_item, dir_path, relative_base):
        # Programmatic tree rebuilding changes item data several times.  Those
        # changes must never enter the rename/create handler while the subtree is
        # only half built, otherwise a file can be reinserted as another file's
        # child and the refresh can recurse until the process exits.
        previous_signal_state = self.binder_tree.blockSignals(True)
        try:
            # 게으른 로딩을 위해 기존 자식들을 모두 지웁니다.
            parent_item.takeChildren()
            entries = self._sorted_tree_entries(dir_path, relative_base)

            for entry in entries:
                if entry.endswith(".tmp"):
                    continue
                full_entry_path = os.path.join(dir_path, entry)
                rel_path = os.path.join(relative_base, entry).replace("\\", "/")

                # 파일인 경우 .txt 확장자 숨김 처리
                display_text = entry[:-4] if entry.endswith(".txt") else entry
                child_item = QTreeWidgetItem(parent_item, [display_text])
                child_item.setData(0, Qt.ItemDataRole.UserRole, rel_path)

                if os.path.isdir(full_entry_path):
                    child_item.setIcon(0, self._get_emoji_icon("📁"))
                    # 폴더인 경우, 내부에 항목이 있는지 확인하기 위해 더미 아이템 추가
                    dummy = QTreeWidgetItem(child_item, ["<dummy>"])
                    child_item.setData(0, Qt.ItemDataRole.UserRole + 1, True) # is_folder
                    child_item.setData(0, Qt.ItemDataRole.UserRole + 2, False) # is_loaded
                    child_item.setFlags(
                        Qt.ItemFlag.ItemIsSelectable
                        | Qt.ItemFlag.ItemIsDragEnabled
                        | Qt.ItemFlag.ItemIsDropEnabled
                        | Qt.ItemFlag.ItemIsEnabled
                    )
                else:
                    child_item.setData(0, Qt.ItemDataRole.UserRole + 1, False) # is_folder
                    child_item.setFlags(
                        Qt.ItemFlag.ItemIsSelectable
                        | Qt.ItemFlag.ItemIsDragEnabled
                        | Qt.ItemFlag.ItemIsEnabled
                    )
                    if full_entry_path.endswith(".txt"):
                        if os.path.getsize(full_entry_path) == 0:
                            child_item.setIcon(0, self._get_empty_page_icon())
                        else:
                            child_item.setIcon(0, self._get_emoji_icon("📝"))
        except Exception:
            pass
        finally:
            self.binder_tree.blockSignals(previous_signal_state)

    def _sorted_tree_entries(self, dir_path, relative_base):
        entries = os.listdir(dir_path)

        fixed_manuscript_order = canonical_manuscript_children(
            relative_base, entries
        )
        if fixed_manuscript_order is not None:
            return fixed_manuscript_order

        if relative_base == "메인/휴지통":
            # 휴지통은 장치별 로컬 드래그 순서를 무시하고 최신 삭제본부터
            # 표시한다. 원격 장치에서 보관 파일명이 달라도 순서가 같아진다.
            trash_items = {
                item.get("name"): item
                for item in self.wpm.list_trash_items()
                if item.get("name")
            }

            def trash_sort_key(name):
                info = trash_items.get(name, {})
                deleted_at = info.get("deleted_at") or ""
                try:
                    from datetime import datetime
                    deleted_timestamp = datetime.fromisoformat(
                        deleted_at.replace("Z", "+00:00")
                    ).timestamp()
                except (TypeError, ValueError):
                    try:
                        deleted_timestamp = os.path.getmtime(os.path.join(dir_path, name))
                    except OSError:
                        deleted_timestamp = 0
                # Server commit time is shared by every device. UUID is the
                # deterministic tie breaker when many deletes share a timestamp.
                return (
                    -deleted_timestamp,
                    str(info.get("document_id") or ""),
                    name.casefold(),
                )

            entries.sort(key=trash_sort_key)
            return entries

        tree_order = self.wpm.project_settings.get("tree_order", {})
        saved_order = tree_order.get(relative_base, [])

        def sort_key(x):
            # 1. 커스텀 순서가 저장되어 있으면 그 인덱스를 따른다.
            if x in saved_order:
                return (0, saved_order.index(x))
            # 2. 없으면 폴더가 먼저 오고 이름순으로 (새 파일 등)
            is_file = not os.path.isdir(os.path.join(dir_path, x))
            return (1, is_file, self._natural_sort_key(x))

        entries.sort(key=sort_key)
        return entries

    def on_tree_item_expanded(self, item):
        is_loaded = item.data(0, Qt.ItemDataRole.UserRole + 2)
        if is_loaded is False: # 명시적 체크
            rel_path = item.data(0, Qt.ItemDataRole.UserRole)
            if rel_path and self.wpm.writing_root_path:
                full_path = os.path.join(self.wpm.writing_root_path, rel_path)
                self.binder_tree.blockSignals(True)
                self._populate_tree_level(item, full_path, rel_path)
                item.setData(0, Qt.ItemDataRole.UserRole + 2, True)
                self.binder_tree.blockSignals(False)

    def save_tree_state(self, item=None):
        if getattr(self, '_is_restoring_tree', False):
            return

        expanded_paths = []
        def traverse(current_item):
            if current_item.isExpanded():
                rel_path = current_item.data(0, Qt.ItemDataRole.UserRole)
                if rel_path:
                    expanded_paths.append(rel_path)
            for i in range(current_item.childCount()):
                traverse(current_item.child(i))

        for i in range(self.binder_tree.topLevelItemCount()):
            traverse(self.binder_tree.topLevelItem(i))

        self.wpm.project_settings["expanded_folders"] = expanded_paths
        self.wpm.save_settings()

    def restore_tree_state(self):
        expanded_paths = set(self.wpm.project_settings.get("expanded_folders", []))
        if not expanded_paths: return

        self._is_restoring_tree = True
        try:
            def try_expand(current_item):
                rel_path = current_item.data(0, Qt.ItemDataRole.UserRole)
                if rel_path in expanded_paths:
                    if not current_item.isExpanded():
                        current_item.setExpanded(True)
                        self.on_tree_item_expanded(current_item)
                    for i in range(current_item.childCount()):
                        try_expand(current_item.child(i))

            for i in range(self.binder_tree.topLevelItemCount()):
                try_expand(self.binder_tree.topLevelItem(i))
        finally:
            self._is_restoring_tree = False

    def _natural_sort_key(self, s):
        return [int(text) if text.isdigit() else text.lower() for text in re.split('([0-9]+)', s)]

    @staticmethod
    def _is_live_qt_object(obj):
        # 컨텍스트 메뉴 lambda는 QTreeWidgetItem을 그대로 붙잡는다. 원격 폴링이
        # 트리를 다시 그리면 C++ 객체가 파괴되고, 그 뒤 접근하면 PyQt6가
        # RuntimeError를 낸다. 슬롯 안의 미처리 예외라 프로세스가 abort 된다.
        if obj is None:
            return False
        try:
            return not sip.isdeleted(obj)
        except TypeError:
            # sip 래핑이 아닌 객체(테스트 대역 등)는 수명 추적 대상이 아니다.
            return True

    def show_tree_context_menu(self, pos):
        from PyQt6.QtWidgets import QApplication, QMenu
        from PyQt6.QtCore import Qt, QTimer, QObject, QEvent
        from PyQt6.QtGui import QAction

        callbacks = {}
        custom_triggered_shortcut = [None]

        class GlobalMenuFilter(QObject):
            def __init__(self, menu):
                super().__init__()
                self.menu = menu
                self._processing = False

            def eventFilter(self, obj, event):
                if self._processing:
                    return False
                self._processing = True
                try:
                    target_shortcut = None

                    if event.type() == QEvent.Type.KeyPress:
                        key = event.key()
                        text = event.text().lower()
                        try:
                            vk = event.nativeVirtualKey()
                        except:
                            vk = 0

                        if key == Qt.Key.Key_F or text in ['f', 'ㄹ'] or vk == 0x46:
                            target_shortcut = 'F'
                        elif key == Qt.Key.Key_N or text in ['n', 'ㅜ'] or vk == 0x4E:
                            target_shortcut = 'N'
                        elif key == Qt.Key.Key_D or text in ['d', 'ㅇ'] or vk == 0x44:
                            target_shortcut = 'D'
                        elif key == Qt.Key.Key_E or text in ['e', 'ㄷ'] or vk == 0x45:
                            target_shortcut = 'E'

                    elif event.type() == QEvent.Type.InputMethod:
                        text = event.commitString()
                        if not text:
                            text = event.preeditString()
                        if text:
                            text = text.lower()
                            if text in ['f', 'ㄹ']:
                                target_shortcut = 'F'
                            elif text in ['n', 'ㅜ']:
                                target_shortcut = 'N'
                            elif text in ['d', 'ㅇ']:
                                target_shortcut = 'D'
                            elif text in ['e', 'ㄷ']:
                                target_shortcut = 'E'

                    if target_shortcut and target_shortcut in callbacks:
                        custom_triggered_shortcut[0] = target_shortcut
                        self.menu.close()
                        return True

                    return False
                finally:
                    self._processing = False

        item = self.binder_tree.itemAt(pos)
        if self.binder_tree.is_bottom_spacer(item):
            item = None
        menu = QMenu(self.window())

        if not item:
            add_folder_action = QAction("새 폴더", self)
            add_folder_action.triggered.connect(lambda: self.start_create_root_item(is_folder=True))
            menu.addAction(add_folder_action)
            callbacks['F'] = lambda: self.start_create_root_item(is_folder=True)
        elif item.text(0) == "📚 원고":
            add_volume_action = QAction("권 추가", self)
            add_volume_action.triggered.connect(self.add_volume)
            menu.addAction(add_volume_action)

            menu.addSeparator()
            extract_menu = menu.addMenu("챕터 추출")

            extract_all_action = QAction("전체 추출", self)
            extract_all_action.triggered.connect(self.extract_all_chapters)
            extract_menu.addAction(extract_all_action)

            extract_partial_action = QAction("부분 추출", self)
            extract_partial_action.triggered.connect(self.extract_partial_chapters)
            extract_menu.addAction(extract_partial_action)
        elif item.text(0) == "🗑️ 휴지통":
            empty_trash_action = QAction("비우기", self)
            empty_trash_action.triggered.connect(self.empty_trash)
            menu.addAction(empty_trash_action)
            callbacks['E'] = self.empty_trash
            callbacks['D'] = self.empty_trash
        else:
            rel_path = item.data(0, Qt.ItemDataRole.UserRole)
            is_file = rel_path and rel_path.endswith(".txt")
            top_parent = item
            while top_parent.parent():
                top_parent = top_parent.parent()

            if top_parent.text(0) == "🗑️ 휴지통":
                restore_original_action = QAction("↩ 원래 위치로 복원", self)
                restore_original_action.triggered.connect(lambda: self.restore_trash_item(item))
                menu.addAction(restore_original_action)

                restore_selected_action = QAction("📁 선택 위치로 복원", self)
                restore_selected_action.triggered.connect(lambda: self.restore_trash_item(item, choose_location=True))
                menu.addAction(restore_selected_action)
                menu.addSeparator()

                delete_action = QAction("영구 삭제", self)
                delete_action.triggered.connect(lambda: self.delete_tree_item(item))
                menu.addAction(delete_action)
                callbacks['D'] = lambda: self.delete_tree_item(item)
            else:
                if is_file:
                    history_action = QAction("🕒 이전 버전 보기/복원", self)
                    history_action.triggered.connect(lambda: self.show_history_viewer(rel_path))
                    menu.addAction(history_action)

                if top_parent.text(0) != "📚 원고" and not is_file:
                    add_folder_action = QAction("새 폴더", self)
                    add_folder_action.triggered.connect(lambda: self.start_create_item(item, is_folder=True))
                    menu.addAction(add_folder_action)
                    callbacks['F'] = lambda: self.start_create_item(item, is_folder=True)

                    add_file_action = QAction("새 문서", self)
                    add_file_action.triggered.connect(lambda: self.start_create_item(item, is_folder=False))
                    menu.addAction(add_file_action)
                    callbacks['N'] = lambda: self.start_create_item(item, is_folder=False)

                if item.parent() is not None or item.text(0) not in self.root_nodes:
                    is_volume = (item.parent() is not None and item.parent().text(0) == "📚 원고")
                    if not is_volume:
                        rename_action = QAction("이름 변경", self)
                        rename_action.triggered.connect(lambda: self.start_rename_item(item))
                        menu.addAction(rename_action)

                    # '📚 원고' 하위의 모든 폴더/파일은 삭제 불가 (불상사 방지)
                    if top_parent.text(0) != "📚 원고":
                        delete_action = QAction("삭제", self)
                        delete_action.triggered.connect(lambda: self.delete_tree_item(item))
                        menu.addAction(delete_action)
                        callbacks['D'] = lambda: self.delete_tree_item(item)

        # 글로벌 이벤트 필터 장착 (메뉴가 열려있는 동안 모든 키보드/IME 이벤트 감시)
        global_filter = GlobalMenuFilter(menu)
        QApplication.instance().installEventFilter(global_filter)

        def _exec_menu():
            # 메뉴가 열려 있는 동안에는 원격 폴링이 트리를 다시 그리지 못하게 막는다.
            # 메뉴 항목들이 붙잡고 있는 QTreeWidgetItem이 파괴되는 것을 줄인다.
            pull_timer = getattr(self, "remote_pull_timer", None)
            pull_timer_was_active = bool(
                pull_timer is not None and pull_timer.isActive()
            )
            if pull_timer_was_active:
                pull_timer.stop()
            try:
                # 50ms 대기하는 동안 이미 단축키가 눌렸다면 메뉴를 열지 않고 바로 콜백 실행
                if custom_triggered_shortcut[0] is None:
                    menu.exec(self.binder_tree.viewport().mapToGlobal(pos))
            finally:
                if pull_timer_was_active and WritingTreeMixin._is_live_qt_object(pull_timer):
                    pull_timer.start()
                QApplication.instance().removeEventFilter(global_filter)

                # 좀비 청소
                for action in menu.actions():
                    action.deleteLater()

                # 안전한 외부 실행 (팝업 충돌 방지)
                triggered_shortcut = custom_triggered_shortcut[0]
                if triggered_shortcut:
                    QApplication.inputMethod().reset()
                    QTimer.singleShot(0, callbacks[triggered_shortcut])

        QTimer.singleShot(50, _exec_menu)

    def delete_tree_item(self, item):
        if not WritingTreeMixin._is_live_qt_object(item): return
        rel_path = item.data(0, Qt.ItemDataRole.UserRole)
        if not rel_path: return

        top_parent = item
        while top_parent.parent():
            top_parent = top_parent.parent()

        if top_parent.text(0) == "🗑️ 휴지통":
            reply = QMessageBox.question(self, "영구 삭제 확인", f"'{item.text(0)}'을(를) 영구적으로 삭제하시겠습니까?\n이 작업은 되돌릴 수 없습니다.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                try:
                    trash_entry = next(
                        (
                            entry for entry in self.wpm.list_trash_items()
                            if entry.get("trash_path") == rel_path
                        ),
                        {"trash_path": rel_path},
                    )
                    self.wpm.delete_from_trash(rel_path)
                    if hasattr(self, "sync_manager"):
                        self.sync_manager.record_trash_purge([trash_entry])
                    self._cleanup_after_delete(rel_path, item)
                except Exception as e:
                    QMessageBox.warning(self, "오류", f"영구 삭제 실패: {e}")
            return

        reply = QMessageBox.question(self, "삭제 확인", f"'{item.text(0)}'을(를) 휴지통으로 이동하시겠습니까?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            try:
                sync_manager = getattr(self, "sync_manager", None)
                mutation_gate = (
                    sync_manager.local_structure_mutation()
                    if sync_manager is not None else nullcontext()
                )
                with mutation_gate:
                    trash_rel_path = self.wpm.move_to_trash(rel_path)
                    if sync_manager is not None:
                        operations = sync_manager.record_tombstone(
                            rel_path, trash_rel_path, retry=False
                        )
                    else:
                        operations = []
                    if hasattr(self, "controller"):
                        self.controller.forget_path(rel_path)
                    self._cleanup_after_delete(
                        rel_path, item, operations=operations
                    )
                if sync_manager is not None:
                    sync_manager.retry_pending_syncs()

                # 휴지통 노드 시각적 갱신
                for i in range(self.binder_tree.topLevelItemCount()):
                    if self.binder_tree.topLevelItem(i).text(0) == "🗑️ 휴지통":
                        trash_item = self.binder_tree.topLevelItem(i)
                        if trash_item.isExpanded():
                            self._populate_tree_level(trash_item, os.path.join(self.wpm.writing_root_path, "메인", "휴지통"), "메인/휴지통")
                        else:
                            trash_item.setData(0, Qt.ItemDataRole.UserRole + 2, False) # is_loaded = False
                            if trash_item.childCount() == 0:
                                from PyQt6.QtWidgets import QTreeWidgetItem
                                QTreeWidgetItem(trash_item, ["<dummy>"])
                        break
            except Exception as e:
                if 'trash_rel_path' in locals():
                    sync_manager = getattr(self, "sync_manager", None)
                    rollback_gate = (
                        sync_manager.local_structure_mutation()
                        if sync_manager is not None else nullcontext()
                    )
                    with rollback_gate:
                        try:
                            self.wpm.restore_from_trash(trash_rel_path)
                        except Exception:
                            if sync_manager is not None:
                                sync_manager.record_structure_recovery(
                                    rel_path,
                                    trash_rel_path,
                                    "DELETE_ROLLBACK_FAILED",
                                )
                    self.load_tree_data()
                QMessageBox.warning(self, "오류", f"삭제 실패: {e}")

    def restore_trash_item(self, item, choose_location=False):
        if not WritingTreeMixin._is_live_qt_object(item):
            return
        rel_path = item.data(0, Qt.ItemDataRole.UserRole)
        if not rel_path:
            return

        destination_parent = None
        if choose_location:
            main_root = os.path.abspath(os.path.join(self.wpm.writing_root_path, "메인"))
            selected = QFileDialog.getExistingDirectory(self, "복원할 위치 선택", main_root)
            if not selected:
                return
            selected = os.path.abspath(selected)
            try:
                if os.path.commonpath([selected, main_root]) != main_root:
                    raise ValueError
            except ValueError:
                QMessageBox.warning(self, "복원 실패", "집필 모드의 메인 폴더 안에서 위치를 선택해주세요.")
                return
            destination_parent = os.path.relpath(selected, self.wpm.writing_root_path).replace("\\", "/")

        try:
            sync_manager = getattr(self, "sync_manager", None)
            mutation_gate = (
                sync_manager.local_structure_mutation()
                if sync_manager is not None else nullcontext()
            )
            trash_entry = next(
                (
                    entry for entry in self.wpm.list_trash_items()
                    if entry.get("trash_path") == rel_path
                ),
                {},
            )
            original_rel_path = trash_entry.get("original_path")
            with mutation_gate:
                restored_path = self.wpm.restore_from_trash(
                    rel_path, destination_parent
                )
                if sync_manager is not None:
                    operations = sync_manager.record_restore(
                        rel_path,
                        restored_path,
                        original_rel_path=original_rel_path,
                        retry=False,
                    )
                else:
                    operations = []
                if hasattr(self, "controller"):
                    self.controller.rename_path(rel_path, restored_path)
                self.load_tree_data()
                if sync_manager is not None:
                    tree_order = WritingTreeMixin._current_tree_order_snapshot(self)
                    if not operations:
                        # A folder with no documents in it restores without a
                        # single document operation to publish. The tree order
                        # is then the only thing that tells the server this
                        # folder is alive again, and the server is still
                        # holding a tombstone for it: skip this and the next
                        # pull follows that tombstone and takes the folder
                        # straight back to 휴지통. Deleting publishes
                        # unconditionally for the same reason.
                        WritingTreeMixin._persist_tree_order(
                            self, tree_order, retry=False
                        )
                    elif any(
                        operation.get("contract_structure_intents")
                        or operation.get("contract_document_changes")
                        for operation in operations
                        if isinstance(operation, dict)
                    ):
                        sync_manager.queue_contract_path_change_with_order(
                            operations, tree_order, retry=False
                        )
                    else:
                        self.defer_tree_order_until_operations(
                            operations, tree_order=tree_order
                        )
            if sync_manager is not None:
                sync_manager.retry_pending_syncs()
            QMessageBox.information(self, "복원 완료", f"다음 위치로 복원했습니다.\n{restored_path}")
        except Exception as e:
            if 'restored_path' in locals():
                sync_manager = getattr(self, "sync_manager", None)
                rollback_gate = (
                    sync_manager.local_structure_mutation()
                    if sync_manager is not None else nullcontext()
                )
                with rollback_gate:
                    try:
                        self.wpm.move_to_trash(restored_path)
                    except Exception:
                        if sync_manager is not None:
                            sync_manager.record_structure_recovery(
                                rel_path,
                                restored_path,
                                "RESTORE_ROLLBACK_FAILED",
                            )
                self.load_tree_data()
            QMessageBox.warning(self, "복원 실패", str(e))

    def _cleanup_after_delete(self, rel_path, item, operations=None):
        parent = item.parent()
        if parent:
            parent.removeChild(item)
        else:
            index = self.binder_tree.indexOfTopLevelItem(item)
            self.binder_tree.takeTopLevelItem(index)

        def path_was_deleted(current_path):
            return bool(
                current_path
                and (current_path == rel_path or current_path.startswith(rel_path + "/"))
            )

        if path_was_deleted(getattr(self, 'current_loaded_file_left', None)):
            self.left_editor.clear()
            self.left_editor.setReadOnly(True)
            self.current_loaded_file_left = None
            self.lbl_current_doc.setText("문서를 선택하세요")
            self.is_dirty_left = False
            self.left_editor.document().setModified(False)

        if path_was_deleted(getattr(self, 'current_loaded_file_right', None)):
            self.right_editor.clear()
            self.right_editor.setReadOnly(True)
            self.current_loaded_file_right = None
            self.lbl_r_doc.setText("문서를 선택하세요")
            self.is_dirty_right = False
            self.right_editor.document().setModified(False)

        if not hasattr(getattr(self, "wpm", None), "project_settings"):
            self.save_tree_order()
            return
        tree_order = WritingTreeMixin._current_tree_order_snapshot(self)
        if operations and any(
            operation.get("contract_structure_intents")
            or operation.get("contract_document_changes")
            for operation in operations
            if isinstance(operation, dict)
        ):
            self.sync_manager.queue_contract_path_change_with_order(
                operations, tree_order, retry=False
            )
        else:
            WritingTreeMixin._persist_tree_order(
                self, tree_order, retry=False
            )

    def empty_trash(self):
        reply = QMessageBox.question(self, "휴지통 비우기", "휴지통의 모든 항목을 영구적으로 삭제하시겠습니까?\n이 작업은 되돌릴 수 없습니다.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            try:
                trash_items = self.wpm.list_trash_items()
                self.wpm.empty_trash()
                if hasattr(self, "sync_manager"):
                    self.sync_manager.record_trash_purge(
                        trash_items, empty_all=True
                    )
                for i in range(self.binder_tree.topLevelItemCount()):
                    if self.binder_tree.topLevelItem(i).text(0) == "🗑️ 휴지통":
                        trash_item = self.binder_tree.topLevelItem(i)
                        trash_item.takeChildren()
                        break
                QMessageBox.information(self, "완료", "휴지통을 비웠습니다.")
            except Exception as e:
                QMessageBox.warning(self, "오류", f"휴지통 비우기 실패: {e}")

    def handle_item_moved(self, item, old_rel_path, new_parent_rel_path):
        new_rel_path = None
        filesystem_changed = False
        try:
            sync_manager = getattr(self, "sync_manager", None)
            mutation_gate = (
                sync_manager.local_structure_mutation()
                if sync_manager is not None else nullcontext()
            )
            with mutation_gate:
                new_rel_path = self.wpm.move_item(
                    old_rel_path, new_parent_rel_path
                )
                filesystem_changed = True
                path_operations = []
                if sync_manager is not None:
                    path_operations = sync_manager.record_path_change(
                        old_rel_path, new_rel_path, retry=False
                    )
                if hasattr(self, "controller"):
                    self.controller.rename_path(old_rel_path, new_rel_path)

                # 하위 항목들의 rel_path도 전부 업데이트
                def update_paths(node, current_rel_path):
                    node.setData(0, Qt.ItemDataRole.UserRole, current_rel_path)
                    for i in range(node.childCount()):
                        child = node.child(i)
                        if child.text(0) == "<dummy>":
                            continue
                        child_basename = os.path.basename(
                            child.data(0, Qt.ItemDataRole.UserRole)
                        )
                        update_paths(
                            child,
                            os.path.join(
                                current_rel_path, child_basename
                            ).replace("\\", "/"),
                        )

                update_paths(item, new_rel_path)

                def update_loaded_file(attr_name):
                    current_path = getattr(self, attr_name, None)
                    if current_path:
                        if current_path == old_rel_path:
                            setattr(self, attr_name, new_rel_path)
                        elif current_path.startswith(old_rel_path + "/"):
                            setattr(
                                self,
                                attr_name,
                                current_path.replace(
                                    old_rel_path, new_rel_path, 1
                                ),
                            )

                update_loaded_file('current_loaded_file_left')
                update_loaded_file('current_loaded_file_right')

                self.save_tree_order_for_rename(
                    old_rel_path,
                    new_rel_path,
                    retry=False,
                    operations=path_operations,
                )
            if sync_manager is not None:
                sync_manager.retry_pending_syncs()

        except Exception as e:
            if filesystem_changed and new_rel_path:
                self._rollback_structure_path(new_rel_path, old_rel_path)
            QMessageBox.warning(self, "이동 실패", f"항목을 이동할 수 없습니다:\n{e}")
            self.load_tree_data() # UI 원복

    def _rollback_structure_path(self, current_rel_path, original_rel_path):
        """Restore filesystem and UI projection after durable queue failure."""
        try:
            self.wpm.rename_item(current_rel_path, original_rel_path)
            if hasattr(self, "controller"):
                self.controller.rename_path(
                    current_rel_path, original_rel_path
                )
            for attr_name in (
                "current_loaded_file_left", "current_loaded_file_right"
            ):
                current_path = getattr(self, attr_name, None)
                if current_path == current_rel_path:
                    setattr(self, attr_name, original_rel_path)
                elif current_path and current_path.startswith(
                    current_rel_path + "/"
                ):
                    setattr(
                        self,
                        attr_name,
                        current_path.replace(
                            current_rel_path, original_rel_path, 1
                        ),
                    )
            versions = getattr(self, "loaded_versions", {})
            restored = {}
            for path in list(versions):
                if path == current_rel_path or path.startswith(
                    current_rel_path + "/"
                ):
                    restored[path.replace(
                        current_rel_path, original_rel_path, 1
                    )] = versions.pop(path)
            versions.update(restored)
            return True
        except Exception:
            sync_manager = getattr(self, "sync_manager", None)
            if sync_manager is not None:
                sync_manager.record_structure_recovery(
                    original_rel_path,
                    current_rel_path,
                    "FILESYSTEM_ROLLBACK_FAILED",
                )
            return False

    def _current_tree_order_snapshot(self):
        saved_order = self.wpm.project_settings.get("tree_order", {})
        tree_order = (
            copy.deepcopy(saved_order) if isinstance(saved_order, dict) else {}
        )
        self.binder_tree.ensure_trash_at_bottom()

        writing_root = getattr(self.wpm, "writing_root_path", None)
        if writing_root:
            # Remove only baselines for directories that no longer exist.  This
            # prevents a deleted folder from surviving merely because its item
            # was outside the currently materialized UI subtree.
            for parent_path in list(tree_order):
                if parent_path == "<root>":
                    continue
                full_parent = os.path.join(writing_root, parent_path)
                if not os.path.isdir(full_parent):
                    tree_order.pop(parent_path, None)

        def traverse(item):
            rel_path = item.data(0, Qt.ItemDataRole.UserRole)
            if rel_path:
                child_names = []
                has_dummy = False
                for i in range(item.childCount()):
                    child = item.child(i)
                    if child.text(0) == "<dummy>":
                        has_dummy = True
                        continue
                    child_rel_path = child.data(0, Qt.ItemDataRole.UserRole)
                    if child_rel_path:
                        child_names.append(os.path.basename(child_rel_path))

                folder_state = item.data(0, Qt.ItemDataRole.UserRole + 1)
                full_path = (
                    os.path.join(writing_root, rel_path) if writing_root else None
                )
                is_directory = bool(
                    folder_state is True
                    or (full_path and os.path.isdir(full_path))
                    or item.childCount() > 0
                )
                is_unloaded = bool(
                    is_directory
                    and has_dummy
                    and item.data(0, Qt.ItemDataRole.UserRole + 2) is False
                )
                if is_unloaded and full_path:
                    # A collapsed volume is absent from the Qt subtree, not from
                    # the project.  Reuse its saved order and deterministically
                    # append any newly created disk entries (notably 25 chapters
                    # from add_volume) instead of dropping the whole parent list.
                    tree_order[rel_path] = self._sorted_tree_entries(
                        full_path, rel_path
                    )
                elif is_directory:
                    tree_order[rel_path] = child_names
            for i in range(item.childCount()):
                child = item.child(i)
                if child.text(0) != "<dummy>":
                    traverse(child)

        root_names = []
        for i in range(self.binder_tree.topLevelItemCount()):
            item = self.binder_tree.topLevelItem(i)
            if self.binder_tree.is_bottom_spacer(item):
                continue
            rel_path = item.data(0, Qt.ItemDataRole.UserRole)
            root_names.append(
                os.path.basename(rel_path) if rel_path else item.text(0)
            )
            traverse(item)

        tree_order["<root>"] = root_names
        for parent_path, child_names in list(tree_order.items()):
            fixed_order = canonical_manuscript_children(
                parent_path, child_names
            )
            if fixed_order is not None:
                tree_order[parent_path] = fixed_order
        return tree_order

    @staticmethod
    def _rename_saved_tree_order(tree_order, old_rel_path, new_rel_path):
        """Rename one same-parent folder in a saved, possibly lazily loaded tree."""
        if not isinstance(tree_order, dict):
            return {}
        old_rel_path = str(old_rel_path or "").replace("\\", "/").strip("/")
        new_rel_path = str(new_rel_path or "").replace("\\", "/").strip("/")
        old_parent, _, old_name = old_rel_path.rpartition("/")
        new_parent, _, new_name = new_rel_path.rpartition("/")
        if not old_name or not new_name or old_parent != new_parent:
            return copy.deepcopy(tree_order)

        renamed = {}
        for parent_path, child_names in tree_order.items():
            normalized_parent = str(parent_path).replace("\\", "/")
            if normalized_parent == old_rel_path:
                renamed_parent = new_rel_path
            elif normalized_parent.startswith(old_rel_path + "/"):
                renamed_parent = new_rel_path + normalized_parent[len(old_rel_path):]
            else:
                renamed_parent = normalized_parent
            if renamed_parent in renamed and renamed_parent != normalized_parent:
                raise ValueError("TREE_ORDER_PATH_CONFLICT")
            renamed[renamed_parent] = copy.deepcopy(child_names)

        # Children directly below the physical ``메인`` directory are
        # represented by the logical ``<root>`` tree-order key.
        sibling_key = "<root>" if old_parent == "메인" else old_parent
        siblings = renamed.get(sibling_key)
        if isinstance(siblings, list):
            old_indexes = [
                index for index, name in enumerate(siblings) if name == old_name
            ]
            if len(old_indexes) == 1 and new_name not in siblings:
                siblings[old_indexes[0]] = new_name
        return renamed

    def _persist_tree_order(self, tree_order, retry=True):
        self.wpm.project_settings["tree_order"] = tree_order
        self.wpm.save_settings()
        if hasattr(self, "sync_manager"):
            return self.sync_manager.record_tree_order(tree_order, retry=retry)
        return None

    def save_tree_order(self, retry=True):
        return WritingTreeMixin._persist_tree_order(
            self,
            WritingTreeMixin._current_tree_order_snapshot(self),
            retry=retry,
        )

    def save_tree_order_for_rename(
        self, old_rel_path, new_rel_path, retry=True, operations=None
    ):
        """Preserve unloaded descendants while persisting a local path rename."""
        saved_order = WritingTreeMixin._rename_saved_tree_order(
            self.wpm.project_settings.get("tree_order", {}),
            old_rel_path,
            new_rel_path,
        )
        current_order = WritingTreeMixin._current_tree_order_snapshot(self)
        # The visible parent contains the renamed item and therefore has the
        # freshest sibling order. Unloaded descendants are absent from the UI
        # snapshot and remain preserved from the saved snapshot above.
        saved_order.update(current_order)
        if operations:
            if any(
                operation.get("contract_structure_intents")
                for operation in operations
                if isinstance(operation, dict)
            ):
                return self.sync_manager.queue_contract_path_change_with_order(
                    operations, saved_order, retry=retry
                )
            return self.defer_tree_order_until_operations(
                operations, tree_order=saved_order
            )
        return WritingTreeMixin._persist_tree_order(
            self, saved_order, retry=retry
        )

    def defer_tree_order_until_operations(self, operations, tree_order=None):
        tree_order = (
            copy.deepcopy(tree_order)
            if isinstance(tree_order, dict)
            else WritingTreeMixin._current_tree_order_snapshot(self)
        )
        self.wpm.project_settings["tree_order"] = tree_order
        if self.wpm.save_settings() is False:
            raise OSError("TREE_ORDER_SETTINGS_SAVE_FAILED")
        return self.sync_manager.defer_tree_order_until_operations(
            tree_order, operations
        )

    def start_create_root_item(self, is_folder):
        # Rapid clicks can arrive before the first 150ms inline-editor timer.
        # Commit the previous default-named item so only one editor can open.
        self._finalize_current_tree_creation()
        new_name = self._create_binder_item("메인", "새 폴더" if is_folder else "새_문서", is_folder)
        if not new_name: return

        self.binder_tree.blockSignals(True)
        new_item = QTreeWidgetItem()
        self.binder_tree.insert_root_item(new_item)
        display_name = new_name[:-4] if not is_folder else new_name
        new_item.setText(0, display_name)
        editable_flags = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsDropEnabled | Qt.ItemFlag.ItemIsEnabled
        new_item.setFlags(editable_flags)

        if is_folder:
            new_item.setIcon(0, self._get_emoji_icon("📁"))
        else:
            new_item.setIcon(0, self._get_empty_page_icon())

        new_item.setData(0, Qt.ItemDataRole.UserRole, f"메인/{new_name}")
        new_item.setData(0, Qt.ItemDataRole.UserRole + 1, is_folder)
        new_item.setData(0, Qt.ItemDataRole.UserRole + 4, True)  # creation in progress
        self.binder_tree.blockSignals(False)
        pending_items = getattr(self, "_pending_tree_creation_items", [])
        pending_items.append(new_item)
        self._pending_tree_creation_items = pending_items
        self._tree_creation_item = new_item
        self._tree_item_creation_active = True

        def edit_new_item():
            try:
                if not bool(new_item.data(0, Qt.ItemDataRole.UserRole + 4)):
                    return
            except RuntimeError:
                return
            self.binder_tree.blockSignals(True)
            self.binder_tree.scrollToItem(new_item)
            self.binder_tree.setCurrentItem(new_item)
            self.binder_tree.blockSignals(False)

            self.binder_tree.setFocus()
            self.binder_tree.editItem(new_item, 0)

        from PyQt6.QtCore import QTimer
        QTimer.singleShot(150, edit_new_item)

    def show_history_viewer(self, rel_path):
        if not self.wpm.writing_root_path: return
        dialog = HistoryViewerDialog(self.wpm, rel_path, self)
        if dialog.exec():
            backup_path = dialog.get_selected_backup_path()
            if backup_path:
                success, msg = self.sync_manager.check_and_acquire_lock(self.pm.current_project, rel_path, self.session_id)
                if not success:
                    QMessageBox.warning(self, "복원 실패", msg)
                    return
                try:
                    restore_result = self.wpm.restore_backup(rel_path, backup_path)
                    restored_content = restore_result["content"]
                except Exception as e:
                    QMessageBox.warning(self, "복원 실패", str(e))
                    return
                self.sync_manager.upload_content_async(
                    self.wpm, self.pm.current_project, rel_path, restored_content,
                    local_updated_at=self.loaded_versions.get(rel_path),
                    conflict_callback=self.on_conflict_detected
                )
                if self.current_loaded_file_left == rel_path:
                    self.left_editor.blockSignals(True)
                    self.left_editor.setText(restored_content)
                    self.left_editor.document().setModified(False)
                    self.is_dirty_left = False

                    from PyQt6.QtGui import QTextCursor
                    cursor = self.left_editor.textCursor()
                    cursor.movePosition(QTextCursor.MoveOperation.End)
                    self.left_editor.setTextCursor(cursor)

                    self.left_editor.blockSignals(False)
                if self.current_loaded_file_right == rel_path:
                    self.right_editor.blockSignals(True)
                    self.right_editor.setText(restored_content)
                    self.right_editor.document().setModified(False)
                    self.is_dirty_right = False

                    from PyQt6.QtGui import QTextCursor
                    cursor = self.right_editor.textCursor()
                    cursor.movePosition(QTextCursor.MoveOperation.End)
                    self.right_editor.setTextCursor(cursor)

                    self.right_editor.blockSignals(False)
                self.apply_editor_margins()
                QMessageBox.information(self, "복원 완료", "과거 버전으로 성공적으로 복원되었습니다.")

    def start_create_item(self, parent_item, is_folder):
        if not WritingTreeMixin._is_live_qt_object(parent_item): return
        if parent_item.data(0, Qt.ItemDataRole.UserRole + 1) is False:
            return
        self._finalize_current_tree_creation()
        if not parent_item.isExpanded():
            parent_item.setExpanded(True)

        parent_rel_path = parent_item.data(0, Qt.ItemDataRole.UserRole)

        new_name = self._create_binder_item(parent_rel_path, "새 폴더" if is_folder else "새_문서", is_folder)
        new_rel_path = os.path.join(parent_rel_path, new_name).replace("\\", "/") if parent_rel_path else new_name

        self.binder_tree.blockSignals(True)
        new_item = QTreeWidgetItem(parent_item)
        display_name = new_name[:-4] if not is_folder else new_name
        new_item.setText(0, display_name)
        editable_flags = (
            Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsEditable
            | Qt.ItemFlag.ItemIsDragEnabled
            | Qt.ItemFlag.ItemIsEnabled
        )
        if is_folder:
            editable_flags |= Qt.ItemFlag.ItemIsDropEnabled
        new_item.setFlags(editable_flags)

        if not is_folder:
            new_item.setIcon(0, self._get_empty_page_icon())
        else:
            new_item.setIcon(0, self._get_emoji_icon("📁"))

        new_item.setData(0, Qt.ItemDataRole.UserRole, new_rel_path)
        new_item.setData(0, Qt.ItemDataRole.UserRole + 1, is_folder)
        new_item.setData(0, Qt.ItemDataRole.UserRole + 4, True)  # creation in progress
        self.binder_tree.blockSignals(False)
        pending_items = getattr(self, "_pending_tree_creation_items", [])
        pending_items.append(new_item)
        self._pending_tree_creation_items = pending_items
        self._tree_creation_item = new_item
        self._tree_item_creation_active = True

        def edit_new_item():
            try:
                if not bool(new_item.data(0, Qt.ItemDataRole.UserRole + 4)):
                    return
            except RuntimeError:
                return
            self.binder_tree.blockSignals(True)
            self.binder_tree.scrollToItem(new_item)
            self.binder_tree.setCurrentItem(new_item)
            self.binder_tree.blockSignals(False)

            self.binder_tree.setFocus()
            self.binder_tree.editItem(new_item, 0)

        from PyQt6.QtCore import QTimer
        QTimer.singleShot(150, edit_new_item)

    def start_rename_item(self, item=None):
        # 죽은 아이템이 넘어왔다면 현재 선택 항목으로 대체하지 않는다.
        # 엉뚱한 항목의 이름을 바꾸는 것보다 아무 일도 하지 않는 편이 안전하다.
        if item is not None and not WritingTreeMixin._is_live_qt_object(item):
            return
        if item is None:
            item = self.binder_tree.currentItem()
        if not WritingTreeMixin._is_live_qt_object(item): return

        if item.parent() is None and item.text(0) in self.root_nodes:
            return

        is_volume = (item.parent() is not None and item.parent().text(0) == "📚 원고")
        if is_volume:
            return

        is_folder = item.data(0, Qt.ItemDataRole.UserRole + 1) is True
        editable_flags = (
            Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsEditable
            | Qt.ItemFlag.ItemIsDragEnabled
            | Qt.ItemFlag.ItemIsEnabled
        )
        if is_folder:
            editable_flags |= Qt.ItemFlag.ItemIsDropEnabled
        self.binder_tree.blockSignals(True)
        item.setFlags(editable_flags)
        self.binder_tree.blockSignals(False)
        self.binder_tree.editItem(item, 0)

    def on_tree_item_changed(self, item, column):
        # QStyledItemDelegate commits model data synchronously while its editor
        # is still being closed.  A folder rename can update many child rows and
        # SQLite operations, so doing that work inside setModelData/closeEditor
        # risks corrupting Qt's editor lifecycle.  Reserve the structure gate
        # now, then perform the durable rename on the next UI event.
        try:
            editor_is_closing = (
                self.binder_tree.state()
                == QAbstractItemView.State.EditingState
            )
        except (AttributeError, RuntimeError):
            editor_is_closing = False
        if editor_is_closing:
            sync_manager = getattr(self, "sync_manager", None)
            reservation = (
                sync_manager.local_structure_mutation()
                if sync_manager is not None
                else nullcontext()
            )
            reservation.__enter__()

            def apply_after_editor_close():
                try:
                    self._apply_tree_item_changed(
                        item, column, finalize_flags=False
                    )
                finally:
                    reservation.__exit__(None, None, None)

            try:
                QTimer.singleShot(0, apply_after_editor_close)
            except Exception:
                reservation.__exit__(None, None, None)
                raise
            return

        return self._apply_tree_item_changed(item, column)

    def _apply_tree_item_changed(self, item, column, finalize_flags=True):
        is_folder = item.data(0, Qt.ItemDataRole.UserRole + 1)
        was_creating = bool(item.data(0, Qt.ItemDataRole.UserRole + 4))
        print(f"[DEBUG TOP] on_tree_item_changed called! is_folder = {is_folder!r}, text = {item.text(0)!r}")
        if is_folder is not None:
            self.binder_tree.blockSignals(True)
            try:
                old_rel_path = item.data(0, Qt.ItemDataRole.UserRole)
                if not old_rel_path: return

                parent_rel_path = os.path.dirname(old_rel_path)
                new_display_name = unicodedata.normalize(
                    "NFC", item.text(0).rstrip()
                )
                if new_display_name != item.text(0):
                    item.setText(0, new_display_name)

                # '📚 원고' 하위 항목 이름 변경 규칙 강제
                if old_rel_path.startswith("메인/원고/"):
                    if is_folder:
                        # 더 이상 이곳에 도달하지 않아야 하지만, 안전장치로 롤백만 수행 (팝업 제거)
                        item.setText(0, os.path.basename(old_rel_path))

                        return
                    else:
                        old_base = os.path.basename(old_rel_path).replace(".txt", "")
                        match = re.match(r'^(\d+화)', old_base)
                        if match:
                            prefix = match.group(1)
                            if not new_display_name.startswith(prefix):
                                cleaned_suffix = re.sub(r'^\d+화\s*', '', new_display_name)
                                new_display_name = f"{prefix} {cleaned_suffix}".strip()
                                item.setText(0, new_display_name)

                if not is_folder:
                    new_name = new_display_name + ".txt"
                else:
                    new_name = new_display_name

                try:
                    new_name = normalize_local_entry_name(new_name)
                except LocalProjectPathError:
                    old_display_name = os.path.basename(old_rel_path)
                    if not is_folder and old_display_name.endswith(".txt"):
                        old_display_name = old_display_name[:-4]
                    item.setText(0, old_display_name)
                    if was_creating:
                        if hasattr(self, "sync_manager"):
                            self.sync_manager.record_path_change(
                                old_rel_path, old_rel_path
                            )
                        self._finish_tree_item_creation(item)
                        self.save_tree_order()
                    if finalize_flags:
                        self._finish_item_name_edit(item, is_folder)
                    self._show_temporary_invalid_name_message()
                    return

                new_rel_path = os.path.join(parent_rel_path, new_name).replace("\\", "/") if parent_rel_path else new_name

                if old_rel_path == new_rel_path:
                    if was_creating and hasattr(self, "sync_manager"):
                        self.sync_manager.record_path_change(old_rel_path, new_rel_path)
                    if was_creating:
                        self._finish_tree_item_creation(item)
                        self.save_tree_order()
                    if finalize_flags:
                        self._finish_item_name_edit(item, is_folder)
                    def _set_active():
                        try:
                            self.binder_tree.setCurrentItem(item)
                            item.setSelected(True)
                            from PyQt6.QtWidgets import QApplication
                            if not QApplication.activePopupWidget():
                                self.binder_tree.setFocus()
                        except RuntimeError:
                            pass
                    from PyQt6.QtCore import QTimer
                    QTimer.singleShot(10, _set_active)
                    return

                print(f"[DEBUG] rename target: {old_rel_path} -> {new_rel_path}")
                print(f"[DEBUG] writing_root_path = {self.wpm.writing_root_path!r}")
                print(f"[DEBUG] wpm object id = {id(self.wpm)}")

                if self.wpm.writing_root_path:
                    filesystem_changed = False
                    try:
                        sync_manager = getattr(self, "sync_manager", None)
                        mutation_gate = (
                            sync_manager.local_structure_mutation()
                            if sync_manager is not None
                            else nullcontext()
                        )
                        with mutation_gate:
                            self.wpm.rename_item(old_rel_path, new_rel_path)
                            filesystem_changed = True
                            path_operations = []
                            if sync_manager is not None:
                                path_operations = sync_manager.record_path_change(
                                    old_rel_path, new_rel_path, retry=False
                                )
                            if hasattr(self, "controller"):
                                self.controller.rename_path(old_rel_path, new_rel_path)
                            item.setData(0, Qt.ItemDataRole.UserRole, new_rel_path)
                            if was_creating:
                                self._finish_tree_item_creation(item)
                            print(f"[DEBUG] setData 직후 재확인: {item.data(0, Qt.ItemDataRole.UserRole)!r}")

                            # 1. 탭 제목 실시간 갱신 및 현재 열린 파일 경로 갱신
                            def update_loaded_file(attr_name, label_widget):
                                current_path = getattr(self, attr_name, None)
                                if current_path:
                                    if current_path == old_rel_path:
                                        setattr(self, attr_name, new_rel_path)
                                        base_name = os.path.basename(new_rel_path).replace(".txt", "")
                                        label_widget.setText(f"{base_name}")
                                        if attr_name == 'current_loaded_file_left':
                                            self._original_text_l = f"{base_name}"
                                        else:
                                            self._original_text_r = f"{base_name}"
                                    elif current_path.startswith(old_rel_path + "/"):
                                        new_cur_path = current_path.replace(old_rel_path, new_rel_path, 1)
                                        setattr(self, attr_name, new_cur_path)
                                        base_name = os.path.basename(new_cur_path).replace(".txt", "")
                                        label_widget.setText(f"{base_name}")
                                        if attr_name == 'current_loaded_file_left':
                                            self._original_text_l = f"{base_name}"
                                        else:
                                            self._original_text_r = f"{base_name}"

                            update_loaded_file('current_loaded_file_left', self.lbl_current_doc)
                            update_loaded_file('current_loaded_file_right', self.lbl_r_doc)

                            # 2. 서버 동기화 버전 캐시(self.loaded_versions) 동기화
                            new_versions = {}
                            for path, version in list(self.loaded_versions.items()):
                                if path == old_rel_path:
                                    new_versions[new_rel_path] = self.loaded_versions.pop(path)
                                elif path.startswith(old_rel_path + "/"):
                                    new_path = path.replace(old_rel_path, new_rel_path, 1)
                                    new_versions[new_path] = self.loaded_versions.pop(path)
                            self.loaded_versions.update(new_versions)

                            # 3. 폴더라면 트리에 표시된 모든 자식 경로도 같은 임계구역에서 갱신
                            if is_folder:
                                def update_children_user_role(parent_item, old_base, new_base):
                                    for i in range(parent_item.childCount()):
                                        child = parent_item.child(i)
                                        child_path = child.data(0, Qt.ItemDataRole.UserRole)
                                        if child_path and child_path.startswith(old_base + "/"):
                                            new_child_path = child_path.replace(old_base, new_base, 1)
                                            child.setData(0, Qt.ItemDataRole.UserRole, new_child_path)
                                            update_children_user_role(child, old_base, new_base)
                                update_children_user_role(item, old_rel_path, new_rel_path)
                                if sync_manager is not None:
                                    sync_manager.record_folder_rename_intent(
                                        old_rel_path, new_rel_path
                                    )

                            self.save_tree_order_for_rename(
                                old_rel_path,
                                new_rel_path,
                                retry=False,
                                operations=path_operations,
                            )

                        if sync_manager is not None:
                            # SQLite enqueue is durable before any network worker starts.
                            sync_manager.retry_pending_syncs()
                        if finalize_flags:
                            self._finish_item_name_edit(item, is_folder)

                        def _set_active():
                            try:
                                self.binder_tree.setCurrentItem(item)
                                item.setSelected(True)
                                from PyQt6.QtWidgets import QApplication
                                if not QApplication.activePopupWidget():
                                    self.binder_tree.setFocus()
                            except RuntimeError:
                                pass
                        from PyQt6.QtCore import QTimer
                        QTimer.singleShot(10, _set_active)
                    except Exception as e:
                        if filesystem_changed:
                            self._rollback_structure_path(
                                new_rel_path, old_rel_path
                            )
                        if was_creating:
                            self._finish_tree_item_creation(item)
                        print(f"[DEBUG] rename_item 실패! old={old_rel_path!r}, new={new_rel_path!r}, error={e!r}")
                        QMessageBox.warning(self, "오류", f"이름 변경 실패: {e}")
                        item.setText(0, os.path.basename(old_rel_path).replace(".txt", "") if not is_folder else os.path.basename(old_rel_path))
                        if finalize_flags:
                            self._finish_item_name_edit(item, is_folder)

                        def _set_active():
                            try:
                                self.binder_tree.setCurrentItem(item)
                                item.setSelected(True)
                                from PyQt6.QtWidgets import QApplication
                                if not QApplication.activePopupWidget():
                                    self.binder_tree.setFocus()
                            except RuntimeError:
                                pass
                        from PyQt6.QtCore import QTimer
                        QTimer.singleShot(10, _set_active)
            finally:
                self.binder_tree.blockSignals(False)

    def add_volume(self):
        sync_manager = getattr(self, "sync_manager", None)
        mutation_gate = (
            sync_manager.local_structure_mutation()
            if sync_manager is not None
            else nullcontext()
        )
        with mutation_gate:
            new_vol_name = self._create_binder_volume()
            if not new_vol_name:
                return

            # Rebuild first so the new volume and all 25 chapters are present in
            # the binder. Publish a folder-only binder skeleton before the
            # chapter documents, then publish the complete order after all
            # document UUIDs have reached the server. A peer can therefore add
            # its next volumes without basing that edit on an older tree.
            self.load_tree_data()
            document_operations = []
            if sync_manager is not None:
                match = re.fullmatch(r"(\d+)권", new_vol_name)
                if not match:
                    raise ValueError("INVALID_VOLUME_NAME")
                start_chapter = (int(match.group(1)) - 1) * 25 + 1
                chapter_names = [
                    f"{chapter_number:03d}화.txt"
                    for chapter_number in range(
                        start_chapter, start_chapter + 25
                    )
                ]
                volume_path = f"메인/원고/{new_vol_name}"
                tree_order = WritingTreeMixin._current_tree_order_snapshot(self)
                manuscript_path = os.path.join(
                    self.wpm.writing_root_path, "메인", "원고"
                )
                disk_volumes = [
                    name for name in os.listdir(manuscript_path)
                    if os.path.isdir(os.path.join(manuscript_path, name))
                ]
                saved_volumes = tree_order.get("메인/원고", [])
                volume_order = [
                    name for name in saved_volumes if name in disk_volumes
                ]
                volume_order.extend(sorted(
                    (name for name in disk_volumes if name not in volume_order),
                    key=lambda name: WritingTreeMixin._natural_sort_key(
                        self, name
                    ),
                ))
                tree_order["메인/원고"] = volume_order
                tree_order[volume_path] = chapter_names
                if not sync_manager._uses_contract_structure():
                    folder_only_order = copy.deepcopy(tree_order)
                    for parent_path in list(folder_only_order):
                        if re.fullmatch(r"메인/원고/\d+권", parent_path):
                            folder_only_order[parent_path] = []
                    sync_manager.record_tree_order(
                        folder_only_order, retry=False
                    )
                folder_operations = sync_manager.record_path_change(
                    volume_path, volume_path, retry=False
                )
                for folder_operation in folder_operations:
                    intents = folder_operation.get("contract_structure_intents")
                    if intents:
                        sync_manager.queue_contract_structure_intents(
                            intents, retry=False
                        )
                for chapter_number in range(start_chapter, start_chapter + 25):
                    chapter_path = (
                        f"메인/원고/{new_vol_name}/{chapter_number:03d}화.txt"
                    )
                    operation = sync_manager.record_created_document(
                        chapter_path, retry=False
                    )
                    if operation is None:
                        raise RuntimeError("DOCUMENT_QUEUE_FAILED")
                    document_operations.append(operation)
                # Do not depend on lazy Qt child materialization for the durable
                # barrier.  The new volume identity and its exact 25 chapters
                # are known from the filesystem transaction above.
                self.defer_tree_order_until_operations(
                    document_operations, tree_order
                )
            else:
                self.save_tree_order()

        if sync_manager is not None:
            # Never start a network worker while the structure gate is held.
            sync_manager.retry_pending_syncs()

        QMessageBox.information(self, "권 추가", f"{new_vol_name}이 생성되고 25화 분량 파일이 만들어졌습니다.")
        self._open_new_volume_chapters(new_vol_name)

        # 새 권수 아이템 포커스 강제
        def _set_active():
            for i in range(self.binder_tree.topLevelItemCount()):
                root_item = self.binder_tree.topLevelItem(i)
                if root_item.text(0) == "📚 원고":
                    for j in range(root_item.childCount()):
                        if root_item.child(j).text(0) == new_vol_name:
                            new_item = root_item.child(j)
                            self.binder_tree.setCurrentItem(new_item)
                            new_item.setSelected(True)
                            self.binder_tree.setFocus()
                            break
                    break
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(10, _set_active)

    def _open_new_volume_chapters(self, volume_name):
        """Open the first new chapter, and its successor when split view is on."""
        match = re.fullmatch(r"(\d+)권", volume_name)
        if not match:
            return

        first_chapter_number = (int(match.group(1)) - 1) * 25 + 1
        first_chapter = f"메인/원고/{volume_name}/{first_chapter_number:03d}화.txt"

        self.set_active_editor(self.left_editor)
        self._open_file_by_path(first_chapter)

        if self.btn_toggle_split.isChecked() and self.right_editor_container.isVisible():
            next_chapter = (
                f"메인/원고/{volume_name}/{first_chapter_number + 1:03d}화.txt"
            )
            self.set_active_editor(self.right_editor)
            self._open_file_by_path(next_chapter)

        # The newly-created volume always leaves the main (left) editor active.
        self.set_active_editor(self.left_editor)

    def on_tree_current_item_changed(self, current, previous):
        if not current:
            return
        if bool(current.data(0, Qt.ItemDataRole.UserRole + 4)):
            return
        rel_path = current.data(0, Qt.ItemDataRole.UserRole)
        print(f"[DEBUG] 클릭 시 읽어온 경로: {rel_path!r}")
        if not rel_path:
            return

        self._open_file_by_path(rel_path)
