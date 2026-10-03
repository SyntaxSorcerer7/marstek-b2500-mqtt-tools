# MARSTEK B2500 / HMJ-2 – Einzel- und Mehrgerätebetrieb per MQTT

## Zweck

Dieses Dokument beschreibt den derzeit bekannten Stand zum **Lesen und Setzen des Einzel-/Mehrgerätebetriebs** beim MARSTEK B2500 V2 / HMJ-2 über MQTT.

Der Fokus liegt auf folgender Topologie:

```text
B2500 A OUT1  ─────>  MPPT/PV-Eingang 1 des Wechselrichters
B2500 B OUT1  ─────>  MPPT/PV-Eingang 2 des Wechselrichters

Keine Leistungsleitung zwischen B2500 A und B2500 B.
```

Es geht **nicht** um eine Master/Slave-Leistungsverkabelung, bei der der Ausgang eines B2500 in einen PV-Eingang eines anderen B2500 geführt wird.

---

## Stand der Erkenntnisse

Es werden drei Evidenzstufen unterschieden:

- **Dokumentiert** – durch die aktuelle Reverse-Engineering-Dokumentation des HMJ-2-Protokolls beschrieben.
- **Experimentell bestätigt** – durch einen Vorher/Nachher-Test mit zwei realen HMJ-2 und Umschaltung über die offizielle MARSTEK-App beobachtet.
- **Offen** – Bedeutung oder Verhalten ist noch nicht ausreichend geklärt.

---

# 1. MQTT-Topics

Für HMJ-2 werden normalerweise folgende Topics verwendet.

## Befehl an das Gerät

```text
hame_energy/HMJ-2/App/<MAC>/ctrl
```

Beispiel:

```text
hame_energy/HMJ-2/App/AABBCCDDEEFF/ctrl
```

## Antwort vom Gerät

```text
hame_energy/HMJ-2/device/<MAC>/ctrl
```

Die MAC-Adresse wird ohne `:` oder `-` verwendet.

---

# 2. Aktuelle Konfiguration lesen

## Statusabfrage

Der vollständige Laufzeitstatus wird mit folgendem Befehl angefordert:

```text
cd=01
```

Beispiel mit `mosquitto_pub`:

```bash
mosquitto_pub \
  -t "hame_energy/HMJ-2/App/AABBCCDDEEFF/ctrl" \
  -m "cd=01"
```

Die Antwort enthält unter anderem:

```text
sm=...
bn=...
o1=...
o2=...
g1=...
g2=...
h1=...
sp=...
```

---

# 3. Relevante Felder

## `sm`

**Dokumentierter Stand:**

`sm` wird in der aktuellen Protokolldokumentation als

```text
Combination/multi-device model
```

geführt und vermutlich mit `cd=29` in Verbindung gebracht.

**Experimenteller Befund:**

Bei einem der getesteten HMJ-2 änderte sich nach Aktivierung des Mehrgerätebetriebs in der offiziellen App:

```text
sm=0  ->  sm=1
```

Gleichzeitig änderten sich:

```text
o2=1   -> o2=0
h1=800 -> h1=400
```

Damit ist `sm` derzeit der **beste bekannte Indikator für den Mehrgeräte-/Kombinationsmodus**.

### Praktische Interpretation

Aktueller Arbeitsstand:

```text
sm=0
```

spricht für normalen bzw. nicht aktivierten Kombinationsbetrieb.

```text
sm=1
```

spricht stark für aktivierten Mehrgeräte-/Kombinationsbetrieb.

Diese Interpretation ist durch den realen Vorher/Nachher-Test gestützt, aber noch nicht als vollständige Firmware-Spezifikation dokumentiert.

---

## `bn`

Die Bedeutung von `bn` ist weiterhin **nicht geklärt**.

Die aktuelle Protokolldokumentation führt:

```text
bn = Unknown
```

Im durchgeführten Vorher/Nachher-Test erschien `bn` bei keinem der beiden Geräte als verändertes Feld.

Daher gilt ausdrücklich:

> `bn` sollte derzeit nicht als sicherer Readback von `b2500_num` interpretiert werden.

