"""drawdown/seed.py — dd.db gzip 种子恢复（首次部署填充全历史）。

背景：dd.db 全历史约 46MB，超过 WorkBuddy 单应用 19.7MB 上传阈值，
无法随包上传；Docker VPS 虽无上传限制，但空卷首启也需要一次性灌入全历史。
故把 dd.db 以 gzip(约 9.8MB) 作为种子放进部署包，首启检测空库时原子解压恢复。

两个调用点（同一份逻辑，零新增进程）：
- WorkBuddy：national_team/auto_refresh.py 的 _dd_incremental() 之前先调 ensure()；
- VPS Docker：scheduler.py 的 dd 任务执行前先调 ensure()（cron 容器有网络）。

设计约束：
- 幂等：目标库已有数据（available_assets 非空）直接跳过，绝不覆盖真实数据；
- 并发安全：FileLock 串行，避免首启两个线程/容器同时解压；
- 原子写：先解压到 .tmp 再 os.replace，避免半截库；
- 单标的失败/无种子只记日志返回 False，绝不阻断主面板或调度器。
"""
import gzip
import os
import shutil

from . import constants as C
from . import db

# 种子文件：仓库根 dd.db.gz，随部署包上传（.gitignore 显式放行）
SEED_FILE = os.path.join(C.ROOT, "dd.db.gz")


def ensure(path=None, seed_file=None, log=print, force=False):
    """目标 dd.db 为空/不存在时，从 gzip 种子原子恢复。返回是否执行了恢复。

    force=True 仅用于测试/离线重建（会覆盖目标库），正常调用一律 False。
    """
    path = path or db.DB_PATH
    seed_file = seed_file or SEED_FILE
    try:
        info = db.available_assets(path)
    except Exception:  # noqa: BLE001  空库/损坏库都当「需要恢复」
        info = {}
    if info and not force:
        log("[dd-seed] 已有 %d 标的，跳过种子恢复" % len(info))
        return False
    if not os.path.exists(seed_file):
        log("[dd-seed] 无种子文件 %s，跳过（留空待 backfill）" % seed_file)
        return False

    # 并发串行：复用 common.lock 的跨平台文件锁（与 drawdown 同仓，ROOT 已在 sys.path）
    from common.lock import FileLock
    lock = FileLock(path + ".seed.lock")
    if not lock.acquire():
        log("[dd-seed] 另一进程正在恢复种子，跳过")
        return False
    try:
        # 双检：拿锁后再确认一次（等锁期间可能被别的进程恢复好了）
        try:
            info2 = db.available_assets(path)
        except Exception:  # noqa: BLE001
            info2 = {}
        if info2 and not force:
            log("[dd-seed] 已有 %d 标的（锁内复检），跳过" % len(info2))
            return False
        tmp = path + ".seed.tmp"
        with gzip.open(seed_file, "rb") as g, open(tmp, "wb") as out:
            shutil.copyfileobj(g, out, 1024 * 1024)
        os.replace(tmp, path)
        # WAL 残留清理：种子是干净库，若目标目录有旧 -wal/-shm 应删掉，
        # 否则 SQLite 会把陈旧 WAL 回放覆盖刚恢复的库。
        for suffix in ("-wal", "-shm"):
            stale = path + suffix
            if os.path.exists(stale):
                try:
                    os.remove(stale)
                except OSError:
                    pass
        n = len(db.available_assets(path))
        log("[dd-seed] 已从种子恢复 %s：%d 标的" % (os.path.basename(path), n))
        return True
    except Exception as e:  # noqa: BLE001
        log("[dd-seed] 恢复失败：%s: %s" % (type(e).__name__, e))
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except (OSError, UnboundLocalError):
            pass
        return False
    finally:
        lock.release()


if __name__ == "__main__":
    ensure()
