import paho.mqtt.client as mqtt
import threading
import sys
import getpass
import time
import os
import json

TIMEOUT = 10
DISCOVERY_TIME = 8
LIVE_INTERVAL = 2.0
METER_CONNECTION_TIME = 30
ENV_FILE = ".env"
DEVICE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".mqtt_devices.json")
DISCOVERY_TOPICS = (
    "hame_energy/+/+/+/ctrl",
    "marstek_energy/+/+/+/ctrl",
    "hm2mqtt/HMJ-2/availability/+",
    "hm2mqtt/HMJ-2/device/+/data",
)
broker_key = None

# ============================================================
# SMART-METER-TYPEN
# ============================================================

# Auswahl -> (Name, meter-Code, echte MAC erforderlich?)
SMART_METERS = {
    1: ("MARSTEK CT001 / CT001.5", 0, False),
    2: ("Shelly Pro 3EM", 1, False),
    3: ("P1 Meter", 2, True),
    4: ("MARSTEK CT002", 3, True),
    5: ("MARSTEK CT003", 4, True),
    6: ("Shelly EM Gen3", 5, True),
    7: ("Shelly Pro EM50", 6, True),
    8: ("EcoTracker", 7, True),
}

# Vom HMJ-2 gemeldeter ct_t-Code -> Name
REPORTED_SMART_METER_TYPES = {
    1: "MARSTEK CT001 / CT001.5",
    3: "MARSTEK CT002",
    4: "Shelly Pro 3EM",
    5: "P1 Meter",
    6: "MARSTEK CT003",
    7: "Shelly EM Gen3",
    8: "Shelly Pro EM50",
    9: "EcoTracker",
}

# Erwarteter ct_t-Code nach dem Setzen per meter=
EXPECTED_CT_T = {
    0: 1,
    1: 4,
    2: 5,
    3: 3,
    4: 6,
    5: 7,
    6: 8,
    7: 9,
}

PHASES = {
    0: "Phase 1 / L1",
    1: "Phase 2 / L2",
    2: "Phase 3 / L3",
    3: "Nicht eindeutig zugeordnet (Code 3)",
    255: "Keine / automatisch",
}

# ============================================================
# GLOBALE VARIABLEN
# ============================================================

response_received = threading.Event()
response_lock = threading.Lock()
response_data = None

mqtt_client = None
command_topic = None
response_topic = None

device_id = None
device_type = None
device_namespace = None

discovered_devices = {}
discovery_lock = threading.Lock()
connection_ready = threading.Event()
connection_error = None

# ============================================================
# .ENV LADEN
# ============================================================

def load_env_file(path=ENV_FILE):
    env = {}

    if not os.path.isfile(path):
        return env

    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()

                if not line or line.startswith("#") or "=" not in line:
                    continue

                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()

                if (
                    len(value) >= 2
                    and (
                        (value.startswith('"') and value.endswith('"'))
                        or (value.startswith("'") and value.endswith("'"))
                    )
                ):
                    value = value[1:-1]

                env[key] = value

        print(f"✓ Konfiguration aus {path} geladen.")

    except Exception as exc:
        print(f"⚠ .env konnte nicht gelesen werden: {exc}")

    return env

# ============================================================
# HILFSFUNKTIONEN
# ============================================================

def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def normalize_mac(mac: str) -> str:
    return (
        mac.replace(":", "")
        .replace("-", "")
        .replace(" ", "")
        .upper()
    )


def valid_mac(mac: str) -> bool:
    if len(mac) != 12:
        return False

    try:
        int(mac, 16)
        return True
    except ValueError:
        return False


def format_mac(mac: str) -> str:
    if len(mac) != 12:
        return mac

    return ":".join(mac[i:i + 2] for i in range(0, 12, 2))


def parse_payload(payload: str) -> dict:
    result = {}

    for item in payload.split(","):
        item = item.strip()

        if "=" not in item:
            continue

        key, value = item.split("=", 1)
        result[key.strip()] = value.strip()

    return result


def as_int(data, key):
    try:
        return int(data[key])
    except (KeyError, ValueError, TypeError):
        return None


def value_or_dash(value, unit=""):
    if value is None:
        return "-"
    return f"{value} {unit}" if unit else str(value)


def format_ct_phase(data):
    phase = as_int(data, "c0")
    if phase is None:
        return "nicht gemeldet"
    return f"{PHASES.get(phase, 'Unbekannt')} [c0={phase}]"


def format_adaptive_mode(data):
    return {0: "Aus", 1: "Ein"}.get(as_int(data, "md"), "nicht gemeldet")


def interpret_multi_device_mode(data):
    """
    Liest die von cd=01 gemeldeten Felder sm/bn aus.

    Wichtig:
    Die exakte Rücklesesemantik von sm und bn ist für den HMJ-2
    nicht vollständig dokumentiert. Deshalb wird nicht blind behauptet,
    dass sm=0/1 direkt dem single_mode-Parameter von cd=29 entspricht.

    Eine bn-Nummer im dokumentierten Slotbereich 5000..5499 ist jedoch
    ein starker Hinweis auf eine Mehrgeräte-Konfiguration.
    """
    sm = as_int(data, "sm")
    bn = as_int(data, "bn")

    if sm is None and bn is None:
        mode = "nicht bestimmbar"
        detail = "Firmware meldet weder sm noch bn."
    elif bn is not None and 5000 <= bn <= 5499:
        mode = "Mehrgerätebetrieb wahrscheinlich"
        detail = (
            f"bn={bn} liegt im dokumentierten B2500-Slotbereich "
            "5000–5499."
        )
    elif bn == 0:
        mode = "Einzelgerät wahrscheinlich"
        detail = (
            "bn=0: kein Mehrgeräte-Slot gemeldet. "
            "Die Zuordnung ist eine konservative Interpretation."
        )
    else:
        mode = "nicht eindeutig"
        detail = (
            "Die gemeldeten Werte lassen sich mit dem aktuell "
            "dokumentierten Protokoll nicht eindeutig zuordnen."
        )

    return {
        "mode": mode,
        "sm": sm,
        "bn": bn,
        "detail": detail,
    }


