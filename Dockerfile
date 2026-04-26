FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (minimal needed)
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the bot script
COPY bot.py .

# Run the bot
CMD ["python", "bot.py"]
