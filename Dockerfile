FROM python:3.12-slim

WORKDIR /app

# Install system dependencies.
#
# WeasyPrint runtime deps (libpango, libharfbuzz, libpangoft2) are what
# lets us render report PDFs from the actual HTML template — same look
# as the web view. fonts-liberation provides the default sans/serif
# faces WeasyPrint falls back to when the template doesn't declare a
# custom font.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmagic1 \
    libpq-dev \
    gcc \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz0b \
    fonts-liberation \
    fonts-dejavu \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create non-root user for security
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Default command (Railway overrides this with startCommand)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
