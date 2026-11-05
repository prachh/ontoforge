FROM node:24-alpine AS frontend
WORKDIR /build/web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.13-slim
LABEL org.opencontainers.image.title="OntoForge" org.opencontainers.image.source="https://github.com/prachh/ontoforge" org.opencontainers.image.licenses="MIT"
WORKDIR /app
COPY requirements.lock.txt ./
RUN pip install --no-cache-dir -r requirements.lock.txt
COPY backend/ backend/
COPY sample/ sample/
COPY --from=frontend /build/web/dist/ web/dist/
RUN useradd --create-home --uid 10001 ontoforge && mkdir -p /app/data /sources && chown ontoforge:ontoforge /app/data
USER ontoforge
ENV ONTO_DATA=/app/data ONTO_LOCAL_ROOTS=/sources
EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
