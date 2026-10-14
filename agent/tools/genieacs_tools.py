import requests
import os
import time
from langchain.tools import tool
from datetime import datetime, timezone, timedelta

GENIEACS_URL = os.getenv("GENIEACS_URL", "http://genieacs1:7557")

# ─────────────────────────────────────────────
# Structured error codes
# ─────────────────────────────────────────────
ERR_NOT_FOUND  = "[NOT_FOUND]"
ERR_TIMEOUT    = "[TIMEOUT]"
ERR_GENIEACS   = "[GENIEACS_ERROR]"

# ─────────────────────────────────────────────
# Mock line profiles
#
# GenieACS receives DSL parameters from tr069_sim.py Inform
# messages but does not persist them without provisioning scripts.
# These mock profiles simulate the datastore values that GenieACS
# would store in a production deployment with provisioning scripts.
#
# In production: GenieACS provisioning scripts poll these parameters
# on every TR-069 Inform cycle and store them in MongoDB.
# In this lab: mock profiles provide the same data directly.
#
# Values match exactly what tr069_sim.py sends in Inform messages.
# ─────────────────────────────────────────────

MOCK_LINE_PROFILES = {

    # S01: Karim — PPPoE auth failure, healthy line
    "SIM-000005": {
        "snr_down":    "18", "snr_up":   "17",
        "attenuation": "12",
        "dl_rate":     "20000", "ul_rate": "5000",
        "wan_ip":      "",
        "conn_status": "Disconnected",
        "conn_error":  "ERROR_AUTHENTICATION_FAILURE",
        "pppoe_user":  "karim.mansouri",
        "uptime":      "0",
        "mtu":         None,
    },

    # S02: Leila — degraded line, SNR 4 dB (Poor)
    "SIM-000006": {
        "snr_down":    "4", "snr_up":    "5",
        "attenuation": "48",
        "dl_rate":     "2100", "ul_rate": "512",
        "wan_ip":      "100.64.6.3",
        "conn_status": "Connected",
        "conn_error":  "",
        "pppoe_user":  None,
        "uptime":      "7200",
        "mtu":         None,
    },

    # S03: Rami — zombie session, healthy line
    "SIM-000007": {
        "snr_down":    "20", "snr_up":   "19",
        "attenuation": "10",
        "dl_rate":     "50000", "ul_rate": "10000",
        "wan_ip":      "",
        "conn_status": "Disconnected",
        "conn_error":  "ERROR_ISP_TIME_OUT",
        "pppoe_user":  None,
        "uptime":      "320",
        "mtu":         None,
    },

    # S04: Nour — quota exhausted, healthy line
    "SIM-000008": {
        "snr_down":    "22", "snr_up":   "21",
        "attenuation": "8",
        "dl_rate":     "20000", "ul_rate": "5000",
        "wan_ip":      "",
        "conn_status": "Disconnected",
        "conn_error":  "ERROR_ISP_DISCONNECT",
        "pppoe_user":  None,
        "uptime":      "1800",
        "mtu":         None,
    },

    # S05: Tarek — outdated firmware, good line
    "SIM-000009": {
    "snr_down":    "16", "snr_up":   "15",
    "attenuation": "15",
    "dl_rate":     "40000", "ul_rate": "8000",
    "wan_ip":      "100.64.9.5",
    "conn_status": "Connected",
    "conn_error":  "",
    "pppoe_user":  None,
    "uptime":      "14400",
    "mtu":         None,
    "firmware":    "2.1.0",   # ← add this
},
    # S06: Yasmine — MTU mismatch (1500 instead of 1492)
    "SIM-000010": {
        "snr_down":    "19", "snr_up":   "18",
        "attenuation": "11",
        "dl_rate":     "50000", "ul_rate": "10000",
        "wan_ip":      "100.64.10.2",
        "conn_status": "Connected",
        "conn_error":  "",
        "pppoe_user":  None,
        "uptime":      "172800",
        "mtu":         "1500",
    },

    # S07: Amine — forgot password, excellent line
    "SIM-000011": {
        "snr_down":    "20", "snr_up":   "19",
        "attenuation": "10",
        "dl_rate":     "48000", "ul_rate": "9500",
        "wan_ip":      "",
        "conn_status": "Disconnected",
        "conn_error":  "ERROR_AUTHENTICATION_FAILURE",
        "pppoe_user":  "amine.gharbi",
        "uptime":      "0",
        "mtu":         None,
    },

    # S08: Sarra — marginal SNR 8 dB (Lost-Carrier instability)
    "SIM-000012": {
        "snr_down":    "8", "snr_up":    "7",
        "attenuation": "35",
        "dl_rate":     "8000", "ul_rate": "2000",
        "wan_ip":      "100.64.12.1",
        "conn_status": "Connected",
        "conn_error":  "",
        "pppoe_user":  None,
        "uptime":      "3600",
        "mtu":         None,
    },

    # S09: Mehdi — SNR 9 dB marginal (intermittent disconnections)
    "SIM-000013": {
        "snr_down":    "9", "snr_up":    "8",
        "attenuation": "32",
        "dl_rate":     "10000", "ul_rate": "2500",
        "wan_ip":      "100.64.13.1",
        "conn_status": "Connected",
        "conn_error":  "",
        "pppoe_user":  None,
        "uptime":      "1800",
        "mtu":         None,
    },

    # S10: Ines — good SNR but low rate (speed below plan)
    "SIM-000014": {
        "snr_down":    "18", "snr_up":   "17",
        "attenuation": "14",
        "dl_rate":     "12000", "ul_rate": "2500",
        "wan_ip":      "100.64.14.1",
        "conn_status": "Connected",
        "conn_error":  "",
        "pppoe_user":  None,
        "uptime":      "86400",
        "mtu":         None,
    },

    # S11: Omar — suspended account, healthy device
    "SIM-000015": {
        "snr_down":    "17", "snr_up":   "16",
        "attenuation": "13",
        "dl_rate":     "19000", "ul_rate": "4800",
        "wan_ip":      "",
        "conn_status": "Disconnected",
        "conn_error":  "ERROR_AUTHENTICATION_FAILURE",
        "pppoe_user":  None,
        "uptime":      "500",
        "mtu":         None,
    },

    # S12: Rim — excellent line but high latency/packet loss
    "SIM-000016": {
        "snr_down":    "19", "snr_up":   "18",
        "attenuation": "11",
        "dl_rate":     "48000", "ul_rate": "9500",
        "wan_ip":      "100.64.16.1",
        "conn_status": "Connected",
        "conn_error":  "",
        "pppoe_user":  None,
        "uptime":      "43200",
        "mtu":         None,
    },

    # S13: Bilel — good line, quota issue
    "SIM-000017": {
        "snr_down":    "21", "snr_up":   "20",
        "attenuation": "9",
        "dl_rate":     "18000", "ul_rate": "4500",
        "wan_ip":      "",
        "conn_status": "Disconnected",
        "conn_error":  "ERROR_ISP_DISCONNECT",
        "pppoe_user":  None,
        "uptime":      "0",
        "mtu":         None,
    },

    # S14: Dorra — perfect line, WiFi issue (not internet)
    "SIM-000018": {
        "snr_down":    "22", "snr_up":   "21",
        "attenuation": "8",
        "dl_rate":     "50000", "ul_rate": "10000",
        "wan_ip":      "100.64.18.1",
        "conn_status": "Connected",
        "conn_error":  "",
        "pppoe_user":  None,
        "uptime":      "259200",
        "mtu":         None,
    },

    # S15: Sofien — factory reset scenario
    # Wrong username on device, all previous fixes failed
    "SIM-000019": {
        "snr_down":    "15", "snr_up":   "14",
        "attenuation": "18",
        "dl_rate":     "35000", "ul_rate": "7000",
        "wan_ip":      "",
        "conn_status": "Disconnected",
        "conn_error":  "ERROR_AUTHENTICATION_FAILURE",
        "pppoe_user":  "sofien.nasr_old",
        "uptime":      "120",
        "mtu":         None,
    },
}


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _find_device_id(serial_number: str) -> str | None:
    """Return the full GenieACS device _id for a given serial number, or None."""
    for query in [
        f'{{"_id":{{"$regex":"{serial_number.replace("-", "%2D")}"}}}}',
        f'{{"_id":{{"$regex":"{serial_number}"}}}}',
    ]:
        try:
            resp = requests.get(
                f"{GENIEACS_URL}/devices",
                params={"query": query},
                timeout=5,
            )
            devices = resp.json()
            if devices:
                return devices[0].get("_id")
        except requests.Timeout:
            return None
        except Exception:
            return None
    return None


