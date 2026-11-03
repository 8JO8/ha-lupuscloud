# Lupus Cloud für Home Assistant

Inoffizielle Home-Assistant-Integration für Geräte in der [Lupus Cloud](https://lupuscloud.com)
(Lupus Electronics), z. B. die Mobilfunk-Rauchwarnmelder. Alarme kommen **sofort per Webhook**,
Messwerte (Temperatur, Batterie, Empfang, Meldungen) werden regelmäßig abgefragt.

> **Hinweis:** Lupus bietet keine offizielle API. Die Integration nutzt dieselben Schnittstellen
> wie die Web-App lupuscloud.com. Änderungen seitens Lupus können sie jederzeit brechen.
> Dieses Projekt steht in keiner Verbindung zu Lupus Electronics.
> **Verlasse dich für den Brandschutz nicht ausschließlich auf Home Assistant.**

## Funktionen

- Login mit E-Mail und Passwort, **Zwei-Faktor-Authentifizierung** (Authenticator-App, SMS, E-Mail) wird unterstützt
- Alle Gebäude und Geräte des Kontos, neue Geräte erscheinen automatisch
- **Sofortige Alarme:** Die Integration richtet in der Lupus Cloud automatisch einen Webhook für jedes
  Gebäude ein und entfernt ihn wieder, wenn die Integration gelöscht wird
- **Einstellbar, welche Ereignisse als Alarm gelten** (Rauch, Hitze, Temperatur, Bewegung, Panik,
  Sabotage, Gerätewarnung …)
- **Räume → Bereiche:** Bei der Einrichtung werden alle Lupus-Räume (Gebäude / Stockwerk /
  Wohnung / Zimmer) angezeigt, in denen Geräte hinterlegt sind. Jedem Raum ordnest du einen
  Home-Assistant-Bereich zu, und die Geräte werden dort angelegt. **Räume ohne Bereich werden nicht
  eingebunden.** Kommt später ein Gerät in einem neuen Raum hinzu, erscheint unter
  *Einstellungen → Reparaturen* ein Hinweis, und du kannst den Raum unter *Konfigurieren* zuordnen.
  Hebst du eine Zuordnung auf, werden die Geräte dieses Raums aus Home Assistant entfernt.
  Verschiebst du ein Gerät in Home Assistant von Hand in einen anderen Bereich, bleibt es dort,
  auch nach Neustarts. Nur wenn du die Zuordnung seines Raums in den Optionen änderst, zieht es
  in den neuen Bereich um.

### Unterstützte Geräte

Die Integration liest **alle Geräte** eines Kontos generisch aus und legt Entitäten nur für die
Werte an, die ein Gerät tatsächlich liefert.

| Gerät | Status |
|---|---|
| Mobilfunk-Rauchwarnmelder (`RWM`) | getestet |
| Rauch-, Hitze-, Bewegungsmelder, Notruf-/Alarmtaster, Sirene, Temperatur-/Feuchtesensor | sollte funktionieren (Alarm mit passender Geräteklasse, Status, Batterie, Empfang, Meldungen), ungetestet |
| Zähler (OMS: Wasser, Wärme, Strom, Gas, Heizkostenverteiler) | erscheinen als Gerät mit Status/Batterie/Empfang, **Verbrauchswerte werden noch nicht ausgelesen** |

Rückmeldungen zu weiteren Gerätetypen gerne als Issue, idealerweise mit den Diagnosedaten
der Integration (sie sind bereits geschwärzt).

### Entitäten pro Gerät

| Entität | Beschreibung |
|---|---|
| Alarm (binary_sensor, Rauch/Hitze/Bewegung/Sicherheit) | sofort per Webhook, zusätzlich per Abfrage |
| Problem, Batterie, Sabotage (binary_sensor) | Gerätezustand |
| Temperatur | Raumtemperatur des Melders |
| Batteriespannung, Batteriezustand, Empfangsqualität | Diagnose |
| Status, Statusgrund, Letzter Kontakt | Diagnose |
| Letzte Meldung | letzter Eintrag des Geräte-Protokolls, die letzten 5 als Attribut |
| Alarm bestätigen (button) | wie in der Web-App |
| Gerätefunktion bestätigen (button, standardmäßig deaktiviert) | z. B. nach Demontage-Warnung |

Zusätzlich gibt es pro Gebäude einen **Sammel-Alarm** und für das Konto die Sensoren
*Ungelesene Benachrichtigungen*, *Letzte Benachrichtigung* und *Webhook* (Status).

### Events für Automationen

| Event | Wann |
|---|---|
| `lupuscloud_alarm` | ein Gerät geht in einen der ausgewählten Alarme (Quelle `webhook` oder `poll`) |
| `lupuscloud_webhook` | jede Push-Nachricht der Lupus Cloud (`event_type`, `status_reason`, `device_uuids`, `alarm`, `payload`) |
| `lupuscloud_notification` | jede neue Benachrichtigung aus der Lupus Cloud |

Beispiel:

```yaml
automation:
  - alias: Rauchalarm
    triggers:
      - trigger: event
        event_type: lupuscloud_alarm
    actions:
      - action: notify.notify
        data:
          title: "🔥 Rauchalarm"
          message: "{{ trigger.event.data.location }}"
```

## Installation

### HACS (empfohlen)

[![In HACS öffnen](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=8JO8&repository=ha-lupuscloud&category=integration)

1. HACS → ⋮ → *Benutzerdefinierte Repositories* → `https://github.com/8JO8/ha-lupuscloud`, Typ *Integration*
   (oder den Button oben verwenden)
2. „Lupus Cloud“ installieren und Home Assistant neu starten
3. *Einstellungen → Geräte & Dienste → Integration hinzufügen → Lupus Cloud*

### Manuell

Den Ordner `custom_components/lupuscloud` nach `<config>/custom_components/` kopieren und
Home Assistant neu starten.

## Voraussetzung für sofortige Alarme

Die Lupus Cloud muss Home Assistant aus dem Internet erreichen können. Eine dieser Varianten genügt:

- **Home Assistant Cloud (Nabu Casa):** wird automatisch genutzt (Cloudhook)
- **Externe URL** unter *Einstellungen → System → Netzwerk*
- **Webhook-URL in den Optionen der Integration**, z. B. bei einem Reverse Proxy

Ohne erreichbare URL funktioniert die Integration trotzdem, Alarme werden dann aber nur im
Abfrageintervall erkannt (eine Reparaturmeldung weist darauf hin).

## Optionen

*Einstellungen → Geräte & Dienste → Lupus Cloud → Konfigurieren*

- Ereignisse, die als Alarm gelten
- Abfrageintervall (Standard 5 Minuten)
- Webhook an/aus und optionale öffentliche URL
- Zuordnung Räume → Bereiche (= welche Geräte eingebunden werden)

## Passwort ändern & Zwei-Faktor-Authentifizierung

- **Passwort in der Lupus Cloud geändert?** Über *⋮ → Neu konfigurieren* bei der Integration kannst
  du das neue Passwort jederzeit eingeben. Schlägt die Anmeldung fehl (z. B. weil das Passwort
  geändert und alle Sitzungen abgemeldet wurden), fordert Home Assistant dich automatisch zur
  erneuten Anmeldung auf. Geräte, Entitäten und Einstellungen bleiben dabei erhalten.
- **2FA:** Ist sie aktiv, fragt der Dialog nach der Anmeldung zusätzlich nach dem Code
  (Authenticator-App, SMS oder E-Mail, je nachdem was eingerichtet ist). Danach arbeitet die
  Integration mit einem Refresh-Token weiter. Erst wenn dieses abläuft, musst du dich erneut mit
  Code anmelden.

## Sicherheit & Datenschutz

- Die Anmeldung erfolgt direkt bei AWS Cognito (wie die Web-App). E-Mail, Passwort und
  Refresh-Token werden im Config-Entry von Home Assistant gespeichert, damit sich die Integration
  nach Ablauf des Tokens selbst neu anmelden kann. Bei aktiver 2FA ist dann eine erneute Anmeldung
  in Home Assistant nötig.
- Der Webhook hat eine zufällige 256-Bit-ID. Zusätzlich muss jede Anfrage ein zufälliges
  Bearer-Token enthalten, das nur der Lupus Cloud bekannt ist. Anfragen ohne gültiges Token
  werden abgelehnt.
- Standortdaten (Koordinaten, Adresse) aus Webhook-Nachrichten werden entfernt, bevor sie in
  Zuständen, Events oder Logs landen. Diagnosedaten werden geschwärzt.
- Die Integration ändert nur den eigenen Webhook in der Lupus Cloud, andere Webhooks bleiben
  unberührt.

## Entwicklung

```bash
docker compose -f dev/docker-compose.yml up -d
```

Home Assistant läuft dann unter http://localhost:8123 mit eingebundener Integration.
`dev/tools/` enthält Hilfsskripte (Lupus-Webhooks anzeigen, Testalarm senden, Alarm simulieren).

## Rechtliches, Quellen & Marken

Inoffizielles Projekt ohne Verbindung zur Lupus-Electronics GmbH. „Lupus“, „Lupus Electronics“,
„Lupus Cloud“ und das Wolf-Logo sind Marken der Lupus-Electronics GmbH und werden nur
beschreibend verwendet. Quellen aller übernommenen Bestandteile (Logo, Bezeichnungen,
Schnittstellen), Zweck, Haftungsausschluss und Kontakt für Entfernungswünsche stehen in
**[NOTICE.md](NOTICE.md)**.

## Lizenz

[MIT](LICENSE). Gilt nur für den Code dieses Projekts, nicht für Marken und Logo von Lupus
Electronics.
