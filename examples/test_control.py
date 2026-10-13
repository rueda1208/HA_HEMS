from controller.ha_interface.ha_interface import HomeAssistantDeviceInterface
from controller.ha_events import HomeAssistantEventListener , on_ha_setpoint_changed
from controller.main import fetch_hems_snapshot , setpoint_entity_ids
import os , json, logging, random, time
import pandas as pd
from sqlalchemy import URL, create_engine, text

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
base_url = "http://hems201.hydroquebec.lab:8123/"
token = os.getenv("SUPERVISOR_TOKEN")
building_id = os.getenv("BUILDING_ID")
print(f"Base URL: {base_url}")
# print(f"Token: {token}")
print(f"Building ID: {building_id}")

def read_timescale_rows(limit: int = 20, metric_type: str | None = None) -> list[dict[str, object]]:
    if limit < 1:
        raise ValueError("limit must be greater than zero")

    db_url = URL.create(
        "postgresql+psycopg2",
        username=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "homeassistant"),
        host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        database=os.getenv("POSTGRES_NAME", "homeassistant"),
    )
    query = """
        SELECT time, metric_type, device_id, name, value
        FROM space_heating
    """
    parameters: dict[str, object] = {"limit": limit}
    if metric_type is not None:
        query += "\nWHERE metric_type = :metric_type"
        parameters["metric_type"] = metric_type
    query += "\nORDER BY time DESC\nLIMIT :limit"

    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            result = connection.execute(text(query), parameters)
            print(f"{pd.DataFrame(result.mappings().all())}")
            return [dict(row) for row in result.mappings()]
    finally:
        engine.dispose()

def test_home_assistant_device_interface():
    interface = HomeAssistantDeviceInterface(base_url=base_url, token=token)
    assert interface is not None

    states = interface.get_devices_states()
    print(f"Device States: {states.keys()}")

    interface

def test_fetch_hems_snapshot():
    snapshot = fetch_hems_snapshot()
    setpoint_ids = setpoint_entity_ids(snapshot.configurations)
    print(f"HEMS Snapshot keys: {snapshot.configurations.keys()} ; gdp: {snapshot.gdp_event}")
    print(f"Setpoint Entity IDs: {setpoint_ids}")

    interface = HomeAssistantDeviceInterface(base_url=base_url, token=token,allow_control=True)
    states = interface.get_devices_states()
    # print(f"Device States: {states.keys()}")

    setpoints = {sp_id: states.get(sp_id) for sp_id in setpoint_ids if sp_id == 'climate.sous_sol_1'}
    print(f"Setpoint States: {json.dumps(setpoints, indent=2)}")

def test_control(iterations: int = 1, delay_seconds: float = 5.0):
    entity_id = 'climate.cuisine'

    event_listener = HomeAssistantEventListener(
        base_url=base_url,
        token=token,
        on_state_changed=on_ha_setpoint_changed,
        relevant_entity_ids= {entity_id},
        relevant_attribute="temperature",
    )

    interface = HomeAssistantDeviceInterface(base_url=base_url, token=token, allow_control=True)

    if iterations < 0 or delay_seconds < 0:
        raise ValueError("iterations and delay_seconds must be non-negative")

    logger.info("Starting control test with %d iterations and %.1f seconds delay", iterations, delay_seconds)
    event_listener.start()
    try:
        for iteration in range(1, iterations + 1):
            if random.choice((True,)):
                setpoint = round(random.uniform(18.0, 21.0), 1)
                try:
                    states = interface.get_devices_states()
                    entity_state = states.get(entity_id, {})
                    current_temperature = entity_state.get("attributes", {}).get("current_temperature")
                    temperature_setpoint = entity_state.get("attributes", {}).get("temperature")
                    logger.info(f"Current observations: T= {current_temperature} ; setpoint= {temperature_setpoint} before executing control actions")
                    interface.execute_control_actions({entity_id: setpoint}, devices_states=states)
                    logger.info(
                        "Iteration %d/%d: sent %.1f C setpoint to %s",
                        iteration,
                        iterations,
                        setpoint,
                        entity_id,
                    )
                    new_states = interface.get_devices_states()
                    new_entity_state = new_states.get(entity_id, {})
                    new_temperature = new_entity_state.get("attributes", {}).get("current_temperature")
                    new_setpoint = new_entity_state.get("attributes", {}).get("temperature")
                    logger.info(f"New observations: T= {new_temperature} ; setpoint= {new_setpoint} after executing control actions")
                except Exception as error:
                    error_summary = str(error).splitlines()[0][:200] if str(error) else "No error details"
                    logger.error(
                        "Iteration %d/%d: failed to set %s (%s): %s",
                        iteration,
                        iterations,
                        entity_id,
                        type(error).__name__,
                        error_summary,
                    )
            else:
                logger.info("Iteration %d/%d: skipped setpoint for %s", iteration, iterations, entity_id)
            time.sleep(delay_seconds)
    finally:
        event_listener.stop()

if __name__ == "__main__":
    test_control(iterations=1, delay_seconds=120.0)
    # test_fetch_hems_snapshot()
    # read_timescale_rows()