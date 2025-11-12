# Base image
FROM telegraf:latest

# Run Telegraf with specified config and watch option
ENTRYPOINT ["telegraf"]
CMD ["--config", "/share/telegraf/telegraf.conf", "--watch-config", "poll"]
