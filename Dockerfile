FROM python:3.11-alpine

LABEL org.opencontainers.image.source="https://github.com/yuuouu/wechat-bridge"
LABEL org.opencontainers.image.description="WeChat message bridge based on iLink Bot API"
LABEL org.opencontainers.image.licenses="MIT"

WORKDIR /app

# 安装依赖
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY app/ .

# Docker 数据路径（覆盖代码中的 ./data 默认值）
ENV TOKEN_FILE=/data/token.json \
    DATA_DIR=/data \
    AI_CONFIG_FILE=/data/ai_config.json \
    NO_BROWSER=1

# 数据持久化目录
VOLUME /data

# 服务端口
EXPOSE 5200

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:5200/api/status')" || exit 1

CMD ["python3", "main.py"]
