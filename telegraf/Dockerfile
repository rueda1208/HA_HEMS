FROM ghcr.io/home-assistant/base:3.23-2026.04.0

RUN apk update && apk add --no-cache telegraf postgresql-client

COPY telegraf.conf /etc/telegraf/telegraf.conf

COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]