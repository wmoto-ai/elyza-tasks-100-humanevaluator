FROM python:3.9

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY app /app
COPY data /data

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