def _get_device_raw(device_id: str) -> dict:
    """Fetch full device document from GenieACS by device_id."""
    resp = requests.get(
        f"{GENIEACS_URL}/devices",
        params={"query": f'{{"_id":"{device_id}"}}'},
        timeout=5,
    )
    devices = resp.json()
    return devices[0] if devices else {}


def _val(d: dict, *path) -> str:
    """Traverse a nested GenieACS document and return _value."""
    node = d
    for key in path:
        if not isinstance(node, dict):
            return "N/A"
        node = node.get(key, {})
    if isinstance(node, dict):
        return str(node.get("_value", "N/A"))
    return "N/A"


def _online_status(last_inform_str: str | None) -> str:
    """Determine online status based on last inform timestamp."""
    if not last_inform_str:
        return "Never connected"
    now         = datetime.now(timezone.utc)
    last_inform = datetime.fromisoformat(last_inform_str.replace("Z", "+00:00"))
    diff        = now - last_inform
    if diff < timedelta(minutes=2):
        return "Online"
    if diff < timedelta(hours=24):
        return f"Offline (last seen {int(diff.total_seconds() / 60)} min ago)"
    return f"Offline (last seen {diff.days} days ago)"


def _snr_quality(val: str) -> str:
    """Return SNR value with quality label."""
    try:
        v = float(val)
        if v >= 15:
            return f"{v} dB (Good)"
        if v >= 6:
            return f"{v} dB (Marginal — may cause instability)"
        return f"{v} dB (Poor — likely causing disconnections)"
    except Exception:
        return f"{val} dB"


