# Crypto Momentum: kostenlose Startversion

Vorbereiteter Beobachtungsagent für X, kostenlose Marktdaten und Telegram.
Stand: 30. Oktober 2026. Quellcode im GitHub-Repository bereitgestellt;
Telegram-Kopplung und authentifizierte Live-Abnahme stehen noch aus.

## Was diese Version tut

* Englische X-Posts in einer begrenzten Stichprobe abfragen, zunächst ausgehend
  von `@ZeroSumEnjoyer`, `@wizardofsoho` und `@rektmando`.
* Abwechselnd diese Accounts, eine kleine allgemeine Suche und konkrete bereits
  gefundene Contract-Adressen prüfen. Insgesamt höchstens 16 Datensätze je
  30-Minuten-Durchlauf, inklusive wiederholter Messungen.
* Konkrete Contract-Adressen in Posts und aufgelösten Links erkennen. Einen
  Cashtag allein nicht als eindeutigen Token behandeln. Symbol-only-Posts
  erzeugen in dieser Startversion keine Kandidaten.
* Nur Token der Fomo-Chains Solana, Base, BNB Chain und Monad prüfen. Bei einem
  mehrdeutigen Contract auf mehreren Chains folgt keine Meldung. Das Vorhandensein
  auf einer unterstützten Chain beweist nicht die Kaufbarkeit des einzelnen Tokens
  in Fomo; diese muss im Handelsdialog geprüft werden.
* Ausschliesslich das Feld `marketCap` von DexScreener verwenden. Keine FDV als
  Ersatz. Es muss bekannt, positiv und strikt kleiner als USD 1'000'000 sein.
* Ein Mindestalter von 60 Minuten anhand eines bereits so lange bestehenden
  Handelspools belegen. Das ist eine Untergrenze für das Tokenalter, kein
  rekonstruierter Mint-Zeitpunkt.
* Liquidität und Poolvolumen mit Sicherheitsinformationen und einer Stichprobe
  tatsächlich ausgeführter Swaps bei GeckoTerminal abgleichen. Volumen desselben
  Pools und identische Swap-Ereignisse nicht doppelt zählen.
* Reply-/Retweet-Zuwachs aus zwei eigenen Messungen berechnen. Verschiedene
  Autoren, Accountalter, Follower-/Following-Zahlen, Werbung und kopierte Texte
  als Qualitätsindizien prüfen.
* Unbekannte Pflichtdaten zum Ausschluss führen lassen. Auch ein leeres Ergebnis
  ist ein gültiges Ergebnis. Keine zwei Signale pro Tag erzwingen.
* Erst nach 90 Minuten Aufwärmzeit und zwei geeigneten Prüfungen im Abstand von
  mindestens 20 und höchstens 45 Minuten eine Beobachtung als bestätigt behandeln.
* Optional höchstens zwei Forschungsbeobachtungen pro Schweizer Kalendertag per
  Telegram senden. Dasselbe Token höchstens einmal innerhalb von 24 Stunden.
* Fomo-Käufe und Verkäufe manuell belassen.

## Die Grenzen sind wesentlich

Diese Version ist ein testbarer Beobachtungsprototyp. Sie ist kein erprobtes
Handelssystem. Ein Score von 75 ist keine Gewinnwahrscheinlichkeit.

16 X-Datensätze je Durchlauf liefern eine kleine Stichprobe, keine vollständige
X-Abdeckung. Die Velocity bezieht sich ausschliesslich auf diese Stichprobe.
Wird sie abgeschnitten, können Autoren, Token und Wachstumsphasen fehlen. Drei
verschiedene Autoren sind kein Beweis für drei wirtschaftlich unabhängige Personen.
Followerzahlen sind kein Beweis für echte Follower. Kopier- und Werbefilter sind
konservative Regeln, keine vollständige semantische Analyse.

Halterkonzentration, bekannte Creator-Anteile, Verkaufsbeschränkungen und auffällige
Swap-Stichproben werden geprüft. Verdeckte Insider-Cluster, Sybil-Wallets und
Waschhandel sind damit nicht vollständig auszuschliessen. Der Agent bezeichnet
Sicherheitsdaten deshalb nie als Beweis der Risikofreiheit. Besonders strenge
Pflichtfelder können auf einzelnen Chains dazu führen, dass gar keine Beobachtung
die Filter besteht. Genau das muss der erste Beobachtungstest sichtbar machen.

Das fachliche Erfolgskriterium bleibt: innerhalb von sechs Stunden zuerst +30 %
netto und nicht vorher -15 %. **Diese Version behauptet nicht, es bereits messen
oder erreichen zu können.** Fomo liefert hier keinen bestätigten automatisierten
Zugang zu ausführbaren Quotes und individuellen Gebühren. Es wird keine
Trefferquote aus blossen Poolpreisen oder 30-Minuten-Snapshots erzeugt. Gesendete
Beobachtungen erhalten ausdrücklich `net_outcome: unverified`. Für eine spätere
Auswertung sind zeitlich passende ausführbare Quotes beziehungsweise tatsächliche
Fills samt allen Kosten und ausreichend feine Kursdaten erforderlich. Die -15 %
sind ein Auswertungskriterium und keine garantierte Verlustgrenze.

