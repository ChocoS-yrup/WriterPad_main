from abc import ABC, abstractmethod
from dataclasses import dataclass
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class ModelSpec:
    """UI 표시, 실제 API 호출, 호환성 상태를 함께 표현하는 모델 정보."""
    display_name: str
    provider: str
    model_id: str
    status: str = "추천"
    recommended: bool = False
    supports_text_generation: bool = True

    @property
    def selection_key(self) -> str:
        """프로젝트 설정에 저장하는 표시명과 독립적인 안정 식별자."""
        return f"{self.provider}|{self.model_id}"

    @classmethod
    def from_dict(cls, raw: dict) -> "ModelSpec":
        required = ("display_name", "provider", "model_id")
        missing = [key for key in required if not raw.get(key)]
        if missing:
            raise ValueError(f"모델 카탈로그에 필수 값이 없습니다: {', '.join(missing)}")
        return cls(
            display_name=str(raw["display_name"]),
            provider=str(raw["provider"]),
            model_id=str(raw["model_id"]),
            status=str(raw.get("status", "추천")),
            recommended=bool(raw.get("recommended", False)),
            supports_text_generation=bool(raw.get("supports_text_generation", True)),
        )


SUPPORTED_PROVIDERS = {"Gemini", "Claude", "OpenAI"}
CATALOG_FILE_NAME = "model_catalog.json"


def _resource_dir() -> Path:
    """개발 실행과 PyInstaller 실행에서 모두 번들 리소스 위치를 찾는다."""
    import sys
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def _fallback_catalog() -> tuple[ModelSpec, ...]:
    """카탈로그 파일을 읽지 못해도 기존 앱이 동작하도록 하는 최소 안전 목록."""
    return (
        ModelSpec("Gemini 3.1 Pro", "Gemini", "gemini-3.1-pro-preview", "미리보기", True),
        ModelSpec("Claude Opus 4.8", "Claude", "claude-opus-4-8", "추천", True),
        ModelSpec("GPT-4o", "OpenAI", "gpt-4o", "기존 모델"),
        ModelSpec("GPT-5.6 Sol", "OpenAI", "gpt-5.6-sol", "추천", True),
        ModelSpec("GPT-5.6 Terra", "OpenAI", "gpt-5.6-terra", "추천", True),
        ModelSpec("GPT-5.6 Luna", "OpenAI", "gpt-5.6-luna", "추천", True),
    )


def load_model_catalog(path: str | os.PathLike | None = None) -> tuple[ModelSpec, ...]:
    """추천 모델 목록을 코드가 아닌 설정 파일에서 불러온다."""
    catalog_path = Path(path) if path else _resource_dir() / CATALOG_FILE_NAME
    try:
        with catalog_path.open("r", encoding="utf-8") as file:
            raw_models = json.load(file).get("models", [])
        models = tuple(ModelSpec.from_dict(raw) for raw in raw_models)
        if not models:
            raise ValueError("모델 목록이 비어 있습니다.")
        if any(model.provider not in SUPPORTED_PROVIDERS for model in models):
            raise ValueError("지원하지 않는 제공자가 포함되어 있습니다.")
        return models
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        print(f"모델 카탈로그 로드 실패, 기본 목록을 사용합니다: {error}")
        return _fallback_catalog()


# 표시명과 API 모델 ID가 달라질 수 있으므로, 호출 코드에서 표시명을 직접 해석하지 않는다.
MODEL_CATALOG = load_model_catalog()
MODEL_BY_DISPLAY_NAME = {model.display_name: model for model in MODEL_CATALOG}
MODEL_BY_SELECTION_KEY = {model.selection_key: model for model in MODEL_CATALOG}
DEFAULT_MODEL_DISPLAY_NAMES = tuple(model.display_name for model in MODEL_CATALOG)

# 과거 설정은 실제로 호출되던 모델과 같은 의미의 표시명으로 한 번만 정규화한다.
# 예: GPT-5.5는 이전 구현에서도 gpt-4o를 호출했으므로 사용자 설정을 안전하게 이어받는다.
LEGACY_MODEL_NAMES = {
    "Gemini 3.1 Pro (확장모드)": "Gemini 3.1 Pro",
    "Claude Opus 4.8 높음": "Claude Opus 4.8",
    "GPT-5.5": "GPT-4o",
}


