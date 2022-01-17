from dataclasses import dataclass
import requests
import logging
from typing import List
from bs4 import BeautifulSoup
import esprima
import json
from datetime import timedelta

logger = logging.getLogger(__name__)


@dataclass
class DownstreamChannel:
    """Class holding information about a downstream channel"""

    id: int
    type: int
    type_name: str
    frequency: int
    width: int
    power: int
    snr: int
    modulation: int
    modulation_name: str
    correctables: int
    uncorrectables: int


@dataclass
class UpstreamChannel:
    """Class holding information about an upstream channel"""

    id: int
    type: int
    type_name: str
    frequency: int
    width: int
    power: int
    modulation: int
    modulation_name: str


@dataclass
class CableModemData:
    """Class holding data about cable modem"""

    model: str
    security: bool
    security_type: str
    boot_status: str
    cfg_status: str
    cfg_file: str
    conn_status: str
    frequency: int
    docsis_version: str
    hardware_version: str
    software_version: str
    mac: str
    serial: str
    board_temperature: float
    uptime: float
    downstream_channels: List[DownstreamChannel]
    upstream_channels: List[UpstreamChannel]


def dict_value_to_int_or_other(value: dict, to_type: dict = None) -> dict:
    if not to_type:
        to_type = {}
    return {k: to_type.get(k, int)(v) for k, v in value.items()}


def dict_key_to_int(value: dict) -> dict:
    return {int(k): v for k, v in value.items()}


def object_expression_to_dict(value: esprima.nodes.ObjectExpression) -> dict:
    return {x.key.value: x.value.value for x in value.properties}


def get_uptime_in_seconds_from_ubee_format(value: str) -> float:
    # Example input: 2 days 23h:06m:05s.00
    days = int(value.split()[0])
    rest = value.split()[-1].split(":")
    time = timedelta(
        days=days,
        hours=int(rest[0][:2]),
        minutes=int(rest[1][:2]),
        seconds=int(rest[2][:2]),
    )
    return time.total_seconds()


class CableModem:
    data: CableModemData = None

    def __init__(
        self,
        host: str = "192.168.178.1",
        ssl: bool = False,
        update_on_init: bool = True,
    ) -> None:
        self._base_url = f"{'https' if ssl else 'http'}://{host}"

        self.data: CableModemData

        logger.debug("Cable modem base URL: %s.", self._base_url)

        if update_on_init:
            self.update()

    @staticmethod
    def _parse_status(value: str) -> dict:
        # I don't know who wrote the HTML/JS/CSS for this product, but it's terrible.
        value.replace("</title><head>", "</title>")

        soup = BeautifulSoup(value, features="html.parser")

        script = [
            x.get_text()
            for x in soup.find_all("script")
            if "cm_status_json" in x.get_text()
        ]
        assert script

        parsed = esprima.parseScript(script[0])

        objects_required = ["cm_status_json"]

        raw = {}
        for obj in parsed.body:
            if not obj.declarations:
                continue
            obj = obj.declarations[0]
            if obj.id.name in objects_required:
                raw[obj.id.name] = json.loads(obj.init.value)

        return {"info": raw["cm_status_json"]}

    @staticmethod
    def _parse_conn(value: str) -> dict:
        soup = BeautifulSoup(value, features="html.parser")
        script = [
            x.get_text()
            for x in soup.find_all("script")
            if "cm_conn_json" in x.get_text()
        ]
        assert script

        parsed = esprima.parseScript(script[0])

        objects_required = ["ds_modulation", "us_modulation", "ifType", "cm_conn_json"]

        raw = {}
        for obj in parsed.body:
            if not obj.declarations:
                continue
            obj = obj.declarations[0]
            if obj.id.name in objects_required:
                if isinstance(obj.init, esprima.nodes.ObjectExpression):
                    raw[obj.id.name] = object_expression_to_dict(obj.init)
                else:
                    # We know only `cm_conn_json` is a Literal that we have to load from JSON.
                    raw[obj.id.name] = json.loads(obj.init.value)

        ds_modulations = dict_key_to_int(raw["ds_modulation"])
        us_modulations = dict_key_to_int(raw["us_modulation"])
        if_types = dict_key_to_int(raw["ifType"])
        info = raw["cm_conn_json"]

        # Special for the modem version
        info["modem_model"] = soup.find("title").get_text()

        # Process downstream channels
        downstream_chs = list()
        for channel in info["cm_conn_ds_gourpObj"]:
            channel = dict_value_to_int_or_other(channel, to_type={"ds_snr": float})
            new_channel = DownstreamChannel(
                id=channel["ds_id"],
                type=channel["ds_type"],
                type_name=if_types[channel["ds_type"]].split()[0],
                modulation=channel["ds_modulation"],
                modulation_name=ds_modulations[channel["ds_modulation"]],
                frequency=channel["ds_freq"],
                width=channel["ds_width"] * 1000
                if channel["ds_type"] == 277
                else channel["ds_width"],
                power=channel["ds_power"],
                snr=channel["ds_snr"] * 10
                if channel["ds_type"] == 277
                else channel["ds_snr"],
                correctables=channel["ds_correct"],
                uncorrectables=channel["ds_uncorrect"],
            )
            downstream_chs.append(new_channel)

        upstream_chs = list()
        for channel in info["cm_conn_us_gourpObj"]:
            channel = dict_value_to_int_or_other(channel)
            if channel["us_type"] == 278:
                logger.warning(
                    "We're missing example value for 'power' for upstream type '278'. Please report this value to the "
                    "project owner: %r",
                    channel["us_power"],
                )
            new_channel = UpstreamChannel(
                id=channel["us_id"],
                type=channel["us_type"],
                type_name=if_types[channel["us_type"]],
                modulation=channel["us_modulation"],
                modulation_name=us_modulations[channel["us_modulation"]],
                frequency=channel["us_freq"],
                width=channel["us_width"] * 1000
                if channel["us_type"] == 278
                else channel["us_width"],
                power=channel["us_power"],
            )
            upstream_chs.append(new_channel)

        return {"info": info, "ds_chs": downstream_chs, "us_chs": upstream_chs}

    def update(self) -> None:
        url = f"{self._base_url}/htdocs/cm_info_connection.php"

        data = requests.get(url)
        data.raise_for_status()

        conn_data = self._parse_conn(data.text)

        url = f"{self._base_url}/htdocs/cm_info_status.php"
        data = requests.get(url)
        data.raise_for_status()

        status_data = self._parse_status(data.text)

        conn_data["info"].update(status_data["info"])

        info = conn_data["info"]
        self.data = CableModemData(
            model=info["modem_model"],
            security=True if info["cm_conn_sec_status"] else False,
            security_type=info["cm_conn_sec_comment"],
            boot_status=info["cm_conn_boot_status"],
            cfg_status=info["cm_conn_cfg_status"],
            cfg_file=info["cm_conn_cfg_comment"],
            conn_status=info["cm_conn_conn_status"],
            frequency=int(info["cm_conn_ds_channel"]),
            docsis_version=info["cm_status_docsis_spec"],
            hardware_version=info["cm_status_hardware_version"],
            software_version=info["cm_status_software_version"],
            mac=info["cm_status_rf_mac"],
            serial=info["cm_status_sn"],
            board_temperature=float(info["cm_status_board_temperature"].split()[0]),
            uptime=get_uptime_in_seconds_from_ubee_format(
                info["cm_status_system_uptime"]
            ),
            downstream_channels=conn_data["ds_chs"],
            upstream_channels=conn_data["us_chs"],
        )
