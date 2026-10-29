"""common — invest-radar 跨模块共用基础设施。

仅放置两模块（national_team / qdii）真正都会用到的底层工具：
- http  ：代理感知抓取（本地地址自动绕过代理、公网沿用环境代理、GBK/JSON）。
- lock  ：跨进程文件锁（调度任务互斥，Linux 用 fcntl，Windows 降级）。

日志 / 市场日历 / 调度基类随 qdii 模块开发时按需补入，不为一次性逻辑造抽象。
"""
from .http import fetch_text, fetch_json
from .lock import FileLock

__all__ = ["fetch_text", "fetch_json", "FileLock"]
