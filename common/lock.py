"""common/lock.py — 跨进程文件锁，用于调度任务互斥（invest-radar 各模块各自独立锁路径）。

- Linux/macOS：`fcntl.flock` 强互斥（推荐部署平台）。
- Windows：`msvcrt.locking` 强互斥（LK_NBLCK，非阻塞）。两个 API 都靠 OS
  释放句柄，进程崩溃时锁自动解除。
- 兜底路径（既无 fcntl 也无 msvcrt，极少见）：`O_CREAT|O_EXCL` 独占创建 +
  PID 存活/年龄检测，防止前一次崩溃把后续所有调度永久挡在门外。

⚠️ 2026-10-30 修复：旧 Windows 分支用 `os.path.exists → return False`，
既没有 TOCTOU 保护（两个进程可同时通过 exists() 检查），也没有陈旧锁回收
（进程崩溃留下的锁文件会挡住之后每一次调度，直到用户手动删除文件）。
现在 msvcrt 走真正的 OS 级互斥，兜底分支也带 PID 探活与超时清理。
"""
import os
import errno
import time

# fcntl / msvcrt 各自可用性
try:
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover - 平台相关
    fcntl = None

try:
    import msvcrt  # type: ignore
except ImportError:
    msvcrt = None

# 兜底锁的“陈旧判定”阈值（秒）：进程被 kill 后 PID 探活也可能失败（Windows
# PID 会被系统回收后重用），所以额外用文件 mtime 判定。设 6 小时覆盖最慢的
# 全量同步任务（SCHED_TIMEOUT 默认 1800s）。
_STALE_SEC = int(os.environ.get("LOCK_STALE_SEC", "21600"))


def _pid_alive(pid: int) -> bool:
    """跨平台 PID 存活检测。

    ⚠️ 2026-10-30 修（Linux CI 首次跑到就暴露）：旧实现把 OSError 一把 catch
    返回 True，而 Linux 上 os.kill(不存在的 PID, 0) 抛的是 ProcessLookupError
    （OSError 的子类），被误判成"存活"，导致兜底分支永远收不回陈旧锁 →
    `test_fallback_stale_pid_reclaimed` 在 ubuntu-latest 上 FAIL。
    Windows 走 ctypes.OpenProcess 那条路径所以本地一直是绿的。
    语义：
      ProcessLookupError → 进程不存在 → False
      PermissionError    → 进程存在但不属于当前用户 → 视为存活 → True
      其它 OSError       → 保守视为存活，宁可让 mtime 陈旧判定兜底
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            import ctypes.wintypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.windll.kernel32
            h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return False
            try:
                code = ctypes.wintypes.DWORD()
                if kernel32.GetExitCodeProcess(h, ctypes.byref(code)):
                    return code.value == STILL_ACTIVE
                return True
            finally:
                kernel32.CloseHandle(h)
        except Exception:
            return False
    # POSIX
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


class FileLock:
    """非阻塞跨进程文件锁。acquire() 返回 bool；上下文管理器拿不到锁会抛。

    使用同一份 lock 文件多次跨进程访问：flock/msvcrt 是内核级独占，进程退出
    时自动释放；不会像 O_EXCL 那样留下需要人工清理的锁文件。
    """

    def __init__(self, path):
        self.path = path
        self._fh = None
        self._backend = None  # "fcntl" | "msvcrt" | "fallback"

    def acquire(self):
        parent = os.path.dirname(self.path) or "."
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError:
            pass

        if fcntl is not None:
            return self._acquire_fcntl()
        if msvcrt is not None:
            return self._acquire_msvcrt()
        return self._acquire_fallback()

    # ---- Linux / macOS ----
    def _acquire_fcntl(self):
        try:
            self._fh = open(self.path, "a+b")
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            # 写 PID 便于人工排查（不参与互斥判定）
            self._write_marker()
            self._backend = "fcntl"
            return True
        except (OSError, IOError):
            self._close_fh()
            return False

    # ---- Windows 主路径：msvcrt 区域锁 ----
    def _acquire_msvcrt(self):
        try:
            # 用 r+b / a+b 打开，避免 O_TRUNC 洗掉别的进程已经写好的 PID 标记
            self._fh = open(self.path, "a+b")
            # msvcrt.locking 需要至少锁 1 字节；从文件头开始，非阻塞
            self._fh.seek(0)
            msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
            self._write_marker()
            self._backend = "msvcrt"
            return True
        except OSError:
            # LK_NBLCK 拿不到锁 → OSError；关闭句柄（同时释放锁）
            self._close_fh()
            return False

    # ---- 兜底：既没 fcntl 也没 msvcrt（如 Cygwin / WASM / 未装扩展） ----
    def _acquire_fallback(self):
        """O_CREAT|O_EXCL 独占创建 + PID 探活 + 年龄判定。"""
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except OSError as e:
            if e.errno not in (errno.EEXIST, errno.EACCES):
                raise
            if not self._is_stale():
                return False
            # 陈旧锁：PID 已死或超过 _STALE_SEC → 强制回收
            try:
                os.remove(self.path)
            except OSError:
                return False
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except OSError:
                return False
        fh = os.fdopen(fd, "wb")
        fh.write(("%d %d" % (os.getpid(), int(time.time()))).encode("ascii"))
        fh.flush()
        fh.close()
        # 兜底模式只保留 path，release 时删文件
        self._fh = None
        self._backend = "fallback"
        return True

    def _is_stale(self):
        try:
            with open(self.path, "rb") as f:
                raw = f.read().decode("ascii", "replace").strip()
        except OSError:
            return True
        parts = raw.split()
        if parts:
            try:
                pid = int(parts[0])
                if pid != os.getpid() and not _pid_alive(pid):
                    return True
            except ValueError:
                pass
        try:
            age = time.time() - os.path.getmtime(self.path)
            return age > _STALE_SEC
        except OSError:
            return True

    def _write_marker(self):
        """尽力写 PID+时间到文件头，供人工排查。失败不影响互斥语义。"""
        if not self._fh:
            return
        try:
            self._fh.seek(0)
            self._fh.truncate(0)
            self._fh.write(("%d %d" % (os.getpid(), int(time.time()))).encode("ascii"))
            self._fh.flush()
        except OSError:
            pass

    def _close_fh(self):
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None

    def release(self):
        if self._backend == "msvcrt" and self._fh is not None:
            try:
                self._fh.seek(0)
                # msvcrt 只暴露 locking()，解锁靠 mode=msvcrt.LK_UNLCK；
                # 旧写法 msvcrt.unlocking 不存在，AttributeError 会让 release
                # 抛异常并泄漏句柄（Windows 兜底场景踩到过）
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            except (OSError, AttributeError):
                pass
        elif self._backend == "fcntl" and self._fh is not None:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            except (OSError, IOError):
                pass
        self._close_fh()
        if self._backend == "fallback":
            try:
                if os.path.exists(self.path):
                    os.remove(self.path)
            except OSError:
                pass
        self._backend = None

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError("无法获取文件锁: " + self.path)
        return self

    def __exit__(self, *exc):
        self.release()
