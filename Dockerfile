# Use official Python runtime as a parent image
FROM python:3.10-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container
COPY backend/requirements.txt .

# Install dependencies (CPU fallback by default to keep image size reasonable, 
# can be overridden with ROCm base image if deploying to AMD hardware)
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu

# Copy the backend and frontend directories
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# Expose port 8000 for the FastAPI application
EXPOSE 8000

# Set the working directory to backend so uvicorn runs correctly
WORKDIR /app/backend

# Command to run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
