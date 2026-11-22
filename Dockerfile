# Streamlit demo image (Hugging Face Spaces uses port 7860).
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home sentinel
WORKDIR /home/sentinel/app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=sentinel . .
USER sentinel

ENV PUBLIC_DEMO=true
EXPOSE 7860
CMD ["streamlit", "run", "streamlit_app/home.py", "--server.port=7860", "--server.address=0.0.0.0"]
