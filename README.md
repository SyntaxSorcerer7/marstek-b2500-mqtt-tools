# MARSTEK B2500 MQTT tools

Eine kleine, lokale Kommandozeilenhilfe für MARSTEK B2500 V2 / HMJ-2 über MQTT. Sie unterstützt die Geräteerkennung, das Auslesen von Live-Daten und Konfiguration sowie die Einrichtung kompatibler Smart Meter und Ausgangs-Zeitfenster.

> [!WARNING]
> Die MQTT-Befehle können die Konfiguration des Speichers dauerhaft verändern. Vor Änderungen den aktuellen Status sichern, nur mit einem eigenen Gerät arbeiten und alle Werte nach dem Zurücklesen prüfen. Das Projekt ist unabhängig von MARSTEK und ohne Gewähr.

## Was enthalten ist

- Geräteerkennung im lokalen `hame_energy`-Namespace und über die `hm2mqtt`-Bridge
- Abfrage von Live- und Konfigurationsdaten
- Smart-Meter-Konfiguration mit Rückleseprüfung
- Ausgabe-/Zeitfenster-Konfiguration (0–800 W) mit Validierung
- Hinweise zum Einzel- und Mehrgerätebetrieb: [Dokumentation](docs/multi-device-mode.md)

Nicht enthalten sind aufgezeichnete MQTT-Daten, Geräteseriennummern/MAC-Adressen, lokale Netzwerkeinstellungen oder Zugangsdaten.

## Schnellstart

Voraussetzung: Python 3.10 oder neuer und ein MQTT-Broker, den das eigene B2500 erreichen kann.

```bash
git clone https://github.com/USER/marstek-b2500-mqtt-tools.git
cd marstek-b2500-mqtt-tools
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python src/hmj2_mqtt_tool.py
```

Trage in `.env` ausschließlich die Daten deines eigenen Brokers ein. Alternativ fragt das Tool die Werte interaktiv ab. Die `.env`-Datei und der lokale Gerätecache sind durch `.gitignore` ausgeschlossen.

## Bedienung

Beim Start sucht das Tool nach bekannten Geräten und aktuellen MQTT-Antworten. Wähle anschließend das gewünschte Gerät. Die Menüpunkte führen vor schreibenden Aktionen durch eine Bestätigung; anschließend liest das Tool den Status zurück und meldet nur eine passende Antwort als Erfolg.

Die MQTT-Topics folgen dem bekannten HMJ-2-Schema:

```text
hame_energy/HMJ-2/App/<MAC>/ctrl       # Befehl
hame_energy/HMJ-2/device/<MAC>/ctrl    # Antwort
```

`<MAC>` ist ein Beispielplatzhalter, keine echte Gerätekennung.

## Weitere Energieprojekte

- [Envertech Local](https://github.com/SyntaxSorcerer7/envertech-local) – lokale Home-Assistant-Integration für Envertech-Mikrowechselrichter
- [Solar Flow Card](https://github.com/SyntaxSorcerer7/home-assistant-solar-flow-card) – HACS-Dashboard-Karte für PV-, Batterie-, Haus- und Netzenergieflüsse
- [Projektübersicht](https://github.com/SyntaxSorcerer7) – Einstiegspunkt für alle Solar- und Home-Assistant-Projekte

## Tests

Die Tests verwenden simulierte MQTT-Clients und benötigen weder Broker noch Gerät:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

## Beitragen

Pull Requests mit reproduzierbaren Beobachtungen sind willkommen. Bitte niemals Zugangsdaten, IP-Adressen, MAC-Adressen, Cloud-IDs oder komplette MQTT-Aufzeichnungen einchecken. Beschreibe Firmware-Version, Vorgehen und erwartetes bzw. tatsächliches Verhalten anonymisiert.

## Quellen und Einordnung

Die Befehle und Feldnamen beruhen auf Beobachtungen und der [B2500-Protokollreferenz von hm2mqtt](https://github.com/tomquist/hm2mqtt/blob/main/docs/b2500.md). Sie können je nach Firmware abweichen.
