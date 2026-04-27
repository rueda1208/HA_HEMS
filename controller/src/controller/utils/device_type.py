from enum import StrEnum


class DeviceType(StrEnum):
    THERMOSTAT = "thermostat"
    HEAT_PUMP = "heat_pump"
    ZONE = "zone"
    HUB = "hub"
    BATTERY = "battery"
    ELECTRIC_VEHICLE = "electric_vehicle"
    WATER_HEATER = "water_heater"
