import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from webapp.ui.layout import HTML_TEMPLATE
from webapp.ui.logged_in_script import LOGGED_IN_JS
from webapp.ui.logged_in_template import LOGGED_IN_CONTENT


def test_message_renderer_supports_voice_file_and_reference_items():
    assert "\\.(silk|amr|wav|mp3|m4a|aac|ogg|oga|flac)" in LOGGED_IN_JS
    assert 'class="chat-file"' in LOGGED_IN_JS
    assert 'class="quote-block"' in LOGGED_IN_JS
    assert "SILK 语音已发送" in LOGGED_IN_JS


def test_file_picker_allows_general_attachments():
    assert 'id="mediaUpload"' in LOGGED_IN_CONTENT
    assert 'accept="image/*,video/*,audio/*"' not in LOGGED_IN_CONTENT


def test_delivery_panel_exposes_consecutive_send_count():
    assert 'id="currentConsecutiveCount"' in LOGGED_IN_CONTENT
    assert "consecutive_send_count" in LOGGED_IN_JS
    assert "连发" in LOGGED_IN_JS


def test_media_styles_include_file_and_quote_blocks():
    assert ".msg-bubble .chat-file" in HTML_TEMPLATE
    assert ".msg-bubble .quote-block" in HTML_TEMPLATE