Insbesondere ist nicht belegt, dass beispielsweise

```text
bn=5001
```

einen Slot 5001 oder eine bestimmte Rolle im Mehrgerätebetrieb bedeutet.

---

## `o2`

`o2` beschreibt den Aktivstatus von Ausgang 2.

```text
o2=0  -> OUT2 aus
o2=1  -> OUT2 ein
```

Beim getesteten Gerät änderte die MARSTEK-App im Mehrgerätebetrieb:

```text
o2=1 -> o2=0
```

Das passt zur beobachteten bzw. dokumentierten Betriebsweise, bei der im Parallel-/Mehrgerätebetrieb nur OUT1 verwendet wird.

---

## `h1`

`h1` ist der zurückgelesene Leistungswert des ersten Zeitfensters.

Beim Test:

```text
h1=800 -> h1=400
```

Die App reduzierte also beim betroffenen Gerät die Ausgangskonfiguration von 800 W auf 400 W.

Diese Änderung trat gemeinsam mit

```text
sm=0 -> 1
o2=1 -> 0
```

auf.

---

# 4. Experimenteller Vorher/Nachher-Befund

Zwei HMJ-2 wurden in der MARSTEK-App in den Mehrgerätebetrieb gebracht.

Beide Geräte sind separat mit dem Wechselrichter verbunden.

## Gerät A

Relevante Änderungen:

| Feld | Vorher | Nachher |
|---|---:|---:|
| `sm` | 0 | 1 |
| `o2` | 1 | 0 |
| `h1` | 800 | 400 |
| `g1` | 148 W | 202 W |
| `sp` | 296 W | 205 W |

## Gerät B

Bei Gerät B änderten sich `sm`, `o2` und `h1` im Vergleich nicht.

Die Leistungsregelung änderte sich jedoch:

| Feld | Vorher | Nachher |
|---|---:|---:|
| `g1` | 153 W | 204 W |
| `sp` | 160 W | 211 W |

---

# 5. Erkenntnis zur gemeinsamen Leistungsregelung

Vor der Gruppierung waren die Sollwerte stark unterschiedlich:

```text
Gerät A: sp = 296 W
Gerät B: sp = 160 W
```

Nach der Gruppierung:

```text
Gerät A: sp = 205 W
Gerät B: sp = 211 W
```

Auch die tatsächliche Ausgangsleistung war anschließend nahezu gleich:

```text
Gerät A: g1 = 202 W
Gerät B: g1 = 204 W
```

Das ist ein starker experimenteller Hinweis darauf, dass der Mehrgerätebetrieb eine **koordinierte Leistungsaufteilung** zwischen den beiden separat am Wechselrichter angeschlossenen B2500 aktiviert.

Vereinfacht:

```text
gemeinsamer Leistungsbedarf
          |
          v
  Mehrgeräte-Regelung
       /       \
      /         \
 B2500 A     B2500 B
  ~50 %        ~50 %
```

Es handelt sich dabei nicht um ein gemeinsames Batterie-BMS.

Jeder B2500 besitzt weiterhin seinen eigenen Akku und eigenen SOC.

---

# 6. Mehrgerätebetrieb setzen

Der dokumentierte MQTT-Befehl lautet:

```text
cd=29,single_mode=<0|1>,b2500_num=<5000..5499>
```

## `single_mode`

Dokumentiert:

```text
single_mode=0
```

bedeutet Mehrgerätebetrieb.

```text
single_mode=1
```

bedeutet Einzelgerätebetrieb.

---

## `b2500_num`

Dokumentiert ist lediglich ein gültiger Wertebereich:

```text
5000 .. 5499
```

Ein dokumentiertes Beispiel ist:

```text
cd=29,single_mode=0,b2500_num=5001
```

Die genaue Bedeutung von `b2500_num` ist noch nicht vollständig geklärt.

Es ist derzeit **nicht ausreichend belegt**, ob es sich um:

- eine Geräte-Slotnummer,
- eine Gruppennummer,
- eine Rollenkennung,
- oder eine andere interne Kennung

handelt.

Daher sollte nicht ohne weitere Tests davon ausgegangen werden, dass für zwei Geräte zwingend

