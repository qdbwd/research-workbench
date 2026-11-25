"""浏览器协议异常统一映射为固定中文提示，避免外部响应泄露路径或凭据。"""

MESSAGES = {
    "browser_disabled": "当前任务未启用受控动态浏览器取证。",
    "browser_unavailable": "动态浏览器暂不可用，请检查部署状态和并发上限。",
    "cdp_unreachable": "无法连接专用本机浏览器，请检查调试窗口和部署配置。",
    "local_browser_busy": "另一项根任务正在独占本机浏览器，可稍后开始新任务或选择容器隔离模式。",
    "origin_blocked": "当前地址超出受控浏览器的访问范围。",
    "stale_observation": "页面观察已失效，请重新观察后再选择控件。",
    "control_blocked": "此控件涉及提交、编辑、上传或其他未开放的操作。",
    "browser_timeout": "浏览器动作超时，会话已关闭。继续时请重新导航。",
    "browser_disconnected": "浏览器连接已中断，会话已关闭。继续时请重新导航。",
    "browser_session_expired": "这个运行没有可用页面，请先重新导航。",
    "browser_action_limit": "该根任务的浏览器动作次数已用完。",
    "browser_empty_content": "当前页面没有足以作为证据的可见正文。",
}


class BrowserToolError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        self.message = MESSAGES[code]
        super().__init__(f"{code}: {self.message}")

    def result(self) -> dict:
        return {"status": "failed", "error_code": self.code, "message": self.message}

