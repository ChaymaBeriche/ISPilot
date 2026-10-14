import requests, os, time, threading, random

ACS_URL     = os.getenv("ACS_URL", "http://genieacs-cwmp:7547")
NUM_DEVICES = int(os.getenv("NUM_DEVICES", "19"))

# ─────────────────────────────────────────────────────────────────
# Device profiles
#
# Devices 1-4:   offline devices (Ahmed, Fatima, Mohamed, Sonia)
# Devices 5-10:  noop slots (owned by scenario devices 11-16)
# Devices 11-16: original 6 scenario devices (Karim→SIM-000005 etc.)
# Devices 17-25: new scenario devices (SIM-000011 to SIM-000019)
# ─────────────────────────────────────────────────────────────────

DEVICE_PROFILES = {

    # ── Offline devices 1-4 ──────────────────────────────────────
    1: {"type": "offline", "description": "Offline - Lost-Carrier user (Ahmed)"},
    2: {"type": "offline", "description": "Offline - Plan exhausted user (Fatima)"},
    3: {"type": "offline", "description": "Offline - Auth failure user (Mohamed)"},
    4: {"type": "offline", "description": "Offline - Mixed issues (Sonia)"},

    # ── Noop slots 5-10 (owned by scenario devices 11-16) ────────
    5:  {"type": "noop", "description": "Reserved - handled by device 11 (SIM-000005)"},
    6:  {"type": "noop", "description": "Reserved - handled by device 12 (SIM-000006)"},
    7:  {"type": "noop", "description": "Reserved - handled by device 13 (SIM-000007)"},
    8:  {"type": "noop", "description": "Reserved - handled by device 14 (SIM-000008)"},
    9:  {"type": "noop", "description": "Reserved - handled by device 15 (SIM-000009)"},
    10: {"type": "noop", "description": "Reserved - handled by device 16 (SIM-000010)"},

    # ── S01: Karim Mansouri — PPPoE auth failure ──────────────────
    # Device is healthy, line is fine. Problem: wrong PPPoE credentials
    # on the router (stale password). Router keeps trying and failing.
    11: {
        "type": "healthy",
        "interval": 30,
        "serial_override": "SIM-000005",
        "description": "Karim - online but wrong PPPoE creds on router",
        "params": {
            "Device.DeviceInfo.SoftwareVersion": "4.1.2",
            "Device.DeviceInfo.HardwareVersion": "v2",
            "Device.DeviceInfo.UpTime": "0",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamSNRMargin": "18",
            "Device.WANDevice.1.WANDSLInterfaceConfig.UpstreamSNRMargin": "17",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamAttenuation": "12",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamCurrRate": "20000",
            "Device.WANDevice.1.WANDSLInterfaceConfig.UpstreamCurrRate": "5000",
            "Device.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.ConnectionStatus": "Disconnected",
            "Device.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.LastConnectionError": "ERROR_AUTHENTICATION_FAILURE",
            "Device.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.Username": "karim.mansouri",
            "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ExternalIPAddress": "",
            "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ConnectionStatus": "Disconnected",
        },
    },

    # ── S02: Leila Hamdi — degraded line / low SNR ───────────────
    # Online and connected but DSL line quality is poor (SNR 4 dB).
    # Causes speed throttling and NAS-Error disconnections.
    12: {
        "type": "healthy",
        "interval": 90,
        "serial_override": "SIM-000006",
        "description": "Leila - online but degraded line / low SNR 4 dB",
        "params": {
            "Device.DeviceInfo.SoftwareVersion": "4.1.2",
            "Device.DeviceInfo.HardwareVersion": "v2",
            "Device.DeviceInfo.UpTime": "7200",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamSNRMargin": "4",
            "Device.WANDevice.1.WANDSLInterfaceConfig.UpstreamSNRMargin": "5",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamAttenuation": "48",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamCurrRate": "2100",
            "Device.WANDevice.1.WANDSLInterfaceConfig.UpstreamCurrRate": "512",
            "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ExternalIPAddress": "100.64.6.3",
            "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ConnectionStatus": "Connected",
            "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ConnectionType": "IP_Routed",
        },
    },

    # ── S03: Rami Bouazizi — zombie RADIUS session ────────────────
    # Device is healthy. Problem is in RADIUS DB: open zombie session
    # blocks new connections. Device keeps trying but RADIUS rejects.
    13: {
        "type": "healthy",
        "interval": 30,
        "serial_override": "SIM-000007",
        "description": "Rami - online device but zombie RADIUS session blocks reconnect",
        "params": {
            "Device.DeviceInfo.SoftwareVersion": "4.2.0",
            "Device.DeviceInfo.HardwareVersion": "v2",
            "Device.DeviceInfo.UpTime": "320",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamSNRMargin": "20",
            "Device.WANDevice.1.WANDSLInterfaceConfig.UpstreamSNRMargin": "19",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamAttenuation": "10",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamCurrRate": "50000",
            "Device.WANDevice.1.WANDSLInterfaceConfig.UpstreamCurrRate": "10000",
            "Device.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.ConnectionStatus": "Disconnected",
            "Device.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.LastConnectionError": "ERROR_ISP_TIME_OUT",
            "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ExternalIPAddress": "",
            "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ConnectionStatus": "Disconnected",
        },
    },

    # ── S04: Nour Sfaxi — quota exhausted ────────────────────────
    # Device is online. Problem: Session-Timeout quota hit.
    # Line quality is perfect — purely a billing/quota issue.
    14: {
        "type": "healthy",
        "interval": 30,
        "serial_override": "SIM-000008",
        "description": "Nour - quota exhausted, Session-Timeout disconnect",
        "params": {
            "Device.DeviceInfo.SoftwareVersion": "4.1.2",
            "Device.DeviceInfo.HardwareVersion": "v2",
            "Device.DeviceInfo.UpTime": "1800",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamSNRMargin": "22",
            "Device.WANDevice.1.WANDSLInterfaceConfig.UpstreamSNRMargin": "21",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamAttenuation": "8",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamCurrRate": "20000",
            "Device.WANDevice.1.WANDSLInterfaceConfig.UpstreamCurrRate": "5000",
            "Device.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.ConnectionStatus": "Disconnected",
            "Device.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.LastConnectionError": "ERROR_ISP_DISCONNECT",
            "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ExternalIPAddress": "",
            "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ConnectionStatus": "Disconnected",
        },
    },

    # ── S05: Tarek Belhaj — outdated firmware ────────────────────
    # Firmware 2.1.0 < minimum 3.0.0. Known PPPoE keepalive bug
    # causes Lost-Carrier every ~3h. Currently connected but will drop.
    15: {
        "type": "healthy",
        "interval": 30,
        "serial_override": "SIM-000009",
        "description": "Tarek - outdated firmware 2.1.0, periodic Lost-Carrier drops",
        "params": {
            "Device.DeviceInfo.SoftwareVersion": "2.1.0",
            "Device.DeviceInfo.HardwareVersion": "v1",
            "Device.DeviceInfo.UpTime": "14400",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamSNRMargin": "16",
            "Device.WANDevice.1.WANDSLInterfaceConfig.UpstreamSNRMargin": "15",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamAttenuation": "15",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamCurrRate": "40000",
            "Device.WANDevice.1.WANDSLInterfaceConfig.UpstreamCurrRate": "8000",
            "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ExternalIPAddress": "100.64.9.5",
            "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ConnectionStatus": "Connected",
            "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ConnectionType": "IP_Routed",
        },
    },

    # ── S06: Yasmine Karray — MTU mismatch ───────────────────────
    # MTU set to 1500 on PPPoE line (should be 1492).
    # Large packets fragmented → videos/downloads stall, browsing OK.
    16: {
        "type": "healthy",
        "interval": 30,
        "serial_override": "SIM-000010",
        "description": "Yasmine - MTU 1500 on PPPoE line (should be 1492)",
        "params": {
            "Device.DeviceInfo.SoftwareVersion": "4.2.0",
            "Device.DeviceInfo.HardwareVersion": "v2",
            "Device.DeviceInfo.UpTime": "172800",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamSNRMargin": "19",
            "Device.WANDevice.1.WANDSLInterfaceConfig.UpstreamSNRMargin": "18",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamAttenuation": "11",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamCurrRate": "50000",
            "Device.WANDevice.1.WANDSLInterfaceConfig.UpstreamCurrRate": "10000",
            "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ExternalIPAddress": "100.64.10.2",
            "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ConnectionStatus": "Connected",
            "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ConnectionType": "IP_Routed",
            "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.MaxMTUSize": "1500",
            "Device.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.MaxMTUSize": "1500",
        },
    },

    # ── S07: Amine Gharbi — forgot password ──────────────────────
    # Device is healthy and online. Customer forgot their PPPoE password.
    # Router has wrong password → auth failures.
    # Agent should reset password + push credentials.
    17: {
        "type": "healthy",
        "interval": 30,
        "serial_override": "SIM-000011",
        "description": "Amine - forgot password, auth failures, healthy device",
        "params": {
            "Device.DeviceInfo.SoftwareVersion": "4.2.0",
            "Device.DeviceInfo.HardwareVersion": "v2",
            "Device.DeviceInfo.UpTime": "0",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamSNRMargin": "20",
            "Device.WANDevice.1.WANDSLInterfaceConfig.UpstreamSNRMargin": "19",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamAttenuation": "10",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamCurrRate": "48000",
            "Device.WANDevice.1.WANDSLInterfaceConfig.UpstreamCurrRate": "9500",
            "Device.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.ConnectionStatus": "Disconnected",
            "Device.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.LastConnectionError": "ERROR_AUTHENTICATION_FAILURE",
            "Device.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.Username": "amine.gharbi",
            "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ExternalIPAddress": "",
            "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ConnectionStatus": "Disconnected",
        },
    },

    # ── S08: Sarra Mejri — Lost-Carrier instability (SNR 8 dB) ───
    # Marginal line quality. SNR 8 dB is above threshold but causes
    # instability under load. Lost-Carrier 6x in last 24h.
    18: {
        "type": "healthy",
        "interval": 60,
        "serial_override": "SIM-000012",
        "description": "Sarra - marginal SNR 8 dB, Lost-Carrier instability",
        "params": {
            "Device.DeviceInfo.SoftwareVersion": "4.1.2",
            "Device.DeviceInfo.HardwareVersion": "v2",
            "Device.DeviceInfo.UpTime": "3600",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamSNRMargin": "8",
            "Device.WANDevice.1.WANDSLInterfaceConfig.UpstreamSNRMargin": "7",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamAttenuation": "35",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamCurrRate": "8000",
            "Device.WANDevice.1.WANDSLInterfaceConfig.UpstreamCurrRate": "2000",
            "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ExternalIPAddress": "100.64.12.1",
            "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ConnectionStatus": "Connected",
            "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ConnectionType": "IP_Routed",
        },
    },

    # ── S09: Mehdi Triki — intermittent disconnections (SNR 9 dB) ─
    # Short sessions 15-30 min repeatedly. SNR 9 dB marginal.
    # Packet loss 3.2% causing instability. Not a quota issue.
    19: {
        "type": "healthy",
        "interval": 45,
        "serial_override": "SIM-000013",
        "description": "Mehdi - intermittent disconnections, SNR 9 dB, marginal line",
        "params": {
            "Device.DeviceInfo.SoftwareVersion": "4.1.2",
            "Device.DeviceInfo.HardwareVersion": "v2",
            "Device.DeviceInfo.UpTime": "1800",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamSNRMargin": "9",
            "Device.WANDevice.1.WANDSLInterfaceConfig.UpstreamSNRMargin": "8",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamAttenuation": "32",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamCurrRate": "10000",
            "Device.WANDevice.1.WANDSLInterfaceConfig.UpstreamCurrRate": "2500",
            "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ExternalIPAddress": "100.64.13.1",
            "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ConnectionStatus": "Connected",
            "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ConnectionType": "IP_Routed",
        },
    },

    # ── S10: Ines Chaabane — speed below plan ─────────────────────
    # Premium plan = 50 Mbps. Getting only 12 Mbps.
    # Line SNR is good (18 dB) — not a line quality issue.
    # Speed issue is network-side (congestion, provisioning problem).
    20: {
        "type": "healthy",
        "interval": 30,
        "serial_override": "SIM-000014",
        "description": "Ines - premium plan 50Mbps but only getting 12Mbps",
        "params": {
            "Device.DeviceInfo.SoftwareVersion": "4.2.0",
            "Device.DeviceInfo.HardwareVersion": "v2",
            "Device.DeviceInfo.UpTime": "86400",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamSNRMargin": "18",
            "Device.WANDevice.1.WANDSLInterfaceConfig.UpstreamSNRMargin": "17",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamAttenuation": "14",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamCurrRate": "12000",
            "Device.WANDevice.1.WANDSLInterfaceConfig.UpstreamCurrRate": "2500",
            "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ExternalIPAddress": "100.64.14.1",
            "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ConnectionStatus": "Connected",
            "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ConnectionType": "IP_Routed",
        },
    },

    # ── S11: Omar Hadj — suspended account ───────────────────────
    # Device is online and physically healthy.
    # Account is suspended in users table and RADIUS rejects auth.
    # Agent should detect suspension and redirect to billing.
    21: {
        "type": "healthy",
        "interval": 30,
        "serial_override": "SIM-000015",
        "description": "Omar - suspended account, device healthy but RADIUS rejects",
        "params": {
            "Device.DeviceInfo.SoftwareVersion": "4.1.2",
            "Device.DeviceInfo.HardwareVersion": "v2",
            "Device.DeviceInfo.UpTime": "500",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamSNRMargin": "17",
            "Device.WANDevice.1.WANDSLInterfaceConfig.UpstreamSNRMargin": "16",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamAttenuation": "13",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamCurrRate": "19000",
            "Device.WANDevice.1.WANDSLInterfaceConfig.UpstreamCurrRate": "4800",
            "Device.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.ConnectionStatus": "Disconnected",
            "Device.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.LastConnectionError": "ERROR_AUTHENTICATION_FAILURE",
            "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ExternalIPAddress": "",
            "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ConnectionStatus": "Disconnected",
        },
    },

    # ── S12: Rim Belhadj — high latency / packet loss ─────────────
    # Connected and good SNR (19 dB). Speed is fine.
    # But latency 185ms and packet loss 4.2% → gaming/VoIP unusable.
    # Network congestion or routing issue, not line quality.
    22: {
        "type": "healthy",
        "interval": 30,
        "serial_override": "SIM-000016",
        "description": "Rim - connected, good SNR, high latency 185ms + 4% packet loss",
        "params": {
            "Device.DeviceInfo.SoftwareVersion": "4.2.0",
            "Device.DeviceInfo.HardwareVersion": "v2",
            "Device.DeviceInfo.UpTime": "43200",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamSNRMargin": "19",
            "Device.WANDevice.1.WANDSLInterfaceConfig.UpstreamSNRMargin": "18",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamAttenuation": "11",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamCurrRate": "48000",
            "Device.WANDevice.1.WANDSLInterfaceConfig.UpstreamCurrRate": "9500",
            "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ExternalIPAddress": "100.64.16.1",
            "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ConnectionStatus": "Connected",
            "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ConnectionType": "IP_Routed",
        },
    },

    # ── S13: Bilel Amor — plan upgrade request ────────────────────
    # Basic plan with 1h Session-Timeout quota. Hits it every day.
    # Customer is frustrated and wants upgrade to premium.
    # Agent should detect Session-Timeout + offer plan upgrade.
    23: {
        "type": "healthy",
        "interval": 30,
        "serial_override": "SIM-000017",
        "description": "Bilel - basic plan, hits 1h quota daily, wants upgrade",
        "params": {
            "Device.DeviceInfo.SoftwareVersion": "4.1.2",
            "Device.DeviceInfo.HardwareVersion": "v2",
            "Device.DeviceInfo.UpTime": "0",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamSNRMargin": "21",
            "Device.WANDevice.1.WANDSLInterfaceConfig.UpstreamSNRMargin": "20",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamAttenuation": "9",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamCurrRate": "18000",
            "Device.WANDevice.1.WANDSLInterfaceConfig.UpstreamCurrRate": "4500",
            "Device.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.ConnectionStatus": "Disconnected",
            "Device.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.LastConnectionError": "ERROR_ISP_DISCONNECT",
            "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ExternalIPAddress": "",
            "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ConnectionStatus": "Disconnected",
        },
    },

    # ── S14: Dorra Miled — WiFi confusion ─────────────────────────
    # Perfect line: SNR 22 dB, connected, fast speed.
    # Customer thinks internet is broken but the problem is WiFi
    # coverage in their home. Line and connection are excellent.
    24: {
        "type": "healthy",
        "interval": 30,
        "serial_override": "SIM-000018",
        "description": "Dorra - perfect line, WiFi coverage issue (not internet)",
        "params": {
            "Device.DeviceInfo.SoftwareVersion": "4.2.0",
            "Device.DeviceInfo.HardwareVersion": "v2",
            "Device.DeviceInfo.UpTime": "259200",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamSNRMargin": "22",
            "Device.WANDevice.1.WANDSLInterfaceConfig.UpstreamSNRMargin": "21",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamAttenuation": "8",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamCurrRate": "50000",
            "Device.WANDevice.1.WANDSLInterfaceConfig.UpstreamCurrRate": "10000",
            "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ExternalIPAddress": "100.64.18.1",
            "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ConnectionStatus": "Connected",
            "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ConnectionType": "IP_Routed",
        },
    },

    # ── S15: Sofien Nasr — factory reset scenario ─────────────────
    # Everything failed: reboot didn't help, PPPoE push failed,
    # MTU set but still not connecting. Device is misconfigured.
    # Last resort: factory reset required after customer confirmation.
    25: {
        "type": "healthy",
        "interval": 30,
        "serial_override": "SIM-000019",
        "description": "Sofien - all fixes failed, factory reset required",
        "params": {
            "Device.DeviceInfo.SoftwareVersion": "3.0.1",
            "Device.DeviceInfo.HardwareVersion": "v2",
            "Device.DeviceInfo.UpTime": "120",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamSNRMargin": "15",
            "Device.WANDevice.1.WANDSLInterfaceConfig.UpstreamSNRMargin": "14",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamAttenuation": "18",
            "Device.WANDevice.1.WANDSLInterfaceConfig.DownstreamCurrRate": "35000",
            "Device.WANDevice.1.WANDSLInterfaceConfig.UpstreamCurrRate": "7000",
            "Device.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.ConnectionStatus": "Disconnected",
            "Device.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.LastConnectionError": "ERROR_AUTHENTICATION_FAILURE",
            "Device.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.Username": "sofien.nasr_old",
            "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ExternalIPAddress": "",
            "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ConnectionStatus": "Disconnected",
        },
    },
}


