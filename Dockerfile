# One long-lived process: the demo keeps trips and the hand-cranked clock in memory.
# Works as-is on Render, Fly.io, Railway and Hugging Face Spaces (set PORT=7860 there).
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=8000 QUORUM_VOICE=mock QUORUM_PAYMENTS=mock
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
