import asyncio
import json

import asyncio_mqtt as aiomqtt
# import paho.mqtt as mqtt

import logging
_LOGGER = logging.getLogger(__name__)


class MQTTController:

    def __init__(self, config: dict) -> None:
        self._client = None
        self._topic = config["root_topic"]
        self._gateway_id = config["gateway_id"]
        async def no_op(*kw):
            pass
        self._on_message = no_op

    def set_on_message(self, handler):
        self._on_message = handler

    def build_topic(self, tmpl, id):
        return tmpl.replace("{topic}", self._topic).replace("{id}", id)
    
    def get_status_topic(self):
        return f"{self._topic}/{self._gateway_id}/status"
    
    async def connect(self, connect_future, hostport: str, client_id: str, username: str, password: str, reconnect_after: int):
        host = hostport
        port = 1883
        hp_parts = hostport.split(":")
        if len(hp_parts) == 2:
            host = hp_parts[0]
            port = int(hp_parts[1], 10)
        status_topic = self.get_status_topic()
        while True:
            self._client = None
            try:
                client = aiomqtt.Client(
                    host,
                    port=port,
                    username=username if username else None,
                    password=password if password else None,
                    client_id=client_id if client_id else None,
                    will=aiomqtt.Will(topic=status_topic, payload=json.dumps({"status": "offline"}), retain=True),
                )
                async with client:
                    _LOGGER.info(f"mqtt_connect(): Connected to: {host}:{port}")
                    if not connect_future.done():
                        connect_future.set_result(self)
                    await client.publish(status_topic, json.dumps({"status": "online"}), retain=True)
                    async with client.messages() as messages:
                        await client.subscribe(f"{self._topic}/+/command/+")
                        self._client = client
                        async for message in messages:
                            _LOGGER.debug(f"mqtt_connect(): New message: {message.topic.value}: {message.payload}")
                            tparts = message.topic.value.split("/")
                            await self._on_message(tparts[1], tparts[3], message.payload.decode())
                _LOGGER.info(f"mqtt_connect(): Disconnected from server")
            except aiomqtt.MqttError as error:
                _LOGGER.exception(f"mqtt_connect(): Error while communicating to MQTT server")
                await asyncio.sleep(reconnect_after)

    async def publish_json(self, id: str, topic_tmpl: str, data: dict, retain: bool=False):
        if self._client:
            _LOGGER.debug(f"mqtt_publish_json(): {topic_tmpl}: {data}")
            await self._client.publish(self.build_topic(topic_tmpl, id), json.dumps(data), retain=retain)
        else:
            _LOGGER.warn(f"mqtt_publish_json(): No active MQTT connection")