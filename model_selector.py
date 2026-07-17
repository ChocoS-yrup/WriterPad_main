from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QWidget

from llm_provider import ModelSpec, load_model_catalog, resolve_model_selection


class ModelDiscoveryWorker(QThread):
    """모델 목록 조회가 GUI를 멈추지 않도록 제공자별 API 요청을 백그라운드에서 수행한다."""
    discoveryFinished = pyqtSignal(object, object)  # list[ModelSpec], list[str]

    def run(self):
        from llm_provider import SUPPORTED_PROVIDERS, discover_available_models

        discovered = []
        errors = []
        for provider in sorted(SUPPORTED_PROVIDERS):
            try:
                discovered.extend(discover_available_models(provider))
            except Exception as error:
                errors.append(f"{provider}: {error}")
        self.discoveryFinished.emit(discovered, errors)


class ModelSelector(QWidget):
    """요약, 초안, 평가 모델을 각각 선택할 수 있는 위젯"""
    refreshStateChanged = pyqtSignal(str)
    modelsUpdated = pyqtSignal(object)

    def __init__(self, pm):
        super().__init__()
        self.pm = pm
        self.model_specs = list(load_model_catalog())
        self._model_worker = None
        self.setMinimumWidth(850) # 찌그러짐 방지
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(15)

        self.cb_summary = QComboBox()
        self.cb_summary.setMinimumWidth(200)
        self.cb_draft = QComboBox()
        self.cb_draft.setMinimumWidth(200)
        self.cb_eval = QComboBox()
        self.cb_eval.setMinimumWidth(200)
        self._combos = {
            "model_summary": self.cb_summary,
            "model_draft": self.cb_draft,
            "model_eval": self.cb_eval,
        }
        self._rebuild_combos()
        for setting_key, combo in self._combos.items():
            combo.currentIndexChanged.connect(
                lambda _index, key=setting_key, current_combo=combo: self._save_model_selection(key, current_combo)
            )

        self.btn_refresh_models = QPushButton("🔄 사용 가능 모델 새로고침")
        self.btn_refresh_models.setObjectName("DarkButton")
        self.btn_refresh_models.setToolTip("저장된 API 키로 각 제공자의 텍스트 생성 모델을 조회합니다.")
        self.btn_refresh_models.clicked.connect(self.refresh_account_models)

        layout.addWidget(QLabel("요약 모델:"))
        layout.addWidget(self.cb_summary)
        layout.addSpacing(30)
        layout.addWidget(QLabel("초안 모델:"))
        layout.addWidget(self.cb_draft)
        layout.addSpacing(30)
        layout.addWidget(QLabel("평가 모델:"))
        layout.addWidget(self.cb_eval)
        layout.addWidget(self.btn_refresh_models)
        layout.addStretch()

        self.setLayout(layout)

    @staticmethod
    def _label_for(model: ModelSpec) -> str:
        label = f"{model.provider} · {model.display_name}"
        if model.status in {"추천", "계정 사용 가능"}:
            return label
        return f"{label} ({model.status})"

    def _saved_selection_key(self, setting_key: str, default_display_name: str) -> str:
        saved_value = self.pm.get_project_setting(setting_key, default_display_name)
        try:
            model = resolve_model_selection(saved_value)
        except ValueError:
            model = resolve_model_selection(default_display_name)
        if saved_value != model.selection_key:
            self.pm.set_project_setting(setting_key, model.selection_key)
        return model.selection_key

    def _ensure_selected_models_are_visible(self, selections: dict[str, str]):
        known_keys = {model.selection_key for model in self.model_specs}
        for selection_key in selections.values():
            if selection_key in known_keys:
                continue
            try:
                model = resolve_model_selection(selection_key)
            except ValueError:
                continue
            self.model_specs.append(model)
            known_keys.add(model.selection_key)

    def _rebuild_combos(self):
        defaults = {
            "model_summary": "Gemini 3.1 Pro",
            "model_draft": "Claude Opus 4.8",
            "model_eval": "Gemini 3.1 Pro",
        }
        selections = {
            setting_key: self._saved_selection_key(setting_key, defaults[setting_key])
            for setting_key in self._combos
        }
        self._ensure_selected_models_are_visible(selections)

        provider_order = {"Gemini": 0, "Claude": 1, "OpenAI": 2}
        ordered_models = sorted(
            self.model_specs,
            key=lambda model: (provider_order.get(model.provider, 99), not model.recommended, model.display_name.lower()),
        )
        for setting_key, combo in self._combos.items():
            combo.blockSignals(True)
            combo.clear()
            for model in ordered_models:
                combo.addItem(self._label_for(model), model.selection_key)
                index = combo.count() - 1
                combo.setItemData(index, f"{model.provider} · {model.status} · {model.model_id}", Qt.ItemDataRole.ToolTipRole)
            selected_index = combo.findData(selections[setting_key])
            combo.setCurrentIndex(selected_index if selected_index >= 0 else 0)
            combo.blockSignals(False)

    def _save_model_selection(self, setting_key: str, combo: QComboBox):
        selection_key = combo.currentData()
        if selection_key:
            self.pm.set_project_setting(setting_key, selection_key)

    def current_model_display_name(self, combo: QComboBox) -> str:
        """비용 기록은 장식된 콤보 텍스트가 아닌 정규 모델명으로 남긴다."""
        try:
            return resolve_model_selection(combo.currentData()).display_name
        except ValueError:
            return combo.currentText()

    def refresh_account_models(self):
        if self._model_worker and self._model_worker.isRunning():
            return
        self.btn_refresh_models.setEnabled(False)
        self.btn_refresh_models.setText("모델 조회 중...")
        self.refreshStateChanged.emit("각 제공자의 계정 사용 가능 모델을 조회 중입니다...")
        self._model_worker = ModelDiscoveryWorker(self)
        self._model_worker.discoveryFinished.connect(self._on_models_discovered)
        self._model_worker.discoveryFinished.connect(self._model_worker.deleteLater)
        self._model_worker.start()

    def _on_models_discovered(self, discovered: list, errors: list):
        existing = {model.selection_key: model for model in self.model_specs}
        for model in discovered:
            if model.supports_text_generation:
                existing[model.selection_key] = model
        self.model_specs = list(existing.values())
        self._rebuild_combos()
        self.btn_refresh_models.setEnabled(True)
        self.btn_refresh_models.setText("🔄 사용 가능 모델 새로고침")

        if discovered:
            message = f"텍스트 생성 모델 {len(discovered)}개를 확인했습니다."
        else:
            message = "사용 가능한 모델을 찾지 못했습니다. API 키와 네트워크를 확인해 주세요."
        if errors:
            message += " " + " / ".join(errors)
        self.refreshStateChanged.emit(message)
        self.modelsUpdated.emit(self.model_specs)