def _post_task(device_id: str, task: dict, timeout_s: int = 10, retries: int = 2) -> dict:
    """POST a task to GenieACS NBI with retry logic."""
    encoded_id = requests.utils.quote(device_id, safe="")
    last_error = "Unknown error"

    for attempt in range(retries + 1):
        try:
            resp = requests.post(
                f"{GENIEACS_URL}/devices/{encoded_id}/tasks",
                json=task,
                timeout=timeout_s,
            )
            if resp.status_code in (200, 202):
                try:
                    task_id = resp.json().get("_id", "unknown")
                except Exception:
                    task_id = "unknown"
                return {"success": True, "status": "queued", "task_id": task_id}
            last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
        except requests.Timeout:
            last_error = f"Timeout after {timeout_s}s (attempt {attempt + 1}/{retries + 1})"
            if attempt < retries:
                time.sleep(1)
            continue
        except Exception as e:
            return {"success": False, "status": str(e), "task_id": None}

    return {"success": False, "status": last_error, "task_id": None}


# ─────────────────────────────────────────────
# READ tools
# ─────────────────────────────────────────────

@tool
def list_devices(limit: int = 10) -> str:
    """
    List TR-069 devices registered in GenieACS with their online status.
    Admin/NOC tool — not for customer support sessions.
    """
    try:
        resp    = requests.get(f"{GENIEACS_URL}/devices", timeout=5)
        devices = resp.json()
        result  = []
        for d in devices[:limit]:
            device_id = d.get("_id", "unknown")
            parts     = device_id.split("-", 2)
            serial    = parts[2] if len(parts) == 3 else device_id
            status    = _online_status(d.get("_lastInform"))
            result.append(f"- {serial} | {status}")
        return f"Found {len(devices)} devices:\n" + "\n".join(result)
    except requests.Timeout:
        return f"{ERR_TIMEOUT} GenieACS did not respond in time."
    except Exception as e:
        return f"{ERR_GENIEACS} {e}"


