FROM python:3.9-slim

WORKDIR /app

RUN pip install --upgrade pip

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Создаем директории 
RUN mkdir -p /app/logs /app/data

# Устанавливаем правильные права доступа
RUN chmod -R 777 /app/data /app/logs

WORKDIR /app

EXPOSE 3000

CMD ["python", "-u", "bot/main.py"]