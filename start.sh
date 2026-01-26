#!/bin/bash
# Startup script for Render deployment
# Ensures correct host and port binding

# Get port from environment variable (Render provides this)
PORT=${PORT:-8000}

# Run uvicorn with correct host and port
exec uvicorn app.main:app --host 0.0.0.0 --port $PORT
