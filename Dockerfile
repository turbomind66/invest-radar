# invest-radar —— 容器镜像（国家队 A 股资金流 + QDII 海外基金定投 + 未来股票模块共用）
# 特点：零第三方 Python 依赖，镜像很小；适合 2C2G 这类小规格 VPS 与其它服务共存。
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai \
    PORT=5000 \
    NT_DB_PATH=/app/data/data.db \
    QDII_PORT=5001 \
    NT_QDII_DB_PATH=/app/data/qdii.db

# tzdata 必装：调度时间与"是否交易日"全部按北京时间判定，缺了会差 8 小时。
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && ln -snf /usr/share/zoneinfo/${TZ} /etc/localtime \
    && echo ${TZ} > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 本项目零第三方依赖（requirements.txt 仅作说明），保留此步以便将来扩展
COPY requirements.txt* ./
RUN if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi

# 以非 root 运行
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /usr/sbin/nologin appuser

COPY --chown=appuser:appuser national_team/*.py ./national_team/
COPY --chown=appuser:appuser national_team/templates ./national_team/templates
# QDII 模块（海外基金定投决策面板）
COPY --chown=appuser:appuser qdii/*.py ./qdii/
COPY --chown=appuser:appuser qdii/templates ./qdii/templates
# 跨模块共用层（http / lock 等）
COPY --chown=appuser:appuser common ./common
# docs/data.json 是"种子数据"：空库首启会自动从中恢复最近 90 天历史，无需重新回补
COPY --chown=appuser:appuser docs ./docs
COPY --chown=appuser:appuser docker/entrypoint.sh ./docker/entrypoint.sh
COPY --chown=appuser:appuser docker/healthcheck.py ./docker/healthcheck.py

RUN mkdir -p /app/data \
    && chown -R appuser:appuser /app/data /app/docs \
    && chmod +x /app/docker/entrypoint.sh

USER appuser
# 5000 = 国家队看板，5001 = QDII 看板（同一镜像，按 APP_ROLE 决定实际监听哪个）
EXPOSE 5000 5001

# 数据目录：务必用命名卷或宿主机目录挂载，否则重建容器后历史数据会丢
VOLUME ["/app/data"]

# /api/status（国家队）与 /api/qdii/status（QDII）任一可达即健康
HEALTHCHECK --interval=60s --timeout=10s --start-period=45s --retries=3 \
    CMD python /app/docker/healthcheck.py

ENTRYPOINT ["/app/docker/entrypoint.sh"]
