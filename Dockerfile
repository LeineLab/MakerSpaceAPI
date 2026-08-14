# ---- Stage 1: build Tailwind CSS ----
# Compiled fresh at image-build time (CI), so the committed tailwind.css never
# needs to be up to date and can even be dropped from version control.
FROM node:20-slim AS css

WORKDIR /build

# Install exact Tailwind toolchain
COPY package.json package-lock.json ./
RUN npm ci

# Sources Tailwind needs: config, entry CSS and the templates it scans for classes
COPY tailwind.config.js ./
COPY app/web/static/css/input.css app/web/static/css/input.css
COPY app/web/templates app/web/templates

RUN npm run build:css


# ---- Stage 2: runtime ----
FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/leinelab/makerspaceapi"

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Overwrite the (possibly stale) committed CSS with the freshly built one
COPY --from=css /build/app/web/static/css/tailwind.css app/web/static/css/tailwind.css

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
