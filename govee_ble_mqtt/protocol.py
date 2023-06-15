import json
import math
import logging

_LOGGER = logging.getLogger(__name__)

H7020 = {
    "twighlight": 2070,
    "meteor": 2071,
    "nebula": 2072,
    "illumination": 63,
    "bright": 2552,
    "colorful": 2553,
    "cheerful": 2097,
    "meditation": 2098,
    "hearthbeat": 65,
    "christmas": 2095,
    "christmas_tree": 2096,
    "sled": 2557,
}

def _clamp(color_component: float, minimum: float = 0, maximum: float = 255) -> float:
    """Clamp the given color component value between the given min and max values.

    The range defined by the minimum and maximum values is inclusive, i.e. given a
    color_component of 0 and a minimum of 10, the returned value is 10.
    """
    color_component_out = max(color_component, minimum)
    return min(color_component_out, maximum)


def _get_red(temperature: float) -> float:
    """Get the red component of the temperature in RGB space."""
    if temperature <= 66:
        return 255
    tmp_red = 329.698727446 * math.pow(temperature - 60, -0.1332047592)
    return _clamp(tmp_red)


def _get_green(temperature: float) -> float:
    """Get the green component of the given color temp in RGB space."""
    if temperature <= 66:
        green = 99.4708025861 * math.log(temperature) - 161.1195681661
    else:
        green = 288.1221695283 * math.pow(temperature - 60, -0.0755148492)
    return _clamp(green)


def _get_blue(temperature: float) -> float:
    """Get the blue component of the given color temperature in RGB space."""
    if temperature >= 66:
        return 255
    if temperature <= 19:
        return 0
    blue = 138.5177312231 * math.log(temperature - 10) - 305.0447927307
    return _clamp(blue)


def _color_temperature_mired_to_kelvin(mired_temperature: float) -> int:
    """Convert absolute mired shift to degrees kelvin."""
    return math.floor(1000000
     / mired_temperature)


def _color_temperature_to_rgb(
    color_temperature_kelvin: float,
) -> tuple[float, float, float]:
    """Return an RGB color from a color temperature in Kelvin.

    This is a rough approximation based on the formula provided by T. Helland
    http://www.tannerhelland.com/4435/convert-temperature-rgb-algorithm-code/
    """
    # range check
    if color_temperature_kelvin < 1000:
        color_temperature_kelvin = 1000
    elif color_temperature_kelvin > 40000:
        color_temperature_kelvin = 40000

    tmp_internal = color_temperature_kelvin / 100.0

    red = _get_red(tmp_internal)

    green = _get_green(tmp_internal)

    blue = _get_blue(tmp_internal)

    return red, green, blue


def _prepare_payload(cmd, payload):
    if len(payload) > 17:
        raise ValueError('Payload too long')

    cmd = cmd & 0xFF
    payload = bytes(payload)

    frame = bytes([0x33, cmd]) + bytes(payload)
    # pad frame data to 19 bytes (plus checksum)
    frame += bytes([0] * (19 - len(frame)))
    
    # The checksum is calculated by XORing all data bytes
    checksum = 0
    for b in frame:
        checksum ^= b
    
    frame += bytes([checksum & 0xFF])
    return frame

def _prepare_music_req(mode: str, effect: dict, cmd: dict):
    # 03 - rhytm, 05 - energetic, 04 - spectrum, 06 - rolling
    mapping = {
        "rhytm": 0x03,
        "energetic": 0x05,
        "spectrum": 0x04,
        "rolling": 0x06,
    }
    is_calm = effect.get("mode", "calm") == "calm"
    resp = [0x13, mapping.get(mode), effect.get("sensivity", 100), 0x01 if is_calm else 0x00]
    if rgb := cmd.get("color"):
        resp += [0x01, int(rgb["r"]), int(rgb["g"]), int(rgb["b"])]
    return resp

def _prepare_video_req(mode: str, effect: dict, cmd: dict):
    # 00 00 - Part/All 00 - Movie/Game 32 - ?? 00 - sound 63 - sound sensivity 00 - ?? 41414141 - bri 00000000000067
    is_all = mode == "all"
    is_game = effect.get("mode") == "game"
    is_sound = effect.get("sound_effect") == True
    resp = [0x00, 0x01 if is_all else 0x00, 0x01 if is_game else 0x00, 0x00, 0x01 if is_sound else 0x00]
    resp += [effect.get("sensivity", 100), 0x00]
    bri = effect.get("tv_brightness", [])
    if len(bri) != 4:
        bri = [100, 100, 100, 100]
    resp += bri
    return resp

def _prepare_color_req(rgb, temp, effect: dict):
    def _prepare_mask_bytes():
        if mask := effect.get("mask"):
            for ch in mask:
                val = (val << 1) | (1 if ch in ("1", "x", "X", "+", "#") else 0)
            return [val & 0xff, (val >> 8) & 0xff, (val >> 16) & 0xff]
        return [0xff, 0xff, 0xff]
    resp = [0x15, 0x01]
    if temp:
        resp += [0x00, 0x00, 0x00]
        color_k = _color_temperature_mired_to_kelvin(temp)
        rgb = _color_temperature_to_rgb(color_k)
        resp += [color_k & 0xff, (color_k >> 8) & 0xff] + [int(rgb[0]), int(rgb[1]), int(rgb[2])]
    else:
        resp += [int(rgb["r"]), int(rgb["g"]), int(rgb["b"])] + [0x00, 0x00, 0x00, 0x00, 0x00]
    resp += _prepare_mask_bytes()
    return resp


def handle_command(cmd: str, payload: str, model: str):
    data = dict()
    if cmd == "json":
        data = json.loads(payload)
    else:
        _LOGGER.warn(f"handle_command(): Unsupported command: {cmd}")
    if "effect" in data:
        _json = json.loads(data["effect"])
        data["_effect"] = dict(scene=_json) if isinstance(_json, str) else _json
    result = []
    if effect := data.get("_effect"):
        if scene := effect.get("scene"):
            code = H7020.get(scene)
            if code >= 0:
                _LOGGER.debug(f"handle_command(): Applying scene: {code}")
                result.append(_prepare_payload(0x05, [0x04, code & 0xff, (code >> 8) & 0xff]))
            else:
                _LOGGER.warn(f"handle_command(): Invalid scene: {scene}")
        elif music := effect.get("music"):
            result.append(_prepare_payload(0x05, _prepare_music_req(music, effect, data)))
        elif video := effect.get("video"):
            result.append(_prepare_payload(0x05, _prepare_video_req(video, effect, data)))
    elif "color" in data or "color_temp" in data:
        result.append(_prepare_payload(0x05, _prepare_color_req(data.get("color"), data.get("color_temp"), data.get("_effect", {}))))
    if "brightness" in data:
        result.append(_prepare_payload(0x04, [data["brightness"]]))
    if "state" in data:
        result.append(_prepare_payload(0x01, [0x01 if data["state"] == "ON" else 0x00]))
    return result
