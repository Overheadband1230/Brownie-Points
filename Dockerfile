FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
# DB auto-creates on boot if missing; no manual migration step for v1.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
