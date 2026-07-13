FROM python:3.12-slim
WORKDIR /app/server
COPY server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server/app.py .
COPY server/static ./static
# CLI 与 skill 由服务在线分发（/install.sh /cli/tn.py /skill/SKILL.md）
COPY cli /app/cli
COPY skill /app/skill
ENV TN_DB=/data/tn.db
VOLUME /data
EXPOSE 8787
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8787"]