def print_multi_device_configuration(data):
    if not data:
        print("Keine Daten vorhanden.")
        return

    info = interpret_multi_device_mode(data)

    print()
    print("=" * 68)
    print("EINZEL-/MEHRGERÄTE-KONFIGURATION")
    print("=" * 68)
    print(f"Gerätemodus:        {info['mode']}")
    print(f"sm (Rohwert):       {value_or_dash(info['sm'])}")
    print(f"bn (Rohwert):       {value_or_dash(info['bn'])}")

    if info["bn"] is not None and 5000 <= info["bn"] <= 5499:
        print(f"Mehrgeräte-Slot:    {info['bn']}")

    print()
    print(info["detail"])
    print()
    print(
        "Hinweis: cd=29 setzt single_mode=0/1 und b2500_num=5000..5499. "
        "Die genaue Rückabbildung der cd=01-Felder sm/bn ist für HMJ-2 "
        "noch nicht vollständig dokumentiert."
    )
    print("=" * 68)


def show_multi_device_configuration():
    if not device_type or not device_type.startswith(("HMA", "HMF", "HMK", "HMJ")):
        print("Diese Abfrage ist nur für B2500 V2 verfügbar.")
        return

    data = request_status()

    if not data:
        print("Keine Statusantwort vom Speicher erhalten.")
        return

    print_multi_device_configuration(data)

# ============================================================
# MQTT-VERBINDUNGSDATEN
# ============================================================

def ask_connection_data():
    env = load_env_file()

    print()
    print("=" * 68)
    print("MARSTEK B2500-D / HMJ-2")
    print("MQTT Konfigurations- und Live-Daten-Tool")
    print("=" * 68)
    print()

    mqtt_host = env.get("MQTT_HOST", "").strip()
    if mqtt_host:
        print(f"MQTT Host:      {mqtt_host} [aus .env]")
    else:
        mqtt_host = input("MQTT Broker IP / Hostname: ").strip()

    port_value = env.get("MQTT_PORT", "").strip()
    if port_value:
        try:
            mqtt_port = int(port_value)
        except ValueError:
            print("⚠ MQTT_PORT in .env ist ungültig, verwende 1883.")
            mqtt_port = 1883
        print(f"MQTT Port:      {mqtt_port} [aus .env]")
    else:
        port_input = input("MQTT Port [1883]: ").strip()
        try:
            mqtt_port = int(port_input) if port_input else 1883
        except ValueError:
            print("Ungültiger MQTT-Port.")
            sys.exit(1)

    username = env.get("MQTT_USERNAME", "").strip()
    if username:
        print(f"MQTT Benutzer:  {username} [aus .env]")
    else:
        username = input("MQTT Benutzername [optional]: ").strip()

    password = env.get("MQTT_PASSWORD", "")
    if password:
        print("MQTT Passwort:   ******** [aus .env]")
    elif username:
        password = getpass.getpass("MQTT Passwort: ")
    else:
        password = None

    return mqtt_host, mqtt_port, username, password

# ============================================================
# DISCOVERY
# ============================================================

def record_discovery(topic, live_response=False):
    parts = topic.split("/")
    if (
        len(parts) == 5
        and parts[0] in ("hame_energy", "marstek_energy")
        and parts[2] in ("App", "device")
        and parts[4] == "ctrl"
        and parts[1] and parts[3]
    ):
        namespace, dtype, source, did, _ = parts
    elif (
        len(parts) == 4
        and parts[0] == "hm2mqtt"
        and parts[1] == "HMJ-2"
        and parts[2] == "availability"
        and valid_mac(parts[3])
    ):
        # Die Bridge veröffentlicht die Gerätekennung retained, auch wenn
        # gerade keine nativen ctrl-Nachrichten gesendet werden.
        namespace, dtype, source, did = "hame_energy", parts[1], "hm2mqtt", parts[3]
    elif (
        len(parts) == 5 and parts[:3] == ["hm2mqtt", "HMJ-2", "device"]
        and valid_mac(parts[3]) and parts[4] == "data"
    ):
        namespace, dtype, source, did = "hame_energy", parts[1], "hm2mqtt", parts[3]
    else:
        return

    # MQTT-Topics sind case-sensitive: die ID niemals normalisieren.
    key = (namespace, dtype, did)
    with discovery_lock:
        device = discovered_devices.setdefault(key, {
            "namespace": namespace,
            "type": dtype,
            "id": did,
            "sources": set(),
        })
        device["sources"].add(source)
        if source == "device" and live_response:
            device["last_response"] = time.monotonic()
            device["confirmed"] = True


