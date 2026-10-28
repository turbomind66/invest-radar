# 国家队买卖A股日报 —— 容器镜像
# 特点：零第三方 Python 依赖，镜像很小；适合 2C2G 这类小规格 VPS 与其它服务共存。
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai \
    PORT=5000 \
    NT_DB_PATH=/app/data/data.db

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

COPY --chown=appuser:appuser app.py db.py fetcher.py intraday.py staticsite.py scheduler.py ./
COPY --chown=appuser:appuser templates ./templates
# docs/data.json 是"种子数据"：空库首启会自动从中恢复最近 90 天历史，无需重新回补
COPY --chown=appuser:appuser docs ./docs
COPY --chown=appuser:appuser docker/entrypoint.sh ./docker/entrypoint.sh

RUN mkdir -p /app/data \
    && chown -R appuser:appuser /app/data /app/docs \
    && chmod +x /app/docker/entrypoint.sh

USER appuser
EXPOSE 5000

# 数据目录：务必用命名卷或宿主机目录挂载，否则重建容器后历史数据会丢
VOLUME ["/app/data"]

HEALTHCHECK --interval=60s --timeout=10s --start-period=45s --retries=3 \
    CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','5000')+'/api/status',timeout=8)"

ENTRYPOINT ["/app/docker/entrypoint.sh"]
