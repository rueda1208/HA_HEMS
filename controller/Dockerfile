FROM ghcr.io/home-assistant/base:3.23-2026.04.0

# Install Python & Poetry
RUN apk update && apk add python3 poetry

# Copy the application files
COPY . .

# Install the dependencies and create a virtual environment (prod only, excludes the "dev" group:
# type stubs, ipykernel, matplotlib - only needed locally for linting/notebooks, not at runtime)
RUN chmod a+x /run.sh && poetry install --only main

CMD [ "/run.sh" ]