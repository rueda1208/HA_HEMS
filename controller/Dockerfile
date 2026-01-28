FROM python:3.11.0-slim-bullseye

# Install Poetry tool
RUN apt-get update && pip3 install poetry && poetry --version

# Create working directory
WORKDIR /workspaces

# Copy the application files
COPY . .

# Install the dependencies and create a virtual environment
RUN poetry install

# Run the Web server
ENTRYPOINT ["poetry", "run", "python", "-m", "controller.main"]