Der Fomo-Feed wird nicht automatisch ausgelesen. Es wird kein Fomo-Zugang benötigt.

## Kostenlos bleiben

Die vorgesehene Kombination ist GitHub Free mit öffentlichem Repository,
Apify Free, öffentliche DexScreener-/GeckoTerminal-/GoPlus-Endpunkte und Telegram.
Der Code prüft vor Actor-Starts, ob der Apify-Tarif eindeutig Free mit einem
Grundpreis von USD 0 ist. Ein unbekannter oder kostenpflichtiger Tarif stoppt den
Durchlauf. Ein Tarifwechsel oder eine Aufladung wird niemals ausgelöst.

Bei 48 Durchläufen täglich, 16 Datensätzen und 31 Tagen ergeben sich
`48 × 16 × 31 × USD 0.00015 = USD 3.5712` Actor-Entgelte. Hinzu kommen
Plattform- und Speicherverbrauch aus demselben Gratisguthaben. Das ist eine
Kalkulation, keine garantierte Gesamtverbrauchsprognose. Der tatsächliche Verbrauch
muss im Testlauf geprüft werden. Vor Starts werden der aktuelle Actor-Preis und
die gesamte monatliche Kontonutzung kontrolliert. Ab einer Nutzung von ungefähr
USD 4.30 wird kein neuer X-Lauf gestartet. Das lässt Reserve unter dem
konfigurierten Limit von USD 4.50 und dem monatlichen USD-5-Gratisguthaben.

Ein einzelner Actor-Lauf erhält eine Ausgabengrenze und 45 Sekunden Laufzeit.
X-Läufe werden nicht blind wiederholt. Beim nächsten Durchlauf wird ein noch
laufender Auftrag nur abgefragt. Andere Nutzung desselben Apify-Kontos reduziert
das verfügbare Guthaben. Bei Erreichen der Grenze pausieren X-Abfragen bis zum
nächsten Abrechnungszyklus. Keine automatische kostenpflichtige Ersatzquelle.

Das Projekt muss für den vorgesehenen GitHub-Gratisbetrieb öffentlich sein.
Veröffentlicht werden nur Code und generische Einstellungen. Tokens und
Portfoliowerte gehören in Secrets; der Laufzustand liegt in einem privaten
Apify-Key-Value-Store. Konsolenausgaben enthalten keine Tokens oder Chat-IDs.
GitHub-Schedules können sich verzögern oder ausfallen. Bei 60 Tagen ohne
Repository-Aktivität kann GitHub den Zeitplan deaktivieren. Es gibt keinen
automatischen Schein-Commit als Umgehung; die Aktivierung ist dann manuell zu
prüfen. Das ist kein garantierter Echtzeitdienst.

## Einrichtung, bevorzugter Weg

1. GitHub mit ChatGPT verbinden und den Repository-Link nennen. Das vorbereitete
   Paket kann dann mit den erlaubten GitHub-Funktionen eingespielt werden.
2. In GitHub ein **öffentliches** Repository namens `crypto-momentum` erstellen:
   <https://github.com/new>. Keine privaten Daten oder Zugangsschlüssel hochladen.
3. Im Repository **Settings → Secrets and variables → Actions → Secrets** öffnen.
4. Die folgenden Repository-Secrets anlegen:

| Secret | Inhalt | Quelle |
| --- | --- | --- |
| `APIFY_TOKEN` | Eigener Apify-API-Token | Apify Console → Settings → Integrations, <https://console.apify.com/account/integrations> |
| `TELEGRAM_BOT_TOKEN` | Token des eigenen neuen Bots | Eigener Dialog mit `@BotFather` |
| `TELEGRAM_PAIR_CODE` | Frei gewählter geheimer Kopplungscode mit mindestens 20 Zeichen | Selbst wählen; keinen API-Token dafür verwenden |
| `PORTFOLIO_CHF` | Optional der aktuelle Gesamtwert, anfangs z. B. `100` | Eigener Portfoliowert; bei Wachstum manuell aktualisieren |

Tokens nicht hier im Chat, im Repository, im README oder in Workflow-Eingabefeldern
eintragen. Nur die dafür vorgesehenen geschützten Secrets verwenden.

5. Im privaten Chat mit dem eigenen Telegram-Bot senden:

   ```text
   /start DEIN_GEHEIMER_KOPPLUNGSCODE
   ```

   Den Platzhalter durch den exakt gleichen Code wie im Secret ersetzen. Die
   Nachricht wird ausschliesslich dem eigenen Bot gesendet. Sie muss weniger als
   24 Stunden alt sein. Ein eigener neuer Bot ist nötig; ein bereits vorhandener
   Webhook wird nicht überschrieben.

6. Unter **Actions → Momentum-Wächter → Run workflow** zuerst den Modus `pair`
   wählen. Der Agent liest nur Nachrichten mit dem passenden Code, bindet genau
   einen privaten Chat und sendet eine Verbindungsbestätigung. Die Chat-ID wird
   privat gespeichert; sie muss weder im Chat genannt noch öffentlich protokolliert
   werden.
