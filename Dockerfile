# invest-radar —— 容器镜像（国家队 A 股资金流 + QDII 海外基金定投 + 未来股票模块共用）
# 特点：零第三方 Python 依赖，镜像很小；适合 2C2G 这类小规格 VPS 与其它服务共存。
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai \
    GATEWAY_PORT=38881 \
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
# 单端口入口：把 / 与 /qdii/ 分发到两个看板（对外只暴露 38881）
COPY --chown=appuser:appuser gateway.py ./gateway.py
COPY --chown=appuser:appuser docker/entrypoint.sh ./docker/entrypoint.sh
COPY --chown=appuser:appuser docker/healthcheck.py ./docker/healthcheck.py

RUN mkdir -p /app/data \
    && chown -R appuser:appuser /app/data /app/docs \
    && chmod +x /app/docker/entrypoint.sh

USER appuser
# 38881 = 单端口入口（对外只暴露它）；5000/5001 = 两个看板的内部端口（仅容器网络内可见）
EXPOSE 38881 5000 5001

# 数据目录：务必用命名卷或宿主机目录挂载，否则重建容器后历史数据会丢
VOLUME ["/app/data"]

# gateway 容器探 38881；两个 web 容器探各自的 5000/5001（脚本内逐个试，任一 200 即健康）
HEALTHCHECK --interval=60s --timeout=10s --start-period=45s --retries=3 \
    CMD python /app/docker/healthcheck.py

ENTRYPOINT ["/app/docker/entrypoint.sh"]
