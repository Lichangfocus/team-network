FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
COPY static ./static
# 数据库放 volume，容器重建不丢数据
ENV TN_DB=/data/tn.db
VOLUME /data
EXPOSE 8787
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8787"]
