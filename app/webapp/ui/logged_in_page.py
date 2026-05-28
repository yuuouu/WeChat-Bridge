from __future__ import annotations

"""已登录页面入口。"""

from webapp.ui.layout import HTML_TEMPLATE
from webapp.ui.logged_in_script import LOGGED_IN_JS
from webapp.ui.logged_in_template import LOGGED_IN_CONTENT


def render_logged_in():
    """已登录聊天界面。"""
    return HTML_TEMPLATE % (LOGGED_IN_CONTENT, LOGGED_IN_JS)
