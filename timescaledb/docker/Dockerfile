FROM timescale/timescaledb:2.15.1-pg16
ENV LANG=C.UTF-8

# Copy init scripts
COPY run.sh /run.sh
RUN chmod a+x /run.sh

# Ensure data directory exists and is writable
RUN mkdir -p /data/postgresql && chmod -R 777 /data

# Run as root
USER root

# Use the official entrypoint
ENTRYPOINT ["/run.sh"]

# Expose Postgres port
EXPOSE 5432
