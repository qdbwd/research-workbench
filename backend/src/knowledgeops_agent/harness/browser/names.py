"""稳定的内建工具名，供权限、预算与界面目录共用。"""

BROWSER_NAMES = {
    "browser_navigate": "导航到网页",
    "browser_observe": "观察页面",
    "browser_click": "点击取证控件",
    "browser_wait": "等待页面内容",
    "browser_back": "返回上一个页面",
    "browser_capture": "采集网页证据",
    "browser_close": "关闭本次浏览器会话",
}
BROWSER_IDS = frozenset(f"builtin:{name}" for name in BROWSER_NAMES)
