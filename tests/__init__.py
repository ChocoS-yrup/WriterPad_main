"""UI를 열지 않고 파일 시스템 동작만 검증하는 회귀 테스트 패키지."""

import os
import tempfile


# Source-level tests must never mix synthetic sync failures with the user's
# real diagnostic history.
os.environ.setdefault(
    "ANTIGRAVITY_APP_DATA_DIR",
    os.path.join(tempfile.gettempdir(), "AntigravityWriterTests", str(os.getpid())),
)