@tool
def get_device(serial_number: str) -> str:
    """
    Get status and basic info of a specific CPE device by its serial number.
    Returns online status, firmware, hardware version, uptime, and WAN IP.
    Use this at the start of any diagnostic flow to check if device is reachable.
    """
    try:
        device_id = _find_device_id(serial_number)
        if not device_id:
            return f"{ERR_NOT_FOUND} Device '{serial_number}' not found in GenieACS."

        d      = _get_device_raw(device_id)
        if not d:
            return f"{ERR_NOT_FOUND} Device '{serial_number}' not found."

        status     = _online_status(d.get("_lastInform"))
        sw_version = _val(d, "Device", "DeviceInfo", "SoftwareVersion")
        hw_version = _val(d, "Device", "DeviceInfo", "HardwareVersion")
        uptime     = _val(d, "Device", "DeviceInfo", "UpTime")

        # WAN IP — try PPPoE first then IPoE
        wan_ip    = _val(d, "Device", "WANDevice", "1", "WANConnectionDevice", "1",
                         "WANPPPConnection", "1", "ExternalIPAddress")
        conn_type = "PPPoE"
        if wan_ip in ("N/A", ""):
            wan_ip    = _val(d, "Device", "WANDevice", "1", "WANConnectionDevice", "1",
                             "WANIPConnection", "1", "ExternalIPAddress")
            conn_type = "IPoE/DHCP"

        # Fall back to mock profile for WAN IP if GenieACS doesn't have it
        mock = MOCK_LINE_PROFILES.get(serial_number)
        if wan_ip in ("N/A", "") and mock:
            wan_ip = mock.get("wan_ip", "")

        lines = [
            f"Serial: {serial_number}",
            f"Status: {status}",
            f"Firmware: {sw_version}",
            f"Hardware: {hw_version}",
            f"Uptime: {uptime} seconds",
            f"WAN IP: {wan_ip or 'none (not connected)'}",
            f"Connection type: {conn_type}",
        ]
        return "\n".join(lines)

    except requests.Timeout:
        return f"{ERR_TIMEOUT} GenieACS did not respond in time for '{serial_number}'."
    except Exception as e:
        return f"{ERR_GENIEACS} {e}"


@tool
def count_online_devices() -> str:
    """
    Count how many CPE devices are currently online vs offline.
    Admin/NOC monitoring tool — not for customer support sessions.
    """
    try:
        resp    = requests.get(f"{GENIEACS_URL}/devices", timeout=5)
        devices = resp.json()
        online = offline = never = 0
        for d in devices:
            s = _online_status(d.get("_lastInform"))
            if s == "Online":
                online += 1
            elif s == "Never connected":
                never += 1
            else:
                offline += 1
        return (
            f"Total devices: {len(devices)}\n"
            f"Online (last seen < 2 min): {online}\n"
            f"Offline: {offline}\n"
            f"Never connected: {never}"
        )
    except requests.Timeout:
        return f"{ERR_TIMEOUT} GenieACS did not respond in time."
    except Exception as e:
        return f"{ERR_GENIEACS} {e}"


