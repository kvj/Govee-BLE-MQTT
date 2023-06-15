import asyncio
from bleak import BleakScanner, BleakClient

import logging
_LOGGER = logging.getLogger(__name__)

GOVEE_MDATA = 0x8802
GOVEE_READ_CHAR = "00010203-0405-0607-0809-0a0b0c0d2b10"
GOVEE_WRITE_CHAR = "00010203-0405-0607-0809-0a0b0c0d2b11"

class BLEController:

    def __init__(self, config: dict) -> None:
        logging.getLogger("bleak").setLevel(logging.INFO)
        async def no_op(*kw):
            pass
        self._on_new_device = no_op
        self._on_device_update = no_op
        self._discovery_event = None
        self._device_allow_list = []
        for addr in config["device"]:
            self._device_allow_list.append(addr.upper())

        self._device_cache = dict()

    def set_on_new_device(self, handler):
        self._on_new_device = handler

    def set_on_device_update(self, handler):
        self._on_device_update = handler

    async def send_commands(self, address, commands):
        if self._discovery_event:
            self._discovery_event.set()
        async def on_notify(client, data):
            _LOGGER.debug(f"on_notify(): {data}")
        cached = self._device_cache.get(address)
        async with BleakClient(cached[0] if cached else address) as client:
            await client.start_notify(GOVEE_READ_CHAR, on_notify)
            _LOGGER.info(f"send_commands() sending [{len(commands)}] to {address}")
            for cmd in commands:
                await client.write_gatt_char(GOVEE_WRITE_CHAR, cmd)
                _LOGGER.debug(f"send_commands(): Wrote cmd: {cmd}")
        _LOGGER.info(f"on_notify(): Connection was sucessful")

    async def start_discovery(self):
        if self._discovery_event:
            _LOGGER.warn(f"Discovery is in progress")
            return False
        async def callback(device, data):
            _LOGGER.debug(f"start_discovery(): Discovered: {device} - {data}")
            address = device.address.upper()
            if len(self._device_allow_list) and address not in self._device_allow_list:
                _LOGGER.debug(f"start_discovery(): Ignore device as it's not in the allow list: {address}")
                return
            if GOVEE_MDATA in data.manufacturer_data:
                mdata = data.manufacturer_data[GOVEE_MDATA]
                _LOGGER.debug(f"start_discovery(): Govee device: {device} - {data}")
                if address not in self._device_cache:
                    _LOGGER.debug(f"start_discovery(): New Govee device: {device}: {data}")
                    self._device_cache[address] = (device, data)
                    await self._on_new_device(device)
                    await self._on_device_update(device, mdata)
                else:
                    if self._device_cache[address][1].manufacturer_data[GOVEE_MDATA] != mdata:
                        _LOGGER.debug(f"start_discovery(): Manufacturer data has changed: {device} {data}")
                        self._device_cache[address] = (device, data)
                        await self._on_device_update(device, mdata)

        self._discovery_event = asyncio.Event()
        async with BleakScanner(callback) as scanner:
            _LOGGER.info(f"start_discovery(): Discovery has started")
            await self._discovery_event.wait()
            self._discovery_event = None
        _LOGGER.info(f"start_discovery(): Discovery stopped")
        return True