def normalize_model_selection(model_name: str) -> str:
    """저장된 이전 모델명을 현재의 표시명으로 변환한다."""
    return LEGACY_MODEL_NAMES.get(model_name, model_name)


def resolve_model_selection(model_name: str) -> ModelSpec:
    """표시명 또는 저장된 선택 키로부터 실제 API 호출 정보를 얻는다."""
    normalized_name = normalize_model_selection(model_name)
    model = MODEL_BY_DISPLAY_NAME.get(normalized_name) or MODEL_BY_SELECTION_KEY.get(normalized_name)
    if model:
        return model

    try:
        provider, model_id = normalized_name.split("|", 1)
    except ValueError:
        provider, model_id = "", ""
    if provider in SUPPORTED_PROVIDERS and model_id:
        # 계정에서 새로 발견한 모델은 다음 앱 실행 후에도 선택 키만으로 안전하게 복원한다.
        return ModelSpec(f"{provider} · {model_id}", provider, model_id, "계정 선택")

    raise ValueError(
        f"지원하지 않는 모델 설정입니다: {model_name}. "
        "설정에서 지원 모델을 다시 선택해 주세요."
    )


def _is_openai_text_model(model_id: str) -> bool:
    model_id = model_id.lower()
    unsupported_terms = ("image", "audio", "realtime", "transcribe", "tts", "embedding", "moderation", "codex")
    return model_id.startswith(("gpt-", "o1", "o3", "o4", "chatgpt-")) and not any(
        term in model_id for term in unsupported_terms
    )


def _http_json(url: str, headers: dict[str, str]) -> dict:
    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=12) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise RuntimeError(f"HTTP {error.code}") from error
    except URLError as error:
        raise RuntimeError("네트워크 연결에 실패했습니다.") from error


