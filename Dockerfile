FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY seed_data.json .
COPY seed.py .

CMD ["python", "seed.py"]