def load_known_devices():
    if not broker_key:
        return
    try:
        with open(DEVICE_FILE, encoding="utf-8") as f:
            entries = json.load(f).get(broker_key, [])
        for topic in entries:
            if isinstance(topic, str):
                record_discovery(topic)
    except FileNotFoundError:
        pass
    except (OSError, ValueError, AttributeError, TypeError) as exc:
        print(f"Geräteliste konnte nicht geladen werden: {exc}")


def save_known_devices():
    if not broker_key:
        return
    try:
        try:
            with open(DEVICE_FILE, encoding="utf-8") as f:
                saved = json.load(f)
        except FileNotFoundError:
            saved = {}
        saved[broker_key] = [
            f"{d['namespace']}/{d['type']}/device/{d['id']}/ctrl"
            for d in discovery_snapshot().values()
        ]
        with open(DEVICE_FILE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(saved, f, indent=2)
            f.write("\n")
        os.replace(DEVICE_FILE + ".tmp", DEVICE_FILE)
    except (OSError, ValueError, TypeError) as exc:
        print(f"Geräteliste konnte nicht gespeichert werden: {exc}")


def discovery_snapshot(include_candidates=False):
    with discovery_lock:
        return {
            key: {**device, "sources": set(device["sources"])}
            for key, device in discovered_devices.items()
            if include_candidates or device["namespace"] != "marstek_energy"
            or device.get("confirmed", False)
        }


def discover_devices():
    load_known_devices()
    discovery_topics = DISCOVERY_TOPICS

    print()
    print("=" * 68)
    print("SUCHE NACH MARSTEK-GERÄTEN")
    print("=" * 68)
    print()

    started = time.monotonic()
    probes = {}
    try:
        for topic in discovery_topics:
            print(f"Abonniere: {topic}")
            result, _ = mqtt_client.subscribe(topic)
            if result != mqtt.MQTT_ERR_SUCCESS:
                raise RuntimeError(f"MQTT-Abonnement fehlgeschlagen: {topic} ({result})")

        print(f"\nSuche für {DISCOVERY_TIME} Sekunden ...\n")
        deadline = time.monotonic() + DISCOVERY_TIME
        while time.monotonic() < deadline:
            for key, device in discovery_snapshot(include_candidates=True).items():
                count, last_probe = probes.get(key, (0, float("-inf")))
                if count < 2 and time.monotonic() - last_probe >= 3 and (
                    device.get("last_response", -1) < started
                ):
                    mqtt_client.publish(
                        f"{device['namespace']}/{device['type']}/App/{device['id']}/ctrl",
                        "cd=01", retain=False,
                    )
                    probes[key] = (count + 1, time.monotonic())
            remaining = max(0, int(deadline - time.monotonic()))
            print(
                f"\rBekannte Geräte: {len(discovery_snapshot())}   "
                f"Restzeit: {remaining}s ", end="", flush=True,
            )
            time.sleep(0.2)
        print("\n")
    finally:
        # Weiter zuhören: auch zwischen Suchläufen eintreffende Geräte merken.
        save_known_devices()

    devices = discovery_snapshot()
    for device in devices.values():
        device["responded"] = device.get("last_response", -1) >= started
    return devices


def select_discovered_device():
    global device_id
    global device_type
    global device_namespace

    devices = discover_devices()

    if not devices:
        print("Es wurde kein MARSTEK-Gerät automatisch erkannt.")
        print()
        print(
            "Hinweis: MQTT kann nur Topics liefern, die während der Suche "
            "gesendet werden oder retained gespeichert sind."
        )
        print()

        manual = input(
            "HMJ-2 MAC-Adresse manuell eingeben? [J/n]: "
        ).strip().lower()

        if manual in ("n", "nein", "no"):
            return False

        mac = normalize_mac(
            input("MAC-Adresse des HMJ-2: ").strip()
        )

        if not valid_mac(mac):
            print("Ungültige MAC-Adresse.")
            return False

        device_id = mac.lower()
        device_type = "HMJ-2"
        device_namespace = "hame_energy"
        record_discovery(f"hame_energy/HMJ-2/App/{device_id}/ctrl")
        save_known_devices()
        return True

    # hame_energy + 12-stellige Geräte-ID bevorzugen
    device_list = sorted(
        devices.values(),
        key=lambda d: (
            0 if d["namespace"] == "hame_energy" and len(d["id"]) == 12 else 1,
            0 if d["namespace"] == "hame_energy" else 1,
            d["type"],
            d["id"],
        )
    )

    print("=" * 68)
    print("GEFUNDENE MARSTEK-GERÄTE")
    print("=" * 68)
    print()

    for index, device in enumerate(device_list, start=1):
        namespace = device["namespace"]
        dtype = device["type"]
        did = device["id"]
        sources = ", ".join(sorted(device["sources"]))

        if namespace == "hame_energy" and len(did) == 12:
            display_id = format_mac(did.upper())
        else:
            display_id = did

        print(
            f"{index}. {dtype:<8} {display_id:<34} "
            f"[{namespace}; {sources}; "
            f"{'antwortet' if device.get('responded') else 'bekannt, aktuell unbestätigt'}]"
            + (" ← ausgewählt" if (namespace, dtype, did) ==
               (device_namespace, device_type, device_id) else "")
        )

    print()

    while True:
        value = input("Gerät auswählen [Nummer / m = MAC hinzufügen / 0 = Abbrechen]: ").strip()
        if value == "0":
            return False
        if value.lower() == "m":
            mac = normalize_mac(input("MAC-Adresse des HMJ-2: ").strip())
            if not valid_mac(mac):
                print("Ungültige MAC-Adresse.")
                continue
            device_namespace, device_type, device_id = "hame_energy", "HMJ-2", mac.lower()
            record_discovery(f"hame_energy/HMJ-2/App/{device_id}/ctrl")
            save_known_devices()
            return True

        try:
            index = int(value)
        except ValueError:
            print("Bitte eine Zahl eingeben.")
            continue

        if not (1 <= index <= len(device_list)):
            print("Ungültige Auswahl.")
            continue

        selected = device_list[index - 1]

        device_type = selected["type"]
        device_id = selected["id"]
        device_namespace = selected["namespace"]

        if device_namespace == "marstek_energy":
            print()
            print(
                "⚠ Dieses Gerät verwendet marstek_energy mit einer "
                "langen/verschlüsselten Geräte-ID."
            )
            print(
                "Für normales lokales MQTT ist hame_energy mit "
                "12-stelliger Geräte-ID vorzuziehen."
            )

        return True

# ============================================================
# TOPICS
# ============================================================

def configure_device_topics():
    global command_topic
    global response_topic
    global response_data

    if response_topic:
        mqtt_client.unsubscribe(response_topic)

    command_topic = (
        f"{device_namespace}/{device_type}/App/{device_id}/ctrl"
    )

    with response_lock:
        response_topic = (
            f"{device_namespace}/{device_type}/device/{device_id}/ctrl"
        )
        response_data = None
        response_received.clear()

    mqtt_client.subscribe(response_topic)

    print()
    print("=" * 68)
    print("AUSGEWÄHLTES GERÄT")
    print("=" * 68)
    print(f"Namespace: {device_namespace}")
    print(f"Typ:       {device_type}")

    if len(device_id) == 12:
        print(f"ID/MAC:    {format_mac(device_id)}")
    else:
        print(f"ID:        {device_id}")

    print(f"TX Topic:  {command_topic}")
    print(f"RX Topic:  {response_topic}")
    print("=" * 68)

# ============================================================
# STATUSABFRAGE
# ============================================================

def request_status():
    global response_data

    with response_lock:
        response_received.clear()
        response_data = None

    result = mqtt_client.publish(command_topic, "cd=01")
    result.wait_for_publish()

    if not response_received.wait(TIMEOUT):
        return None

    return response_data


def connect_smart_meter(expected_type=None):
    """Aktiviert die Zählerregelung und prüft die echte Verbindung."""
    if not device_type or not device_type.startswith(("HMA", "HMF", "HMK", "HMJ")):
        print("Automatische Zählerregelung wird nur für B2500 V2 unterstützt.")
        return False

    data = request_status()
    if not data:
        print("Keine Statusantwort; Automodus wurde nicht geändert.")
        return False
    actual_type = as_int(data, "ct_t")
    if actual_type not in REPORTED_SMART_METER_TYPES or (
        expected_type is not None and actual_type != expected_type
    ):
        print("Smart-Meter-Typ nicht bestätigt; bitte zuerst den Zähler konfigurieren.")
        return False

    mode = as_int(data, "md")
    if mode not in (0, 1):
        print("Automodus nicht gemeldet; keine Änderung vorgenommen.")
        return False
    if mode == 0:
        print("Aktiviere Automodus für die Regelung mit dem Smart Meter ...")
        mqtt_client.publish(command_topic, "cd=04,md=1").wait_for_publish()
        # Erfolg erst anhand einer neuen Statusantwort feststellen.
        data = None

    deadline = time.monotonic() + METER_CONNECTION_TIME
    while True:
        if data and (
            as_int(data, "ct_t") == actual_type
            and as_int(data, "md") == 1
            and as_int(data, "sg") == 1
        ):
            print_configuration(data)
            print("✓ Smart Meter verbunden und Automodus aktiv.")
            return True
        if time.monotonic() >= deadline:
            break
        time.sleep(2)
        data = request_status()

    if data:
        print_configuration(data)
    print("⚠ Verbindung und Automodus konnten noch nicht bestätigt werden.")
    if actual_type == 4:
        print("Shelly: RPC über UDP, passenden Listening-Port und Neustart prüfen.")
        print("Für den hier getesteten HMJ-2 funktioniert Port 2220; Zieladresse bleibt leer.")
        print("WLAN-Erreichbarkeit zwischen Speicher und Shelly prüfen; VPN überträgt ggf. keine Broadcasts.")
    return False

# ============================================================
# SMART-METER-KONFIGURATION ANZEIGEN
# ============================================================

def print_configuration(data):
    if not data:
        print("Keine Daten vorhanden.")
        return

    ct_type = as_int(data, "ct_t")
    phase = as_int(data, "c0")
    connected = as_int(data, "sg")

    m0 = as_int(data, "m0")
    m1 = as_int(data, "m1")
    m2 = as_int(data, "m2")

    measured_power = as_int(data, "st")
    target_power = as_int(data, "sp")

    print()
    print("=" * 68)
    print("AKTUELLE SMART-METER-KONFIGURATION")
    print("=" * 68)

    if ct_type is not None:
        meter_name = REPORTED_SMART_METER_TYPES.get(
            ct_type,
            f"Unbekannter Typ ({ct_type})"
        )
        print(f"Smart Meter:        {meter_name}")
        print(f"ct_t:               {ct_type}")
    else:
        print("Smart Meter:        nicht gemeldet")

    if connected is not None:
        print(
            f"CT verbunden:       "
            f"{'Ja' if connected == 1 else 'Nein'}"
        )

    if phase is not None:
        print(
            f"Phase:              "
            f"{format_ct_phase(data)}"
        )
    print(f"Automodus:          {format_adaptive_mode(data)}")
    print(f"CT-Statuscode c1:    {value_or_dash(as_int(data, 'c1'))}")

    print()
    print("Aktuelle Messwerte")
    print("-" * 68)
    print(f"L1 / m0:            {value_or_dash(m0, 'W')}")
    print(f"L2 / m1:            {value_or_dash(m1, 'W')}")
    print(f"L3 / m2:            {value_or_dash(m2, 'W')}")
    print(
        f"Messleistung:       "
        f"{value_or_dash(measured_power, 'W')}"
    )
    print(
        f"Sollwert:           "
        f"{value_or_dash(target_power, 'W')}"
    )
    print("=" * 68)

# ============================================================
# SMART METER SETZEN
# ============================================================

def select_smart_meter():
    print()
    print("=" * 68)
    print("SMART METER AUSWÄHLEN")
    print("=" * 68)

    for number, data in SMART_METERS.items():
        name, meter_code, needs_mac = data
        mac_text = "MAC erforderlich" if needs_mac else "keine MAC erforderlich"

        print(
            f"{number}. {name:<24} "
            f"meter={meter_code}   {mac_text}"
        )

    print()
    print("0. Abbrechen")

    while True:
        value = input("Auswahl: ").strip()

        try:
            selection = int(value)
        except ValueError:
            print("Bitte eine Zahl eingeben.")
            continue

        if selection == 0:
            return None

        if selection in SMART_METERS:
            return selection

        print("Ungültige Auswahl.")


def ask_meter_mac(selection):
    name, meter_code, needs_mac = SMART_METERS[selection]

    if not needs_mac:
        return "000000000000"

    while True:
        mac = normalize_mac(
            input(f"MAC-Adresse von {name}: ").strip()
        )

        if valid_mac(mac):
            return mac

        print("Ungültige MAC. Beispiel: AA:BB:CC:DD:EE:FF")


def set_smart_meter():
    selection = select_smart_meter()

    if selection is None:
        print("Abgebrochen.")
        return

    name, meter_code, needs_mac = SMART_METERS[selection]
    meter_mac = ask_meter_mac(selection)

    payload = (
        f"cd=27,"
        f"meter={meter_code},"
        f"mac={meter_mac}"
    )

    print()
    print("=" * 68)
    print("NEUE SMART-METER-KONFIGURATION")
    print("=" * 68)
    print(f"Smart Meter: {name}")
    print(f"meter-Code:  {meter_code}")
    print(f"MAC:         {format_mac(meter_mac)}")
    print(f"MQTT:        {payload}")
    print("Anschließend wird der Automodus aktiviert und die Verbindung geprüft.")
    print()

    confirm = input(
        "Konfiguration wirklich senden? [j/N]: "
    ).strip().lower()

    if confirm not in ("j", "ja", "y", "yes"):
        print("Abgebrochen.")
        return

    mqtt_client.publish(
        command_topic,
        payload
    ).wait_for_publish()

    print("Konfiguration wurde gesendet.")
    time.sleep(2)

    data = request_status()

    if not data:
        print("Keine Statusantwort erhalten.")
        return

    print_configuration(data)

    reported_type = as_int(data, "ct_t")
    expected_type = EXPECTED_CT_T.get(meter_code)

    print()

    if reported_type == expected_type:
        print("✓ Smart-Meter-Typ wurde erfolgreich übernommen.")
        connect_smart_meter(expected_type)
    else:
        print(
            "⚠ Der zurückgelesene Typ entspricht noch nicht "
            "dem erwarteten Wert."
        )
        print(f"Erwartetes ct_t: {expected_type}")
        print(f"Gemeldetes ct_t: {reported_type}")

# ============================================================
# CT-PHASE SETZEN
# ============================================================

def set_ct_phase():
    print()
    print("=" * 68)
    print("CT-PHASE SETZEN")
    print("=" * 68)
    print("1. L1")
    print("2. L2")
    print("3. L3")
    print("4. Automatisch / keine feste Phase")
    print("0. Abbrechen")

    mapping = {
        "1": 0,
        "2": 1,
        "3": 2,
        "4": 255,
    }

    selection = input("Auswahl: ").strip()

    if selection == "0":
        return

    if selection not in mapping:
        print("Ungültige Auswahl.")
        return

    phase = mapping[selection]
    payload = f"cd=22,md={phase}"

    print()
    print(f"Sende: {payload}")

    mqtt_client.publish(
        command_topic,
        payload
    ).wait_for_publish()

    print("CT-Phase wurde gesetzt.")
    time.sleep(1)

    data = request_status()

    if data:
        print_configuration(data)

# ============================================================
# PHASENDIAGNOSE
# ============================================================

def start_phase_diagnosis():
    print()
    print(
        "Die Phasendiagnose lässt den HMJ-2 "
        "die CT-Phase neu bestimmen."
    )

    confirm = input(
        "Diagnose starten? [j/N]: "
    ).strip().lower()

    if confirm not in ("j", "ja", "y", "yes"):
        return

    mqtt_client.publish(
        command_topic,
        "cd=27,seq_check"
    ).wait_for_publish()

    print("Phasendiagnose gestartet.")

# ============================================================
# LIVE-DATEN
# ============================================================

def print_live_data(data):
    soc = as_int(data, "pe")

    grid_power = as_int(data, "st")
    target_power = as_int(data, "sp")

    l1 = as_int(data, "m0")
    l2 = as_int(data, "m1")
    l3 = as_int(data, "m2")
    inverter = as_int(data, "m3")

    pv1 = as_int(data, "w1")
    pv2 = as_int(data, "w2")

    battery_power = as_int(data, "bb")
    battery_voltage = as_int(data, "bv")
    battery_current = as_int(data, "bc")

    ct_type = as_int(data, "ct_t")
    phase = as_int(data, "c0")
    connected = as_int(data, "sg")

    meter_name = (
        REPORTED_SMART_METER_TYPES.get(
            ct_type,
            f"Unbekannt ({ct_type})"
        )
        if ct_type is not None
        else "-"
    )

    print("=" * 68)
    print("MARSTEK B2500-D / HMJ-2 LIVE-DATEN")
    print("=" * 68)

    print(
        f"Gerät:                   "
        f"{device_type} "
        f"{format_mac(device_id) if len(device_id) == 12 else device_id}"
    )

    print()
    print("BATTERIE")
    print("-" * 68)
    print(f"Ladezustand (SOC):       {value_or_dash(soc, '%')}")

    if battery_power is not None:
        print(f"Batterieleistung:        {battery_power} W")

    if battery_voltage is not None:
        print(f"Batteriespannung:        {battery_voltage}")

    if battery_current is not None:
        print(f"Batteriestrom:           {battery_current}")

    print()
    print("SMART METER / NETZ")
    print("-" * 68)
    print(f"Smart Meter:             {meter_name}")
    print(
        f"CT verbunden:            "
        f"{'Ja' if connected == 1 else 'Nein' if connected is not None else '-'}"
    )
    print(
        f"CT-Phase:                "
        f"{format_ct_phase(data)}"
    )
    print(f"Automodus:               {format_adaptive_mode(data)}")
    print(f"CT-Statuscode c1:         {value_or_dash(as_int(data, 'c1'))}")
    print(
        f"Netz-/Messleistung:      "
        f"{value_or_dash(grid_power, 'W')}"
    )
    print(
        f"Regel-Sollwert:          "
        f"{value_or_dash(target_power, 'W')}"
    )

    print()
    print("PHASEN")
    print("-" * 68)
    print(f"L1 / m0:                 {value_or_dash(l1, 'W')}")
    print(f"L2 / m1:                 {value_or_dash(l2, 'W')}")
    print(f"L3 / m2:                 {value_or_dash(l3, 'W')}")

    if inverter is not None:
        print(f"Mikrowechselrichter m3:  {inverter} W")

    if pv1 is not None or pv2 is not None:
        print()
        print("PV")
        print("-" * 68)
        print(f"PV Eingang 1:            {value_or_dash(pv1, 'W')}")
        print(f"PV Eingang 2:            {value_or_dash(pv2, 'W')}")

        if pv1 is not None and pv2 is not None:
            print(f"PV Gesamt:               {pv1 + pv2} W")

    print()
    print("-" * 68)
    print(f"Aktualisierung alle {LIVE_INTERVAL:.1f} Sekunden")
    print("STRG+C = zurück zum Hauptmenü")
    print(f"Letzte Aktualisierung: {time.strftime('%H:%M:%S')}")
    print("=" * 68)


def live_data():
    print()
    print("Live-Modus wird gestartet ...")
    time.sleep(0.5)

    try:
        while True:
            data = request_status()
            clear_screen()

            if data:
                print_live_data(data)
            else:
                print("=" * 68)
                print("HMJ-2 LIVE-DATEN")
                print("=" * 68)
                print()
                print("Keine Antwort vom Speicher erhalten.")
                print()
                print("STRG+C = zurück zum Hauptmenü")

            time.sleep(LIVE_INTERVAL)

    except KeyboardInterrupt:
        print()
        print("Live-Modus beendet.")
        time.sleep(0.5)

# ============================================================
# ROHDATEN
# ============================================================

def show_raw_data():
    data = request_status()

    if not data:
        print("Keine Daten erhalten.")
        return

    print()
    print("=" * 68)
    print("ROHDATEN cd=01")
    print("=" * 68)

    for key in sorted(data.keys()):
        print(f"{key:10} = {data[key]}")

# ============================================================
# HAUPTMENÜ
# ============================================================

def output_time(value, allow_end=False):
    try:
        hour, minute = map(int, str(value).split(":"))
    except (ValueError, TypeError):
        raise ValueError("Zeit als HH:MM eingeben.")
    if not (0 <= hour <= 23 and 0 <= minute <= 59) and not (
        allow_end and hour == 24 and minute == 0
    ):
        raise ValueError("Ungültige Uhrzeit (24:00 nur als Ende erlaubt).")
    return f"{hour}:{minute}"


def output_periods(data):
    periods = {}
    for i in range(1, 6):
        keys = [f"{prefix}{i}" for prefix in ("d", "e", "f", "h")]
        if not any(key in data for key in keys):
            continue
        if not all(key in data for key in keys):
            raise ValueError(f"Zeitfenster {i} wurde unvollständig gemeldet.")
        enabled, watts = as_int(data, f"d{i}"), as_int(data, f"h{i}")
        if enabled not in (0, 1) or watts is None or not 0 <= watts <= 800:
            raise ValueError(f"Ungültige Werte für Zeitfenster {i}.")
        periods[i] = (enabled, output_time(data[f"e{i}"]),
                      output_time(data[f"f{i}"], True), watts)
    if not all(i in periods for i in (1, 2, 3)):
        raise ValueError("Die ersten drei Zeitfenster fehlen; keine Änderung gesendet.")
    return periods


def build_output_schedule(data, slot, enabled, start, end, watts):
    periods = output_periods(data)
    if slot not in periods or enabled not in (0, 1) or not 0 <= watts <= 800:
        raise ValueError("Zeitfenster, Aktivierung oder Leistung ungültig (0–800 W).")
    start, end = output_time(start), output_time(end, True)
    def minutes(value):
        h, m = map(int, value.split(":"))
        return h * 60 + m
    if enabled and minutes(start) >= minutes(end):
        raise ValueError("Ende muss nach Start liegen; über Mitternacht zwei Fenster verwenden.")
    periods[slot] = (enabled, start, end, watts)
    active = sorted((minutes(s), minutes(e)) for on, s, e, w in periods.values() if on)
    if any(right[0] < left[1] for left, right in zip(active, active[1:])):
        raise ValueError("Aktive Zeitfenster überschneiden sich. Erst das andere Fenster deaktivieren.")
    mode = as_int(data, "md")
    if mode not in (0, 1):
        raise ValueError("Aktueller Automodus fehlt.")
    fields, expected = ["cd=07", f"md={mode}"], {"md": str(mode)}
    for i, (on, s, e, w) in periods.items():
        fields.extend((f"a{i}={on}", f"b{i}={s}", f"e{i}={e}", f"v{i}={w}"))
        expected.update({f"d{i}": str(on), f"e{i}": s, f"f{i}": e, f"h{i}": str(w)})
    return ",".join(fields), expected


def print_output_configuration(data):
    print("\nAUSGANGSKONFIGURATION")
    print(f"Gerät: {device_type} {device_id}")
    print(f"Automodus: {format_adaptive_mode(data)}")
    print("Lademodus: " + {0: "Gleichzeitig laden und entladen", 1: "Zuerst voll laden, dann entladen"}.get(as_int(data, "cs"), "nicht gemeldet"))
    for i in (1, 2):
        state = {0: "Aus", 1: "Ein"}.get(as_int(data, f"o{i}"), "nicht gemeldet")
        print(f"Ausgang {i}: {state}, aktuelle Leistung {value_or_dash(as_int(data, f'g{i}'), 'W')}")
    for i in range(1, 6):
        if f"d{i}" in data:
            state = {0: "Aus", 1: "Ein"}.get(as_int(data, f"d{i}"), "unbekannt")
            print(f"Zeitfenster {i}: {state}, {data.get(f'e{i}', '?')}–{data.get(f'f{i}', '?')}, {data.get(f'h{i}', '?')} W")
    print("Im Automodus bestimmt der Smart Meter den Sollwert; feste Wattwerte gelten im manuellen Modus.")
    print("Bei B2500 V2 sind OUT1/OUT2 hier Statusanzeigen; die Regelung erfolgt über Modus und Zeitfenster.")


def output_configuration(edit=False):
    if not device_type or not device_type.startswith(("HMA", "HMF", "HMK", "HMJ")):
        print("Diese Ausgangskonfiguration ist nur für B2500 V2 verfügbar.")
        return
    data = request_status()
    if not data:
        print("Keine Statusantwort; keine Änderung möglich.")
        return
    print_output_configuration(data)
    if not edit:
        return
    print("\n1. Automodus ein/aus\n2. Lade-/Entlademodus\n3. Zeitfenster und Ausgangsleistung\n0. Abbrechen")
    choice = input("Auswahl: ").strip()
    if choice == "0":
        return
    try:
        if choice in ("1", "2"):
            prompt = "Automodus [0=manuell, 1=Smart Meter]: " if choice == "1" else "Lademodus [0=gleichzeitig, 1=zuerst voll laden]: "
            value = int(input(prompt).strip())
            if value not in (0, 1):
                raise ValueError("Nur 0 oder 1 erlaubt.")
            payload = f"cd={'04' if choice == '1' else '03'},md={value}"
            expected = {"md" if choice == "1" else "cs": str(value)}
        elif choice == "3":
            slot = int(input("Zeitfenster-Nummer: "))
            periods = output_periods(data)
            if slot not in periods:
                raise ValueError("Dieses Zeitfenster wird vom Gerät nicht gemeldet.")
            old = periods[slot]
            on = int(input(f"Aktiv [0/1, aktuell {old[0]}]: ").strip() or old[0])
            start = input(f"Start [{old[1]}]: ").strip() or old[1]
            end = input(f"Ende [{old[2]}]: ").strip() or old[2]
            watts = int(input(f"Leistung 0–800 W [{old[3]}]: ").strip() or old[3])
            payload, expected = build_output_schedule(data, slot, on, start, end, watts)
        else:
            raise ValueError("Ungültige Auswahl.")
    except ValueError as exc:
        print(str(exc))
        return
    print(f"\nÄnderung für {device_type} {device_id}: {payload}")
    print("Wird dauerhaft gespeichert. Andere gemeldete Zeitfenster bleiben erhalten.")
    if input("Ausgangskonfiguration senden? [j/N]: ").strip().lower() not in ("j", "ja", "y", "yes"):
        print("Abgebrochen.")
        return
    mqtt_client.publish(command_topic, payload).wait_for_publish()
    for attempt in range(3):
        time.sleep(1)
        actual = request_status()
        if actual:
            matches = True
            for key, value in expected.items():
                try:
                    returned = output_time(actual.get(key), key.startswith("f")) if key.startswith(("e", "f")) else str(as_int(actual, key))
                except ValueError:
                    returned = None
                matches = matches and returned == value
            if matches:
                print_output_configuration(actual)
                print("✓ Ausgangskonfiguration zurückgelesen und bestätigt.")
                return
    print("⚠ Gesendet, aber nicht vollständig bestätigt. Aktuellen Status erneut abfragen.")


def menu():
    while True:
        print()
        print("=" * 68)
        print("HAUPTMENÜ")
        print("=" * 68)

        display_id = (
            format_mac(device_id)
            if device_id and len(device_id) == 12
            else device_id
        )

        print(
            f"Gerät: {device_type} {display_id} "
            f"[{device_namespace}]"
        )
        print()
        print("1. Smart-Meter-Konfiguration anzeigen")
        print("2. Smart Meter konfigurieren")
        print("3. CT-Phase setzen")
        print("4. CT-Phasendiagnose starten")
        print("5. LIVE-Daten des Speichers")
        print("6. Rohdaten anzeigen")
        print("7. Anderes MARSTEK-Gerät suchen")
        print("8. Smart Meter verbinden / Automodus aktivieren")
        print("9. Ausgangskonfiguration anzeigen")
        print("10. Ausgangskonfiguration setzen")
        print("11. Einzel-/Mehrgeräte-Konfiguration anzeigen")
        print("0. Beenden")
        print()

        selection = input("Auswahl: ").strip()

        if selection == "1":
            data = request_status()

            if data:
                print_configuration(data)
            else:
                print("Keine Antwort erhalten.")

        elif selection == "2":
            set_smart_meter()

        elif selection == "3":
            set_ct_phase()

        elif selection == "4":
            start_phase_diagnosis()

        elif selection == "5":
            live_data()

        elif selection == "6":
            show_raw_data()

        elif selection == "7":
            if select_discovered_device():
                configure_device_topics()

        elif selection == "8":
            connect_smart_meter()

        elif selection in ("9", "10"):
            output_configuration(edit=selection == "10")

        elif selection == "11":
            show_multi_device_configuration()

        elif selection == "0":
            print()
            print("Programm beendet.")
            break

        else:
            print("Ungültige Auswahl.")

# ============================================================
# MQTT CALLBACKS
# ============================================================

def on_connect(client, userdata, flags, reason_code, properties):
    global connection_error
    connection_error = None if reason_code == 0 else str(reason_code)
    if reason_code == 0:
        print()
        print("✓ Mit MQTT-Broker verbunden.")
    else:
        print()
        print(f"MQTT-Verbindung fehlgeschlagen: {reason_code}")
    connection_ready.set()
    if reason_code == 0 and response_topic:
        client.subscribe(response_topic)
    if reason_code == 0:
        for topic in DISCOVERY_TOPICS:
            client.subscribe(topic)


def on_message(client, userdata, msg):
    global response_data

    topic = msg.topic
    record_discovery(topic, live_response=not bool(msg.retain))

    # Normale Statusantwort des ausgewählten Gerätes
    if response_topic and topic == response_topic:
        payload = msg.payload.decode(
            "utf-8",
            errors="replace"
        )

        data = parse_payload(payload)

        # cd=28 und Zellabfragen enthalten ebenfalls ct_t/c0, sind aber
        # keine Laufzeitantwort auf cd=01. Sie dürfen diese nicht ersetzen.
        if "pe" in data and "vv" in data:
            with response_lock:
                if topic == response_topic and not bool(msg.retain):
                    response_data = data
                    response_received.set()

# ============================================================
# MAIN
# ============================================================

def main():
    global mqtt_client
    global broker_key

    mqtt_host, mqtt_port, mqtt_username, mqtt_password = (
        ask_connection_data()
    )
    broker_key = f"{mqtt_host}:{mqtt_port}"
    load_known_devices()

    mqtt_client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2
    )

    if mqtt_username:
        mqtt_client.username_pw_set(
            mqtt_username,
            mqtt_password
        )

    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message

    try:
        mqtt_client.connect(
            mqtt_host,
            mqtt_port,
            keepalive=30
        )

    except Exception as exc:
        print()
        print(f"MQTT-Verbindung fehlgeschlagen: {exc}")
        sys.exit(1)

    connection_ready.clear()
    mqtt_client.loop_start()

    try:
        if not connection_ready.wait(TIMEOUT):
            print("Keine MQTT-Verbindungsbestätigung erhalten.")
            return
        if connection_error:
            return
        if not select_discovered_device():
            print("Kein Gerät ausgewählt.")
            return

        configure_device_topics()
        time.sleep(0.5)
        menu()

    except KeyboardInterrupt:
        print()
        print("Programm beendet.")

    finally:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()


if __name__ == "__main__":
    main()
