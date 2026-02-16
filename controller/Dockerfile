ARG BUILD_FROM
FROM $BUILD_FROM

# Install Python & Poetry
RUN apk update && apk add python3 poetry

# Copy the application files
COPY . .

# Install the dependencies and create a virtual environment
RUN chmod a+x /run.sh && poetry install

CMD [ "/run.sh" ]