```text
Gerät A -> 5001
Gerät B -> 5002
```

gesetzt werden muss.

---

# 7. Empfohlener Setzvorgang

Für die derzeit untersuchte Konfiguration sollte eine Software den Modus nicht blind setzen, sondern kontrolliert vorgehen.

## Schritt 1 – aktuellen Status sichern

Für beide Geräte:

```text
cd=01
```

Relevante Werte speichern:

```text
sm
bn
o1
o2
h1
g1
g2
sp
md
cs
```

---

## Schritt 2 – `cd=29` senden

Beispiel:

```text
cd=29,single_mode=0,b2500_num=5001
```

Der Wert `5001` ist ein dokumentiertes Beispiel, seine genaue interne Bedeutung ist aber noch offen.

---

## Schritt 3 – erneut lesen

Nach kurzer Wartezeit erneut:

```text
cd=01
```

Dann insbesondere vergleichen:

```text
sm
bn
o2
h1
sp
g1
```

---

# 8. Empfohlene Erkennung im Programmcode

Eine robuste Implementierung sollte den Modus momentan **nicht nur anhand eines einzigen Feldes endgültig behaupten**.

Empfohlene Logik:

```python
def detect_multi_device_mode(data):
    sm = int(data["sm"]) if "sm" in data else None
    o2 = int(data["o2"]) if "o2" in data else None
    h1 = int(data["h1"]) if "h1" in data else None

    if sm == 1:
        return "Mehrgerätebetrieb wahrscheinlich"

    if sm == 0:
        return "Einzelgerät / Kombination nicht aktiv wahrscheinlich"

    return "Modus nicht bestimmbar"
```

Zusätzliche Plausibilitätsindikatoren für die untersuchte Topologie:

```text
o2 = 0
h1 = 400
```

Diese Werte sollten aber nicht allein als Modusnachweis verwendet werden, weil sie theoretisch auch über andere Konfigurationen entstehen können.

---

# 9. Empfohlene Anzeige

Beispiel:

```text
EINZEL-/MEHRGERÄTE-KONFIGURATION
--------------------------------------------------
Modus:                  Mehrgerätebetrieb wahrscheinlich
sm:                     1
bn:                     0
OUT1:                   aktiv
OUT2:                   aus
Zeitfenster 1 Leistung: 400 W
Sollleistung:           205 W
Ausgangsleistung:       202 W
```

Bei unbekannter oder widersprüchlicher Konfiguration:

```text
Modus: nicht eindeutig
```

statt einen festen Zustand zu behaupten.

---

# 10. Einzelgerätebetrieb setzen

Der dokumentierte Parameter für den Einzelgerätebetrieb ist:

```text
single_mode=1
```

Das Befehlsformat bleibt:

```text
cd=29,single_mode=1,b2500_num=<5000..5499>
```

Da die Bedeutung von `b2500_num` noch nicht eindeutig geklärt ist, sollte eine Automatisierung den zuletzt bekannten bzw. durch weitere Tests bestätigten Wert verwenden und nicht willkürlich einen neuen Wert erzeugen.

Nach dem Setzen muss ebenfalls wieder mit

```text
cd=01
```

kontrolliert werden.

Erwartet wird nach dem bisherigen Teststand insbesondere eine Rückkehr von

```text
sm=1
```

in Richtung

```text
sm=0
```

Diese Rückrichtung wurde im beschriebenen Test allerdings noch nicht separat experimentell vermessen und sollte daher vor produktiver Automatisierung validiert werden.

---

# 11. Was derzeit als belastbar gilt

## Dokumentiert

- `cd=01` liest den Laufzeitstatus.
- `cd=29` konfiguriert den Multi-Device-Modus.
- `single_mode=0` = Mehrgerätebetrieb.
- `single_mode=1` = Einzelgerätebetrieb.
- `b2500_num` akzeptiert Werte von `5000` bis `5499`.
- `sm` steht mit Combination/Multi-Device-Betrieb in Zusammenhang.
- `bn` ist aktuell nicht entschlüsselt.
- `o1` und `o2` melden den Status von OUT1 und OUT2.
- `g1` und `g2` melden die Ausgangsleistungen.

