"""common/lock.py — 跨进程文件锁，用于调度任务互斥（invest-radar 各模块各自独立锁路径）。

- Linux/macOS：fcntl.flock（强互斥，推荐部署平台）。
- Windows：降级为「锁文件 + PID 标记」，非强互斥但足以防止同机重复调度。
"""
import os


class FileLock:
    def __init__(self, path):
        self.path = path
        self._fh = None

    def acquire(self):
        parent = os.path.dirname(self.path) or "."
        os.makedirs(parent, exist_ok=True)
        try:
            import fcntl
            self._fh = open(self.path, "w")
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except ImportError:
            # Windows：若锁文件已存在视为被占用
            if os.path.exists(self.path):
                return False
            self._fh = open(self.path, "w")
            self._fh.write(str(os.getpid()))
            self._fh.flush()
            return True
        except (OSError, IOError):
            if self._fh:
                self._fh.close()
                self._fh = None
            return False

    def release(self):
        if self._fh:
            try:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            self._fh.close()
            self._fh = None
        try:
            if os.path.exists(self.path):
                os.remove(self.path)
        except OSError:
            pass

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError("无法获取文件锁: " + self.path)
        return self

    def __exit__(self, *exc):
        self.release()