def discover_available_models(provider: str) -> list[ModelSpec]:
    """현재 API 키가 실제로 접근할 수 있는 텍스트 생성 모델만 조회한다."""
    from security_manager import SecurityManager

    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"지원하지 않는 제공자입니다: {provider}")
    api_key = SecurityManager.get_api_key(provider)
    if not api_key:
        raise ValueError(f"{provider} API 키를 먼저 저장해 주세요.")

    if provider == "OpenAI":
        payload = _http_json(
            "https://api.openai.com/v1/models",
            {"Authorization": f"Bearer {api_key}"},
        )
        model_ids = [item.get("id", "") for item in payload.get("data", [])]
        return [
            ModelSpec(model_id, provider, model_id, "계정 사용 가능")
            for model_id in model_ids
            if _is_openai_text_model(model_id)
        ]

    if provider == "Claude":
        query = urlencode({"limit": 100})
        payload = _http_json(
            f"https://api.anthropic.com/v1/models?{query}",
            {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        return [
            ModelSpec(item.get("display_name") or item["id"], provider, item["id"], "계정 사용 가능")
            for item in payload.get("data", [])
            if item.get("id")
        ]

    payload = _http_json(
        f"https://generativelanguage.googleapis.com/v1beta/models?{urlencode({'key': api_key})}",
        {},
    )
    models = []
    for item in payload.get("models", []):
        methods = item.get("supportedGenerationMethods", [])
        if "generateContent" not in methods:
            continue
        model_id = item.get("baseModelId") or item.get("name", "").removeprefix("models/")
        if model_id:
            models.append(ModelSpec(item.get("displayName") or model_id, provider, model_id, "계정 사용 가능"))
    return models

class LLMProvider(ABC):
    def _translate_error(self, e: Exception) -> str:
        error_msg = str(e).lower()
        if "api key" in error_msg or "unauthorized" in error_msg or "401" in error_msg or "key" in error_msg:
            return f"API 키가 유효하지 않거나 만료되었습니다.\n설정 탭에서 API 키를 확인해 주세요.\n(원본 에러: {str(e)})"
        if "rate limit" in error_msg or "429" in error_msg or "quota" in error_msg:
            return f"API 요청 한도를 초과했습니다. 잠시 후 다시 시도해 주세요.\n(원본 에러: {str(e)})"
        if "not found" in error_msg or "404" in error_msg:
            return f"요청하신 AI 모델을 찾을 수 없습니다. 지원되는 모델명인지 확인해 주세요.\n(원본 에러: {str(e)})"
        if "timeout" in error_msg or "timed out" in error_msg:
            return f"API 연결 시간이 초과되었습니다. 네트워크 상태를 확인하거나 잠시 후 다시 시도해 주세요.\n(원본 에러: {str(e)})"
        return f"AI 텍스트 생성 중 통신 오류가 발생했습니다.\n(원본 에러: {str(e)})"

    @abstractmethod
    def generate(self, messages: list, **kwargs) -> tuple:
        """
        AI 모델에 메시지 리스트를 전송하고 응답 텍스트를 반환합니다.
        :param messages: [{"role": "system"|"user"|"assistant", "content": "..."}, ...]
        """
        pass

    @abstractmethod
    def chat_stream(self, messages: list):
        """
        스트리밍 응답을 위한 메서드 (현재 미사용, 향후 확장용)
        """
        pass

class DummyLLMProvider(LLMProvider):
    def __init__(self, model_name="Dummy Model"):
        self.model_name = model_name

    def generate(self, messages: list, include_stats: bool = True, use_context_caching: bool = False, **kwargs) -> tuple:
        # 터미널 디버깅 출력 (슬라이딩 윈도우 생존 여부 확인)
        print("\n" + "="*60)
        caching_log = " (Context Caching 활성화)" if use_context_caching else ""
        print(f"📡 [DummyLLMProvider - {self.model_name}]{caching_log} 백그라운드 전달 완료 (현재 길이: {len(messages)})")
        for i, msg in enumerate(messages):
            role = msg.get("role", "")
            content = msg.get("content", "").replace('\n', ' ')
            preview = content if len(content) < 40 else content[:40] + "..."
            print(f"   [{i:02d}] {role.upper()}: {preview}")
        print("="*60 + "\n")

        # 실제 API 통신을 흉내내기 위한 3초 대기
        time.sleep(3)

        # 전달받은 메시지 목록을 분석 (디버깅/확인용 더미 응답)
        sys_prompt_len = 0
        user_msg_count = 0
        user_text_preview = ""
        
        for msg in messages:
            if msg["role"] == "system":
                sys_prompt_len += len(msg.get("content", ""))
            elif msg["role"] == "user":
                user_msg_count += 1
                if not user_text_preview:
                    content_str = msg.get("content", "")
                    content_clean = content_str.replace('\n', ' ')
                    
                    if len(content_clean) > 80:
                        user_text_preview = f"{len(content_str):,}자 / 앞부분: {content_clean[:30]}... 뒷부분: ...{content_clean[-30:]}"
                    else:
                        user_text_preview = f"{len(content_str):,}자 / 내용: {content_clean}"
                    
        dummy_text = f"이것은 더미 생성 결과입니다. (수신된 컨텍스트 일부: {user_text_preview})"
        
        if not include_stats:
            return dummy_text, 10, 20
                
        caching_alert = "[💡 Gemini Context Caching 적용됨: 토큰 비용 대폭 절감]\n" if use_context_caching else ""
        
        res = (f"[✨ AI 생성 결과]\n"
                f"{caching_alert}"
                f"사용 모델: {self.model_name}\n"
                f"전달받은 메시지 수: {len(messages)}개\n"
                f"시스템 프롬프트 길이: {sys_prompt_len}자\n"
                f"{dummy_text}")
        return res, 10, 20

    def chat_stream(self, messages: list):
        raise NotImplementedError("스트리밍 기능은 아직 지원하지 않습니다.")

class ClaudeProvider(LLMProvider):
    def __init__(self, api_key: str, model_id: str):
        self.api_key = api_key
        self.model_id = model_id if model_id else "claude-opus-4-8"

    def generate(self, messages: list, **kwargs) -> tuple:
        import anthropic
        
        client = anthropic.Anthropic(api_key=self.api_key)
        
        system_prompt = ""
        anthropic_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_prompt += msg["content"] + "\n"
            else:
                anthropic_messages.append({"role": msg["role"], "content": msg["content"]})
                
        try:
            full_text = ""
            with client.messages.stream(
                model=self.model_id,
                max_tokens=20000,
                system=system_prompt.strip(),
                messages=anthropic_messages
            ) as stream:
                for text in stream.text_stream:
                    full_text += text
            final_msg = stream.get_final_message()
            return full_text, final_msg.usage.input_tokens, final_msg.usage.output_tokens
        except Exception as e:
            raise RuntimeError(self._translate_error(e))

    def chat_stream(self, messages: list):
        raise NotImplementedError("스트리밍 기능은 아직 지원하지 않습니다.")

class OpenAIProvider(LLMProvider):
    def __init__(self, api_key: str, model_id: str):
        self.api_key = api_key
        self.model_id = model_id if model_id else "gpt-4o"

    def generate(self, messages: list, **kwargs) -> tuple:
        from openai import OpenAI
        
        client = OpenAI(api_key=self.api_key)
        
        try:
            response = client.chat.completions.create(
                model=self.model_id,
                messages=messages
            )
            return response.choices[0].message.content, response.usage.prompt_tokens, response.usage.completion_tokens
        except Exception as e:
            raise RuntimeError(self._translate_error(e))

    def chat_stream(self, messages: list):
        raise NotImplementedError("스트리밍 기능은 아직 지원하지 않습니다.")

class GeminiProvider(LLMProvider):
    def __init__(self, api_key: str, model_id: str):
        self.api_key = api_key
        self.model_id = model_id

    def generate(self, messages: list, **kwargs) -> tuple:
        from google import genai
            
        client = genai.Client(api_key=self.api_key)
        
        system_instruction = ""
        gemini_contents = []
        
        for msg in messages:
            if msg["role"] == "system":
                system_instruction += msg["content"] + "\n"
            else:
                gemini_contents.append({
                    "role": "model" if msg["role"] == "assistant" else "user",
                    "parts": [{"text": msg["content"]}]
                })
                
        config = {
            'temperature': 1.0,
            'max_output_tokens': 65536,
            'top_p': 0.95
        }
        
        # 3.1-pro-preview 모델의 경우 생각(thinking) 모드 설정 추가
        if "gemini-3.1-pro-preview" in self.model_id:
            config['thinking_config'] = {'thinking_level': 'high'}
            
        if system_instruction.strip():
            config["system_instruction"] = system_instruction.strip()

        try:
            response = client.models.generate_content(
                model=f"models/{self.model_id}",
                contents=gemini_contents,
                config=config
            )
            in_tok = response.usage_metadata.prompt_token_count if response.usage_metadata else 0
            out_tok = response.usage_metadata.candidates_token_count if response.usage_metadata else 0
            return response.text, in_tok, out_tok
        except Exception as e:
            raise RuntimeError(self._translate_error(e))

    def chat_stream(self, messages: list):
        raise NotImplementedError("스트리밍 기능은 아직 지원하지 않습니다.")


class LLMFactory:
    @staticmethod
    def get_provider(provider_type: str = "dummy") -> LLMProvider:
        """
        설정된 타입에 맞는 LLMProvider 인스턴스를 반환합니다.
        Lazy Loading: 워커가 실행될 때만 해당 모델의 API 키를 SecurityManager에서 가져옵니다.
        """
        from security_manager import SecurityManager
        
        if provider_type == "dummy":
            return DummyLLMProvider(provider_type)

        model = resolve_model_selection(provider_type)
        api_key = SecurityManager.get_api_key(model.provider)
        if not api_key:
            raise ValueError(
                f"[{model.provider}] 모델의 API 키가 설정되지 않았습니다. "
                "설정 탭에서 키를 입력해 주세요."
            )

        if model.provider == "Claude":
            return ClaudeProvider(api_key, model.model_id)
        if model.provider == "Gemini":
            return GeminiProvider(api_key, model.model_id)
        if model.provider == "OpenAI":
            return OpenAIProvider(api_key, model.model_id)

        raise ValueError(f"지원하지 않는 AI 제공자입니다: {model.provider}")
