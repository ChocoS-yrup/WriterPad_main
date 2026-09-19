"""Agreed preparation scope. No production activation or fixed test prose."""
from types import SimpleNamespace
import general_editor_plan as previous
from general_validation_plan import PROJECT_ID, PROJECT_NAME, STAGING_URL, DOCUMENT_ID, PARENT_ID, ORDER_ID, NAME, PATH, digest

PLAN_ID = 'normal-editor-single-document-20260913-v1'
INITIAL_REVISION = 6
INITIAL_CONTENT = previous.AUTO
DIRECTORY = 'normal-editor-20260913'


def snapshot_plan(revision, content):
    return SimpleNamespace(PROJECT_ID=PROJECT_ID, PROJECT_NAME=PROJECT_NAME, STAGING_URL=STAGING_URL,
        DOCUMENT_ID=DOCUMENT_ID, PARENT_ID=PARENT_ID, ORDER_ID=ORDER_ID, NAME=NAME, PATH=PATH,
        PLAN_ID=PLAN_ID, digest=digest, STAGES={revision-1:SimpleNamespace(content=content)})


def validate_text(text):
    from general_validation_service import require
    require(isinstance(text,str) and text != '', 'NORMAL_EMPTY_BODY_PRESERVED')
    require('\r' not in text and '\x00' not in text, 'NORMAL_TEXT_ENCODING_CHANGED')
    text.encode('utf-8')
    return text