7. Danach denselben Workflow mit `check` ausführen. Er prüft die Verbindungen und
   den Free-Tarif, ohne einen X-Actor zu starten. Eine Testnachricht sollte in
   Telegram ankommen.
8. Mit `sample` genau einen X-Datensatz abrufen. Dieser Test verbraucht einen
   kleinen Teil des kostenlosen Kontingents und sendet eine Testbestätigung.
   Die Dienste wurden lokal erreichbar getestet, der authentifizierte X-Abruf
   muss hier erstmals mit dem eigenen Konto bestätigt werden.

## Beobachtung aktivieren

Erst wenn die drei manuellen Schritte erfolgreich sind:

* Unter **Settings → Secrets and variables → Actions → Variables** eine
  Repository-Variable `MONITOR_ENABLED` mit dem Wert `true` erstellen.
* `SIGNAL_ALERTS_ENABLED` zunächst nicht setzen oder auf `false` lassen.

Der Workflow läuft dann ungefähr alle 30 Minuten, jeweils um Minute 07 und 37.
Im Beobachtungsmodus schreibt er nur den privaten Zustand und eine kurze
Zusammenfassung in die Workflow-Ausgabe. Es gibt keine automatischen
Forschungsmeldungen in Telegram. Das Ausbleiben einer Meldung bedeutet daher
zunächst nicht, dass der Agent ausgefallen ist.

Im Apify-Key-Value-Store `crypto-momentum-state-v1` liegt der Datensatz `STATE`.
Das Feld `audit` zeigt Kandidatenzahlen und konkrete Ausschlussgründe. Nach
mindestens 48 bis 72 Stunden die Datenlücken, Guthabennutzung und ausgeschlossenen
Kandidaten prüfen. Dieser kurze Test prüft die Funktion, noch keine profitable
Trefferquote. Erst nach ausreichender Prüfung können Forschungsbeobachtungen mit
der Variable `SIGNAL_ALERTS_ENABLED=true` freigeschaltet werden. Dies ändert die
Verlust- und Manipulationsrisiken nicht.

Ein manuelles `signals` funktioniert ebenfalls nur mit dieser Variable. Zum
Pausieren `MONITOR_ENABLED=false` setzen. Manuelle Verbindungstests sind zusätzliche
Testnachrichten; die Grenze von zwei gilt für automatische Forschungsbeobachtungen.

## Manueller Upload als Alternative

Falls keine GitHub-Verbindung verwendet wird, ZIP entpacken und den **Inhalt** des
Ordners `crypto-momentum` ins Repository hochladen. `agent.py`, `signals.py` und
`config.json` müssen direkt im Repository-Hauptverzeichnis liegen. Den Ordner
`.github` ebenfalls hochladen; auf dem Mac macht `⌘⇧.` ihn im Finder sichtbar.
Bei einem Upload über die GitHub-Weboberfläche lässt sich der Workflow alternativ
mit **Add file → Create new file** unter dem exakten Pfad
`.github/workflows/momentum.yml` anlegen und sein Inhalt hineinkopieren. Ohne
diese Datei erscheint der Workflow nicht unter Actions.

## Lokal prüfen

Python 3.12 oder neuer; keine externen Pakete erforderlich:

```bash
python3 -m unittest discover -s tests -v
```

Es werden keine Zugangsschlüssel benötigt und keine Netzwerkabfragen ausgelöst.
Die Tests prüfen insbesondere FDV-Verwechslung, die harte USD-1-Mio.-Grenze,
Tokenalter, Contract-Mehrdeutigkeit, unbekannte Sicherheitsdaten, konzentrierten
Besitz, doppelte Trades, kopierte Werbung, gemessenen Interaktionszuwachs,
Gratisbudget, Preisänderungen und das Schweizer Tageslimit.

Die zusätzlichen Schwellenwerte in `config.json` sind konservative
Startannahmen. Sie wurden nicht auf Profitabilität kalibriert. Änderungen sollten
nach beobachteten Daten erfolgen, ohne die fachlichen Pflichtfilter aufzuweichen.

## Geprüfter Entwicklungsstand

* Lokale Filter- und Budgettests ausgeführt.
* Öffentliche DexScreener-, GoPlus- und GeckoTerminal-Endpunkte sowie das aktuelle
  öffentliche Actor-Preismodell abgefragt.
* Quellcode und Workflows im GitHub-Repository bereitgestellt. Alle 31 Tests
  bestehen auch auf dem GitHub-Runner. Der zusätzliche Workflow `Einrichtung
  prüfen` meldet ausschliesslich, ob die benötigten Secrets gesetzt sind.
* Zugangsschlüssel bleiben in geschützten GitHub-Secrets. Telegram-Kopplung,
  authentifizierter X-Abruf und Fomo-Nettoerfolg sind noch nicht live abgenommen.
  Die Überwachung wurde nicht aktiviert.
* Live-Abnahme von X und Telegram sowie Prüfung des realen Gratisverbrauchs sind
  die nächsten Schritte. Der Bot ist bis dahin nicht betriebsbereit.

Quellen und Schnittstellen: [SOURCES.md](SOURCES.md).
