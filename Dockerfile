FROM python:3.11-slim

WORKDIR /app

# Fix SSL: lower OpenSSL security level for MongoDB Atlas compatibility
RUN apt-get update && apt-get install -y ca-certificates && rm -rf /var/lib/apt/lists/*
RUN sed -i 's/@SECLEVEL=2/@SECLEVEL=1/g' /etc/ssl/openssl.cnf || true

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Expose port
EXPOSE 8000

# Command to run
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