def build_param_list(params: dict) -> str:
    """Build the ParameterList XML block from a params dict."""
    entries = []
    for name, value in params.items():
        xsi_type = "xsd:unsignedInt" if str(value).isdigit() else "xsd:string"
        entries.append(f"""        <ParameterValueStruct>
          <Name>{name}</Name>
          <Value xsi:type="{xsi_type}">{value}</Value>
        </ParameterValueStruct>""")
    return "\n".join(entries)


def make_inform(serial: str, params: dict, oui: str = "AABBCC") -> str:
    """Build a full TR-069 Inform SOAP envelope with the given parameters."""
    param_xml = build_param_list(params)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope
  xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
  xmlns:cwmp="urn:dslforum-org:cwmp-1-0"
  xmlns:xsd="http://www.w3.org/2001/XMLSchema"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <soapenv:Header>
    <cwmp:ID soapenv:mustUnderstand="1">1</cwmp:ID>
  </soapenv:Header>
  <soapenv:Body>
    <cwmp:Inform>
      <DeviceId>
        <Manufacturer>SimVendor</Manufacturer>
        <OUI>{oui}</OUI>
        <ProductClass>SimRouter</ProductClass>
        <SerialNumber>{serial}</SerialNumber>
      </DeviceId>
      <Event>
        <EventStruct>
          <EventCode>0 BOOTSTRAP</EventCode>
          <CommandKey></CommandKey>
        </EventStruct>
      </Event>
      <MaxEnvelopes>1</MaxEnvelopes>
      <CurrentTime>2024-01-01T00:00:00Z</CurrentTime>
      <RetryCount>0</RetryCount>
      <ParameterList>
{param_xml}
      </ParameterList>
    </cwmp:Inform>
  </soapenv:Body>
