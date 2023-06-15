import asyncio
import queue

import click

import logging
from rich.logging import RichHandler

from .ble import BLEController
from .mqtt import MQTTController

from . import protocol

from bleak.backends.device import BLEDevice


LOGGER_FORMAT = "%(message)s"

_LOGGER = logging.getLogger(__name__)

def _address_to_id(address: str) -> str:
    no_colon = address.upper().replace(":", "")
    return f"0x{no_colon}"

class Controller:

    def __init__(self, config: dict) -> None:
        self._devices = {}
        self._cmd_queue = queue.SimpleQueue()
        self._ble = BLEController(config)
        self._mqtt = MQTTController(config)
        self._config = config
        self._ble.set_on_new_device(self.on_new_device)
        self._ble.set_on_device_update(self.on_device_data)
        self._mqtt.set_on_message(self.on_message)


    async def on_new_device(self, device):
        _LOGGER.info(f"on_new_device(): New discovered device: {device}")
        data = {"address": device.address.upper(), "name": device.name}
        model_parts = device.name.split("_")
        data["model"] = model_parts[1] if len(model_parts) == 3 else device.name
        id = _address_to_id(device.address)
        self._devices[id] = data
        try:
            if hass_prefix := self._config["homeassistant_discovery"]:
                _LOGGER.debug(f"on_new_device(): Publish hass discovery message")
                hass_data = {
                    "availability": [{
                        "topic": self._mqtt.get_status_topic(),
                        "value_template": "{{ value_json.status }}"
                    }],
                    "availability_mode": "all",
                    "optimistic": True,
                    "brightness": True,
                    "brightness_scale": 100,
                    "color_mode":True,
                    "command_topic": self._mqtt.build_topic("{topic}/{id}/command/json", id),
                    "device": {
                        "identifiers": [f"govee_ble_{id}"],
                        "manufacturer": "Govee",
                        "model": data["model"],
                        "name": data["name"],
                    },
                    "effect":True,
                    "min_mireds":153,
                    "max_mireds":555,
                    "name": data["name"],
                    "schema": "json",
                    "state_topic": self._mqtt.build_topic("{topic}/{id}/status", id),
                    "supported_color_modes": ["color_temp", "rgb"],
                    "unique_id": f"{id}_govee_ble",
                }
                await self._mqtt.publish_json(id, hass_prefix + "/light/{id}/config", hass_data, retain=True)
            await self._mqtt.publish_json(id, "{topic}/{id}/info", data)
        except Exception:
            _LOGGER.exception(f"on_new_device(): Error while publishing device data")

    async def on_device_data(self, device, mdata):
        _LOGGER.info(f"on_device_data(): Device manufacturer data update: {device}, {mdata}")
        data = {"state": "ON" if mdata[4] == 0x01 else "OFF"}
        try:
            await self._mqtt.publish_json(_address_to_id(device.address), "{topic}/{id}/status", data, retain=True)
        except Exception:
            _LOGGER.exception(f"on_device_data(): Error while publishing device status")

    async def process_cmds(self):
        await asyncio.sleep(0.5)
        _dev_map = dict()
        while True:
            try:
                (id, cmd, payload) = self._cmd_queue.get_nowait()
                cmd_list = _dev_map.get(id, [])
                cmd_list.append((cmd, payload))
                _dev_map[id] = cmd_list
            except queue.Empty:
                break
        _LOGGER.info(f"process_cmds(): Payloads: {_dev_map}")
        for (id, list) in _dev_map.items():
            device = self._devices[id]
            payloads = []
            for (cmd, payload) in list:
                payloads += protocol.handle_command(cmd, payload, device["model"])
            try:
                await self._ble.send_commands(device["address"], payloads)
            except Exception:
                _LOGGER.exception(f"on_message(): Failed to send commands")
        _LOGGER.debug("on_message(): Resuming discovery")
        self._task_group.create_task(
            self._ble.start_discovery()
        )


    async def on_message(self, id, cmd, payload):
        _LOGGER.info(f"on_message(): New MQTT command: {id}, {cmd}, {payload}")
        if self._devices.get(id):
            if self._cmd_queue.empty():
                self._task_group.create_task(
                    self.process_cmds()
                )
            self._cmd_queue.put((id, cmd, payload))
        else:
            _LOGGER.warn(f"on_message(): Unknown device ID: {id}")

    async def start(self):
        self._task_group = asyncio.TaskGroup()
        async with self._task_group as tg:
            connect_future = asyncio.get_running_loop().create_future()
            tg.create_task(
                self._mqtt.connect(
                    connect_future,
                    self._config["mqtt_server"], 
                    client_id=self._config["mqtt_client_id"],
                    username=self._config["mqtt_username"],
                    password=self._config["mqtt_password"],
                    reconnect_after=self._config["mqtt_reconnect"],
                )
            )
            await connect_future
            tg.create_task(
                self._ble.start_discovery()
            )
            # async def _test_device():
            #     await asyncio.sleep(3)
            #     await self.on_new_device(BLEDevice("C5:37:34:32:3D:1E", "Govee_H7060_3D1E", None, 10))
            # self._task_group.create_task(
            #     _test_device()
            # )
            # {"address": "C5:37:34:32:3D:1E", "name": "Govee_H7060_3D1E", "model": "H7060"}


@click.option("--mqtt-server", required=True, help="MQTT server host or host:port", type=str)
@click.option("--mqtt-username", default="", help="MQTT username", type=str)
@click.option("--mqtt-password", default="", help="MQTT username", type=str)
@click.option("--mqtt-client-id", default="", help="MQTT client ID", type=str)
@click.option("--mqtt-reconnect", default=10, help="Reconnect to MQTT server after seconds", type=int)
@click.option("--root-topic", default="govee_ble", help="MQTT root topic", type=str)
@click.option("--gateway-id", default="default", help="Gateway ID", type=str)
@click.option("--homeassistant-discovery", help="Home Assitant Discovery MQTT topic. Usually 'homeassistant'", type=str)
@click.option("--log-level", default="DEBUG", help="Logging level: DEBUG/INFO/WARN", type=str)
@click.option("--device", help="Only manage specified device(s) and ignore all others", type=str, multiple=True)
@click.command()
def cli(**kwargs):
    """Run Govee BLE MQTT Gateway in foreground mode"""
    logging.basicConfig(
        level=kwargs["log_level"], format=LOGGER_FORMAT, datefmt="[%c]", handlers=[RichHandler()]
    )
    _LOGGER.debug(f"Command line arguments: {kwargs}")
    ctrl = Controller(kwargs)
    asyncio.run(ctrl.start())
