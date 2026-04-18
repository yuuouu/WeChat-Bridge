"""Web UI 页面渲染兼容门面。"""

from webapp.ui.auth_page import render_auth_page
from webapp.ui.logged_in_page import render_logged_in
from webapp.ui.qr_page import render_qr_page

__all__ = ["render_auth_page", "render_logged_in", "render_qr_page"]