@tool
def get_device_parameters(serial_number: str) -> str:
    """
    Fetch CPE diagnostic parameters: SNR downstream/upstream, line attenuation,
    current rates, WAN IP, connection status, firmware version, uptime, and MTU.
    Use this to diagnose line quality issues, MTU problems, or PPPoE failures.

    For lab demo devices (SIM-000005 to SIM-000019), parameters are provided
    via mock profiles that simulate GenieACS production datastore values.
    For other devices, parameters are queried directly from GenieACS.
    """
    try:
        device_id = _find_device_id(serial_number)
        if not device_id:
            return f"{ERR_NOT_FOUND} Device '{serial_number}' not found in GenieACS."

        d = _get_device_raw(device_id)
        if not d:
            return f"{ERR_NOT_FOUND} Device '{serial_number}' not found."

        status = _online_status(d.get("_lastInform"))
        fw     = _val(d, "Device", "DeviceInfo", "SoftwareVersion")

        # ── Check mock profile first (demo devices) ───────────────
        mock = MOCK_LINE_PROFILES.get(serial_number)

        if mock:
            # Use mock data for DSL parameters
            uptime     = mock.get("uptime", "N/A")
            snr_down   = mock.get("snr_down")
            snr_up     = mock.get("snr_up")
            atten      = mock.get("attenuation")
            dl_rate    = mock.get("dl_rate")
            ul_rate    = mock.get("ul_rate")
            wan_ip     = mock.get("wan_ip", "")
            conn_status = mock.get("conn_status", "N/A")
            conn_error = mock.get("conn_error", "")
            pppoe_user = mock.get("pppoe_user")
            mtu        = mock.get("mtu")
        else:
            # Query GenieACS directly for unknown/production devices
            uptime = _val(d, "Device", "DeviceInfo", "UpTime")

            dsl       = ("Device", "WANDevice", "1", "WANDSLInterfaceConfig")
            snr_down  = _val(d, *dsl, "DownstreamSNRMargin")
            snr_up    = _val(d, *dsl, "UpstreamSNRMargin")
            atten     = _val(d, *dsl, "DownstreamAttenuation")
            dl_rate   = _val(d, *dsl, "DownstreamCurrRate")
            ul_rate   = _val(d, *dsl, "UpstreamCurrRate")

            pppoe     = ("Device", "WANDevice", "1", "WANConnectionDevice", "1",
                         "WANPPPConnection", "1")
            conn_status = _val(d, *pppoe, "ConnectionStatus")
            conn_error  = _val(d, *pppoe, "LastConnectionError")
            pppoe_user  = _val(d, *pppoe, "Username")
            wan_ip      = _val(d, "Device", "WANDevice", "1", "WANConnectionDevice",
                               "1", "WANIPConnection", "1", "ExternalIPAddress")

            mtu_pppoe  = _val(d, *pppoe, "MaxMTUSize")
            mtu_ipoe   = _val(d, "Device", "WANDevice", "1", "WANConnectionDevice",
                              "1", "WANIPConnection", "1", "MaxMTUSize")
            mtu = mtu_pppoe if mtu_pppoe != "N/A" else (
                mtu_ipoe if mtu_ipoe != "N/A" else None
            )

        # ── Build response ────────────────────────────────────────
        lines = [
            f"Device: {serial_number}",
            f"Status: {status}",
            f"Firmware: {fw}",
            f"Uptime: {uptime} seconds",
        ]

        # Connection info
        if conn_status and conn_status != "N/A":
            lines.append(f"PPPoE/Connection status: {conn_status}")
        if pppoe_user and pppoe_user != "N/A":
            lines.append(f"PPPoE username on device: {pppoe_user}")
        if conn_error and conn_error not in ("N/A", "", "ERROR_NONE"):
            lines.append(f"Last connection error: {conn_error}")

        if wan_ip:
            lines.append(f"WAN IP: {wan_ip}")
        else:
            lines.append("WAN IP: none (not connected)")

        # MTU
        if mtu:
            mtu_note = " ⚠️ PPPoE requires 1492" if str(mtu) == "1500" else ""
            lines.append(f"MTU configured: {mtu}{mtu_note}")

        # DSL line quality
        if snr_down and snr_down != "N/A":
            lines.append(f"SNR downstream: {_snr_quality(str(snr_down))}")
        if snr_up and snr_up != "N/A":
            lines.append(f"SNR upstream: {_snr_quality(str(snr_up))}")
        if atten and atten != "N/A":
            lines.append(f"Line attenuation: {atten} dB")
        if dl_rate and dl_rate != "N/A":
            lines.append(f"Download rate: {dl_rate} kbps")
        if ul_rate and ul_rate != "N/A":
            lines.append(f"Upload rate: {ul_rate} kbps")

        if not mock and all(
            v in ("N/A", None) for v in [snr_down, snr_up, dl_rate]
        ):
            lines.append("DSL line parameters: not yet reported by device.")

        return "\n".join(lines)

    except requests.Timeout:
        return f"{ERR_TIMEOUT} GenieACS did not respond in time for '{serial_number}'."
    except Exception as e:
        return f"{ERR_GENIEACS} {e}"


# ─────────────────────────────────────────────
# INTERVENTION tools
# ─────────────────────────────────────────────

@tool
def reboot_device(serial_number: str) -> str:
    """
    Send a TR-069 remote reboot command to the CPE device.
    Use when the device is online but experiencing connectivity issues,
    after completing diagnosis. Inform the customer before rebooting.
    Device will restart in ~10-30 seconds and reconnect within 60-90 seconds.
    """
    try:
        device_id = _find_device_id(serial_number)
        if not device_id:
            return f"{ERR_NOT_FOUND} Device '{serial_number}' not found in GenieACS."

        d      = _get_device_raw(device_id)
        if not d:
            return f"{ERR_NOT_FOUND} Device '{serial_number}' not found."

        status = _online_status(d.get("_lastInform"))
        result = _post_task(device_id, {"name": "reboot"}, timeout_s=5)

        if result["success"]:
            if "Offline" in status:
                return (
                    f"Reboot command queued for {serial_number} (task: {result['task_id']}).\n"
                    "Device is between inform cycles — will reboot on next contact.\n"
                    "Advise customer to wait up to 3 minutes for reconnection."
                )
            return (
                f"Reboot command sent to {serial_number} (task: {result['task_id']}).\n"
                "Device will restart in ~10-30 seconds and reconnect within 60-90 seconds.\n"
                "Advise customer to wait 2 minutes before testing connection."
            )
        return f"{ERR_GENIEACS} Reboot failed: {result['status']}"

    except requests.Timeout:
        return f"{ERR_TIMEOUT} GenieACS did not respond in time for '{serial_number}'."
    except Exception as e:
        return f"{ERR_GENIEACS} {e}"


