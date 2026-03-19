FROM python:3.12-slim

WORKDIR /app

# Install system deps: Docker CLI + Playwright Chromium dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg \
    # Playwright Chromium dependencies
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libdbus-1-3 libxkbcommon0 libatspi2.0-0 libxcomposite1 libxdamage1 \
    libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    libwayland-client0 && \
    # Docker CLI
    install -m 0755 -d /etc/apt/keyrings && \
    curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc && \
    chmod a+r /etc/apt/keyrings/docker.asc && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list && \
    apt-get update && apt-get install -y --no-install-recommends docker-ce-cli && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright + Chromium browser binary (deps already installed above)
RUN pip install playwright && playwright install chromium

# Copy source
COPY . .

# Pre-pull the sandbox image so first execution is fast
RUN docker pull python:3.12-slim 2>/dev/null || true

RUN useradd -m -s /bin/bash stourio && chown -R stourio:stourio /app
# Add stourio to docker group so it can use the socket
RUN groupadd -f docker && usermod -aG docker stourio
USER stourio

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
