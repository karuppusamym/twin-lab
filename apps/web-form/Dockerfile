FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py routes.json ./
COPY site ./site
COPY licenses ./licenses
COPY THIRD-PARTY-NOTICES.md ./
EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