</soapenv:Envelope>"""


DEFAULT_PARAMS = {
    "Device.DeviceInfo.SoftwareVersion": "1.0.0",
    "Device.DeviceInfo.HardwareVersion": "v1",
    "Device.LAN.IPAddress": "192.168.1.1",
}


def simulate_device(device_num: int):
    profile = DEVICE_PROFILES.get(device_num, {"type": "healthy", "interval": 30})
    serial  = profile.get("serial_override", f"SIM-{device_num:06d}")
    params  = profile.get("params", DEFAULT_PARAMS.copy())

    if "Device.LAN.IPAddress" in DEFAULT_PARAMS and "Device.LAN.IPAddress" not in params:
        params = {**DEFAULT_PARAMS, "Device.LAN.IPAddress": f"192.168.1.{random.randint(2, 254)}"}

    headers = {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": ""}
    print(f"[{serial}] Profile: {profile['description']}")

    # ── Noop devices ─────────────────────────────────────────────
    if profile["type"] == "noop":
        print(f"[{serial}] Noop — skipping, serial owned by scenario device")
        while True:
            time.sleep(3600)
        return

    # ── Offline devices ───────────────────────────────────────────
    if profile["type"] == "offline":
        try:
            resp = requests.post(ACS_URL, data=make_inform(serial, params),
                                 headers=headers, timeout=10)
            print(f"[{serial}] Initial inform sent: {resp.status_code} — now offline")
        except Exception as e:
            print(f"[{serial}] Error on initial inform: {e}")
        while True:
            time.sleep(3600)
        return

    # ── Healthy devices ───────────────────────────────────────────
    interval   = profile.get("interval", 30)
    uptime_key = "Device.DeviceInfo.UpTime"
    has_uptime = uptime_key in params

    while True:
        try:
            if has_uptime:
                try:
                    params[uptime_key] = str(int(params[uptime_key]) + interval)
                except ValueError:
                    pass

            resp = requests.post(ACS_URL, data=make_inform(serial, params),
                                 headers=headers, timeout=10)
            print(f"[{serial}] Inform sent: {resp.status_code} (next in {interval}s)")
        except Exception as e:
            print(f"[{serial}] Error: {e}")
        time.sleep(interval)


# Wait for ACS to be ready
time.sleep(5)

threads = [
    threading.Thread(target=simulate_device, args=(i,), daemon=True)
    for i in range(1, NUM_DEVICES + 1)
]
for t in threads:
    t.start()
for t in threads:
    t.join()