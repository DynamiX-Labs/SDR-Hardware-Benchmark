FROM ubuntu:24.04

# Avoid tzdata interactive prompts
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    rtl-sdr \
    hackrf \
    soapysdr-tools \
    libsoapysdr-dev \
    git \
    build-essential \
    chrony \
    libhamlib-utils \
    portaudio19-dev \
    libhamlib-dev \
    && rm -rf /var/lib/apt/lists/*

# Set up working directory
WORKDIR /app

# Set up virtual environment
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy package requirements and install dependencies
COPY requirements.txt pyproject.toml README.md ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir --no-deps -e .

# Copy remaining project files
COPY . .

# Default command
CMD ["bash"]
