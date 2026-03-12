FROM python:3.14-slim

# Set working directory
WORKDIR /app

# Install system dependencies needed for pyserial (USB/serial support)
RUN apt-get update && apt-get install -y --no-install-recommends \
    udev \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml LICENSE ./
COPY server.py hdmi_matrix.py hdmi_matrix_status.py ./

# Install Python dependencies
RUN pip install --upgrade pip && pip install --no-cache-dir -e .

# Expose Flask port
EXPOSE 5000

# Run the Flask app
CMD ["python", "server.py"]
