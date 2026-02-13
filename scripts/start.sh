#!/bin/bash
set -e

echo "=== ClassUp Application Startup ==="

# Run database migrations
echo "Running database migrations..."
alembic upgrade head

# Start the appropriate service
if [ "$WORKER_MODE" = "true" ]; then
    echo "Starting arq worker..."
    arq app.worker.WorkerSettings
else
    echo "Starting web server on port ${PORT:-8000}..."
    uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_WORKERS:-2}
fi