@tool
def factory_reset_device(serial_number: str) -> str:
    """
    Send a TR-069 factory reset command to the CPE device.
    ONLY use as last resort after reboot and config push have failed,
    and ONLY after explicit customer confirmation.
    All custom settings will be erased. GenieACS will re-provision
    the device automatically after reset.
    """
    try:
        device_id = _find_device_id(serial_number)
        if not device_id:
            return f"{ERR_NOT_FOUND} Device '{serial_number}' not found in GenieACS."

        result = _post_task(device_id, {"name": "factoryReset"}, timeout_s=20)
        if result["success"]:
            return (
                f"Factory reset command sent to {serial_number} (task: {result['task_id']}).\n"
                "WARNING: All device configuration has been erased.\n"
                "GenieACS will re-provision the device automatically when it reconnects (2-5 minutes).\n"
                "If device does not reconnect after 10 minutes, a technician visit is required."
            )
        return f"{ERR_GENIEACS} Factory reset failed: {result['status']}"

    except requests.Timeout:
        return f"{ERR_TIMEOUT} GenieACS did not respond in time for '{serial_number}'."
    except Exception as e:
        return f"{ERR_GENIEACS} {e}"


@tool
def push_pppoe_credentials(serial_number: str, username: str, password: str) -> str:
    """
    Push new PPPoE credentials to the CPE device via TR-069 SetParameterValues.
    Use when the customer changed their RADIUS password and the router needs updating,
    or when diagnosing PPPoE authentication failures (ERROR_AUTHENTICATION_FAILURE).
    Always call change_radius_password first, then this tool to sync the router.
    Device will attempt PPPoE reconnection automatically after the push.
    """
    try:
        device_id = _find_device_id(serial_number)
        if not device_id:
            return f"{ERR_NOT_FOUND} Device '{serial_number}' not found in GenieACS."

        task = {
            "name": "setParameterValues",
            "parameterValues": [
                [
                    "Device.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.Username",
                    username, "xsd:string",
                ],
                [
                    "Device.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.Password",
                    password, "xsd:string",
                ],
            ],
        }
        result = _post_task(device_id, task, timeout_s=20)
        if result["success"]:
            return (
                f"PPPoE credentials pushed to {serial_number} (task: {result['task_id']}).\n"
                f"Username set to: {username}\n"
                "Device will attempt PPPoE reconnection within 30 seconds.\n"
                "If still disconnected after 2 minutes, check RADIUS account status."
            )
        return f"{ERR_GENIEACS} Credential push failed: {result['status']}"

    except requests.Timeout:
        return f"{ERR_TIMEOUT} GenieACS did not respond in time for '{serial_number}'."
    except Exception as e:
        return f"{ERR_GENIEACS} {e}"


