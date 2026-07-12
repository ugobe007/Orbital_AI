FROM python:3.12-slim

WORKDIR /app

# Dependency layer first for build caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The Fleet Dashboard SPA + API both serve on this port (matches fly.toml internal_port).
ENV ORBITAL_PORT=8090
EXPOSE 8090

# uvicorn[standard] brings the websockets impl the live dashboard needs.
CMD ["uvicorn", "orbital_cloud.main:app", "--host", "0.0.0.0", "--port", "8090"]
