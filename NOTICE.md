# Hinweise, Quellen und Marken / Notices, sources and trademarks

## Keine Verbindung zu Lupus Electronics / No affiliation

Dieses Projekt ist ein **inoffizielles, privates Open-Source-Projekt**. Es wird weder von der
Lupus-Electronics GmbH entwickelt noch unterstützt, geprüft oder empfohlen, und es besteht keine
geschäftliche Verbindung.

This is an **unofficial, private open-source project**. It is not developed, endorsed, reviewed or
supported by Lupus-Electronics GmbH and is not affiliated with it in any way.

## Marken / Trademarks

„Lupus“, „Lupus Electronics“, „Lupus Cloud“ und das Wolf-Logo sind Marken bzw. geschäftliche
Bezeichnungen der Lupus-Electronics GmbH. Sie werden hier beschreibend verwendet, um anzugeben,
mit welchem Dienst diese Software zusammenarbeitet.

**Markennamen und Logo werden mit freundlicher Genehmigung der Lupus-Electronics GmbH verwendet.**
Die Lupus-Electronics GmbH hat der Nutzung ihrer Markennamen und Logos im Zusammenhang mit diesem
Projekt am 4. November 2026 per E-Mail zugestimmt. Das Symbol der Integration darf in der
vorliegenden Form verwendet werden.

"Lupus", "Lupus Electronics", "Lupus Cloud" and the wolf logo are trademarks or trade names of
Lupus-Electronics GmbH. They are used here descriptively to indicate which service this software
interoperates with.

**The brand names and the logo are used with kind permission of Lupus-Electronics GmbH**, granted
by e-mail on 4 November 2026 for use in connection with this project, including the integration
icon in its current form.

## Quellen / Sources

| Bestandteil | Quelle |
|---|---|
| `custom_components/lupuscloud/brand/icon.png`, `icon@2x.png` | Wolf-Symbol aus dem öffentlich ausgelieferten Logo der Web-App (`https://lupuscloud.com/assets/images/icons/logo-lupus.svg`), Farben aus dem Favicon (`https://lupuscloud.com/favicon.ico`, Orange `#EF6B00`, Weiß). Nur für die Darstellung der Integration in Home Assistant neu gerendert, inhaltlich nicht verändert. © Lupus-Electronics GmbH, Verwendung mit freundlicher Genehmigung (E-Mail vom 4. November 2026). |
| Endpunkte, Datenformate, Cognito-Pool- und Client-ID in `api.py` / `const.py` | Beobachtet an den Anfragen, die die öffentliche Web-App `https://lupuscloud.com` im Browser eines angemeldeten Nutzers an `api.lupusiot.de` und AWS Cognito stellt. Pool-ID und Client-ID stehen öffentlich im JavaScript der Web-App. Es wurde kein fremder Code übernommen. |
| SRP-Anmeldeverfahren in `api.py` | Eigene Implementierung des öffentlich dokumentierten SRP-6a-Protokolls (RFC 5054, 3072-Bit-Gruppe) in der Variante von AWS Cognito, geprüft gegen die Open-Source-Bibliothek [pycognito](https://github.com/NabuCasa/pycognito) (Apache-2.0). Kein Code kopiert. |
| Gerätetyp-, Status- und Ereignisbezeichnungen in `const.py`, `sensor.py`, Übersetzungen | Angelehnt an die Bezeichnungen, die die Web-App anzeigt (öffentliche Sprachdatei `https://lupuscloud.com/assets/i18n/de_DE.json`). |

## Zweck und Nutzung / Purpose and use

- Die Software dient ausschließlich der **Interoperabilität**: Nutzer können ihre **eigenen**
  Geräte mit ihren **eigenen** Zugangsdaten in Home Assistant einbinden, genau so, wie sie sie
  auch in der Web-App sehen und bedienen können.
- Es werden **keine Schutzmaßnahmen umgangen**. Anmeldung, 2FA und Berechtigungen der Lupus Cloud
  bleiben unverändert wirksam. Es werden nur Daten abgerufen, auf die das jeweilige Konto ohnehin
  Zugriff hat.
- Die Abfragen sind zurückhaltend dimensioniert (Standard alle 5 Minuten, Push per Webhook über
  die offizielle Webhook-Funktion der Lupus Cloud).
- Nutzer sind selbst dafür verantwortlich, die Nutzungsbedingungen der Lupus Cloud einzuhalten.

## Haftungsausschluss / Disclaimer

Die Software wird **ohne jede Gewährleistung** bereitgestellt (siehe [LICENSE](LICENSE)).
Sie ist **kein Ersatz** für die Alarmierung durch die Lupus-Geräte oder die Lupus Cloud selbst
und darf nicht als alleiniges Sicherheits- oder Brandschutzsystem verwendet werden.

The software is provided **without any warranty** (see [LICENSE](LICENSE)). It is **not a
replacement** for the alarming of the Lupus devices or the Lupus Cloud itself and must not be used
as the only safety or fire protection system.

## Kontakt / Contact

Anmerkungen zu Namen, Logo oder Inhalten des Projekts bitte als Issue im Repository. Sie werden
umgehend bearbeitet.

Remarks about names, the logo or other content of this project can be raised as an issue in this
repository and will be handled promptly.
