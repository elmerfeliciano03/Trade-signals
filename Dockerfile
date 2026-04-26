FROM python:3.11-slim

WORKDIR /app

# Install system dependencies needed for numpy
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy your bot script
COPY bot.py .

# Run the bot
CMD ["python", "bot.py"]