## Durch den realen Test bestätigt

Bei Aktivierung des Mehrgerätebetriebs über die MARSTEK-App wurde bei einem Gerät beobachtet:

```text
sm: 0 -> 1
o2: 1 -> 0
h1: 800 -> 400
```

Danach regelten beide B2500 ihre Leistung nahezu identisch:

```text
sp: 205 / 211 W
g1: 202 / 204 W
```

Damit spricht viel dafür, dass der Modus die beiden separat angeschlossenen Geräte gemeinsam koordiniert.

## Noch offen

- Exakte Bedeutung von `bn`.
- Exakte interne Bedeutung von `b2500_num`.
- Ob beide Geräte unterschiedliche `b2500_num` benötigen.
- Ob `b2500_num` Geräte-, Gruppen- oder Rollenkennung ist.
- Warum sich `sm` beim zweiten getesteten Gerät im Vorher/Nachher-Diff nicht änderte.
- Vollständige Rückrichtung beim Umschalten Mehrgerät -> Einzelgerät.
- Ob `o2=0` und `h1=400` durch `cd=29` selbst oder durch zusätzliche App-Befehle gesetzt werden.

Der letzte Punkt ist besonders wichtig:

> Aus dem Vorher/Nachher-Test ist noch nicht bewiesen, dass `cd=29` allein auch `o2` bzw. `h1` verändert. Die MARSTEK-App kann beim Umschalten mehrere MQTT-/BLE-Befehle senden.

---

# 12. Empfehlung für weitere Reverse-Engineering-Tests

Für eine vollständig reproduzierbare MQTT-Implementierung sollten noch zwei kontrollierte Versuche durchgeführt werden.

## Test A – Rückkehr zum Einzelgerät

1. Rohdaten beider Geräte im Mehrgerätebetrieb speichern.
2. In der MARSTEK-App auf Einzelgerätebetrieb zurückstellen.
3. Rohdaten erneut lesen.
4. Änderungen vergleichen.

Besonders:

```text
sm
bn
o2
h1
sp
g1
```

## Test B – MQTT `cd=29` isoliert testen

Aus einem bekannten Einzelgeräte-Zustand:

```text
cd=29,single_mode=0,b2500_num=5001
```

senden.

Danach ausschließlich `cd=01` lesen.

Wenn sich dann beispielsweise direkt

```text
sm=0 -> 1
o2=1 -> 0
h1=800 -> 400
```

ändert, wäre belegt, dass `cd=29` diese Konfiguration selbst auslöst.

Bleiben `o2` und `h1` unverändert, setzt die offizielle App beim Aktivieren des Mehrgerätebetriebs zusätzliche Befehle.

---

# 13. Quellen

Aktuelle Reverse-Engineering-Dokumentation:

- https://github.com/tomquist/hm2mqtt/blob/main/docs/b2500.md
- https://github.com/iacavd/marstek-hm2mqtt/blob/main/docs/b2500.md

Ältere MQTT-Protokollsammlung:

- https://github-wiki-see.page/m/tomquist/hm2500pub/wiki/MQTT

---

## Kurzfassung

Für den aktuellen Implementierungsstand:

### Lesen

```text
cd=01
```

Primärer Indikator:

```text
sm=0 -> wahrscheinlich Einzelgerät / Kombination aus
sm=1 -> wahrscheinlich Mehrgeräte-/Kombinationsmodus
```

Zusätzliche Plausibilität für die getestete Zwei-Geräte-Topologie:

```text
o2=0
h1=400
```

### Setzen

```text
cd=29,single_mode=0,b2500_num=5001
```

Mehrgerätebetrieb.

```text
cd=29,single_mode=1,b2500_num=<bekannter gültiger Wert>
```

Einzelgerätebetrieb.

`b2500_num` noch nicht als vollständig verstanden behandeln.

### Wichtig

Eine produktive Implementierung sollte nach jedem Schreibbefehl erneut

```text
cd=01
```

abfragen und die tatsächliche Zustandsänderung protokollieren.