@tool
def set_mtu(serial_number: str, mtu_value: int = 1492) -> str:
    """
    Push a new MTU value to the CPE device via TR-069 SetParameterValues.
    Default ISP MTU for PPPoE is 1492. Use when customer reports large files
    or videos stalling while basic browsing works fine (classic MTU mismatch).
    Sets MTU on both PPPoE and IPoE connection interfaces.
    """
    try:
        if not (576 <= mtu_value <= 1500):
            return f"{ERR_GENIEACS} Invalid MTU {mtu_value}. Must be between 576 and 1500."

        device_id = _find_device_id(serial_number)
        if not device_id:
            return f"{ERR_NOT_FOUND} Device '{serial_number}' not found in GenieACS."

        task = {
            "name": "setParameterValues",
            "parameterValues": [
                [
                    "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.MaxMTUSize",
                    mtu_value, "xsd:unsignedInt",
                ],
                [
                    "Device.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.MaxMTUSize",
                    mtu_value, "xsd:unsignedInt",
                ],
            ],
        }
        result = _post_task(device_id, task, timeout_s=20)
        if result["success"]:
            return (
                f"MTU set to {mtu_value} on {serial_number} (task: {result['task_id']}).\n"
                "Reconnection will happen automatically within 30 seconds.\n"
                "Standard values: 1492 (PPPoE), 1500 (DHCP/IPoE)."
            )
        return f"{ERR_GENIEACS} MTU push failed: {result['status']}"

    except requests.Timeout:
        return f"{ERR_TIMEOUT} GenieACS did not respond in time for '{serial_number}'."
    except Exception as e:
        return f"{ERR_GENIEACS} {e}"


@tool
def force_connection_request(serial_number: str) -> str:
    """
    Force the CPE to contact GenieACS immediately via TR-069 Connection Request.
    Use after pushing config changes or rebooting to verify the device responded,
    without waiting for the next periodic inform interval (usually 15-30 minutes).
    Waits for CPE response to confirm the device is reachable.
    """
    try:
        device_id = _find_device_id(serial_number)
        if not device_id:
            return f"{ERR_NOT_FOUND} Device '{serial_number}' not found in GenieACS."

        encoded_id = requests.utils.quote(device_id, safe="")
        try:
            resp = requests.post(
                f"{GENIEACS_URL}/devices/{encoded_id}/tasks?connection_request",
                json={
                    "name": "getParameterValues",
                    "parameterNames": ["Device.DeviceInfo.UpTime"],
                },
                timeout=15,
            )
            if resp.status_code in (200, 202):
                return (
                    f"Connection request confirmed for {serial_number}.\n"
                    "Device responded and checked in with GenieACS. "
                    "Use get_device to verify updated status."
                )
            return (
                f"{ERR_GENIEACS} Connection request failed: HTTP {resp.status_code}.\n"
                "Device may be offline or unreachable. Use get_device to check status."
            )
        except requests.Timeout:
            return (
                f"{ERR_TIMEOUT} Connection request sent to {serial_number} "
                "but device did not respond within 15 seconds.\n"
                "Device may be rebooting or temporarily offline. "
                "Try get_device in 1-2 minutes to check status."
            )

    except Exception as e:
        return f"{ERR_GENIEACS} {e}"


@tool
def check_firmware_version(serial_number: str) -> str:
    """..."""
    MIN_FIRMWARE = "3.0.0"   # minimum required for all devices

    try:
        device_id = _find_device_id(serial_number)
        if not device_id:
            return f"{ERR_NOT_FOUND} Device '{serial_number}' not found."

        d  = _get_device_raw(device_id)
        if not d:
            return f"{ERR_NOT_FOUND} Device '{serial_number}' not found."

        # Use mock profile firmware if available (lab environment)
        mock = MOCK_LINE_PROFILES.get(serial_number)
        if mock and mock.get("firmware"):
            fw = mock["firmware"]
        else:
            fw = _val(d, "Device", "DeviceInfo", "SoftwareVersion")

        if fw == "N/A":
            return (
                f"Firmware version not available for {serial_number}. "
                "Device may be offline."
            )

        def ver_tuple(v: str) -> tuple:
            try:
                return tuple(int(x) for x in v.strip().lstrip("vV").split("."))
            except Exception:
                return (0, 0, 0)

        current = ver_tuple(fw)
        minimum = ver_tuple(MIN_FIRMWARE)

        if current >= minimum:
            status         = "Up to date"
            recommendation = "No firmware update needed."
        else:
            status         = "Outdated"
            recommendation = (
                f"Firmware upgrade required: {fw} → {MIN_FIRMWARE}. "
                "Outdated firmware can cause PPPoE instability and Lost-Carrier disconnections. "
                "Schedule a firmware update — technician visit required."
            )

        return (
            f"Device: {serial_number}\n"
            f"Current firmware: {fw}\n"
            f"Minimum required: {MIN_FIRMWARE}\n"
            f"Status: {status}\n"
            f"Recommendation: {recommendation}"
        )
    except Exception as e:
        return f"{ERR_GENIEACS} {e}"