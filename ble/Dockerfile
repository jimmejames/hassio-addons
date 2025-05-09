FROM python:3.11-alpine

# Set the working directory inside the container
WORKDIR /app

# Install system dependencies (Alpine base)
RUN apk add --no-cache \
    python3-dev \
    bluez \
    dbus \
    libffi-dev \
    musl-dev \
    jq

# Install Python dependencies
RUN pip3 install bleak
RUN pip3 install Pillow

# Copy your application files into the container
COPY . .

COPY run.sh /run.sh

# Make run.sh executable
RUN chmod +x /run.sh

# Default command to run when the container starts
CMD ["/run.sh"]
