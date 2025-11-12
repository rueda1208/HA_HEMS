# Home Energy Management System (HEMS)

This repository contains the HEMS (Home Energy Management System) implementation and related deployment artifacts.

The repo is structured to hold the main control service, configurations, container build scripts and helpers for telemetry (telegraf) and time-series storage (TimescaleDB).

## Prerequisites
- Home Assistant OS / Supervised (Supervisor required for add-on install).
- Administrator access to the Supervisor UI or SSH access to the host for manual installs.
- **TimescaleDB Add-on**: This add-on provides a powerful time-series database solution for Home Assistant. It is designed to efficiently store and query time-series data, making it ideal for monitoring and analyzing energy usage patterns. For more details, visit the [TimescaleDB add-on repository](https://github.com/expaso/hassos-addons).

### Install the TimescaleDB add-on
1. Open Home Assistant → Settings → Add-on → Add-on store.
2. Click the three-dot menu (top right) → Repositories.
3. Add repository URL: `https://github.com/expaso/hassos-addons`
4. The new add-ons appear in the Add-on store. Select the TimescaleDB add-on, click Install.
5. Configure the add-on on its Configuration tab (follow the add-on’s documented options) and click Start.
6. (Optional) Enable Start on boot and Show in sidebar as needed.
7. Check Logs to verify startup and troubleshoot if required.

For more information, see the [official TimescaleDB add-on documentation](https://github.com/expaso/hassos-addon-timescaledb/blob/v5.4.2/README.md).

## Installation

[![Open your Home Assistant instance and show the add add-on repository dialog with a specific repository URL pre-filled.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Frueda1208%2FHA_HEMS)

or..

In the Home-Assistant add-on store, a possibility to add a repository is provided.
Use the following URL to add this repository:

```txt
https://github.com/rueda1208/HA_HEMS
```

## Available Add-ons

### [Telegraf][addon-telegraf]
Telegraf is an open-source agent for collecting, processing, and sending metrics and events from various sources to different outputs. It is designed to be lightweight and efficient, making it an ideal choice for monitoring and analyzing energy usage data in conjunction with TimescaleDB. Telegraf supports a wide range of input and output plugins, allowing for flexible integration with various systems and services.

![aarch64][aarch64-shield] ![amd64][amd64-shield] ![armhf][armhf-shield] ![armv7][armv7-shield] ![i386][i386-shield]

### [Controller][addon-controller]
The Controller add-on is designed to efficiently manage and orchestrate appliances and distributed energy resources (DERs) available within the dwelling. It provides a centralized interface for monitoring and controlling energy usage, ensuring optimal performance and integration of all connected devices. Additionally, it allows users to customize settings according to their preferences, enhancing user satisfaction by tailoring energy management to individual needs and habits.

![aarch64][aarch64-shield] ![amd64][amd64-shield] ![armhf][armhf-shield] ![armv7][armv7-shield] ![i386][i386-shield]


[addon-telegraf]: https://github.com/rueda1208/HA_HEMS/tree/main/telegraf
[addon-controller]: https://github.com/rueda1208/HA_HEMS/tree/main/controller
[aarch64-shield]: https://img.shields.io/badge/aarch64-yes-green.svg
[amd64-shield]: https://img.shields.io/badge/amd64-yes-green.svg
[armhf-shield]: https://img.shields.io/badge/armhf-yes-green.svg
[armv7-shield]: https://img.shields.io/badge/armv7-yes-green.svg
[i386-shield]: https://img.shields.io/badge/i386-yes-green.svg