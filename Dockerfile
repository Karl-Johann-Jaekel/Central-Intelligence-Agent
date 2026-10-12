# One image for cia-api and cia-worker. Build context: repository root with the submodule checked out.
FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml ./
COPY cia/ cia/
# Editable install: contracts and migrations are resolved relative to the source tree.
RUN pip install --no-cache-dir -e .
COPY migrations/ migrations/
COPY vendor/bis/contracts/ vendor/bis/contracts/
RUN useradd --create-home app
USER app
EXPOSE 8000
CMD ["cia", "api", "--host", "0.0.0.0", "--port", "8000"]
