# Bericht: Zielbild und GUI-Konzept der Anwendung „GoldenAge“

## 1. Zweck der Anwendung

GoldenAge ist eine vorgangsorientierte Arbeitsanwendung für Personen, die fortlaufend Dokumente, E-Mails und Ereignisse bearbeiten müssen und dabei den Überblick über nächste Schritte und Wiedervorlagen behalten wollen.

Der Kernnutzen der Anwendung besteht darin, jeden neuen Eingang sofort in einen bestehenden Arbeitskontext einzuordnen, daraus eine konkrete nächste Tätigkeit abzuleiten und einen passenden Wiedervorlagezeitpunkt festzulegen. Die Anwendung soll damit verhindern, dass Vorgänge aus dem Blick geraten, unbearbeitet liegen bleiben oder ohne nächsten Termin versanden.

GoldenAge ist damit im Kern kein klassisches Dokumentenarchiv und auch kein gewöhnlicher Aufgabenmanager, sondern ein System zur operativen Führung laufender Vorgänge.

## 2. Fachliche Grundidee

Die Anwendung basiert auf drei zentralen Objekten:

### 2.1 Vorgang

Ein Vorgang ist der eigentliche Arbeitskontext. Er bündelt alle Informationen, Dokumente, Kommunikationsbezüge und Tätigkeiten zu einem Fall, einer Angelegenheit oder einem Geschäftsvorfall.

### 2.2 Tätigkeit / Wiedervorlage

Zu jedem Vorgang können Tätigkeiten mit einem Tätigkeitszeitpunkt angelegt werden. Die wichtigste Form davon ist die Wiedervorlage: ein geplanter Zeitpunkt, zu dem die Anwendung erneut fragt, was als Nächstes zu tun ist.

### 2.3 Eingang / Artefakt

Ein Eingang ist ein File, eine E-Mail, ein Scan oder ein sonstiges Dokument, das einem Vorgang zugeordnet werden soll. Die Anwendung nutzt KI, um diesen Eingang möglichst direkt dem richtigen Vorgang zuzuordnen.

Der erste praktische Mailslice soll bewusst klein sein: Die Anwendung nimmt zunächst manuell exportierte Outlook-`.msg`-Dateien per Drag-and-drop oder Dateiauswahl entgegen. Andere Formate bleiben vorerst sichtbar ausgeschlossen und werden mit einer klaren MVP-Rückmeldung zurückgewiesen.

### 2.4 Unternehmen, Kontakt und Kontaktnotiz

Neben dem Vorgang braucht die Anwendung auf Dauer eigene Arbeitsobjekte für Unternehmen und Kontakte. Aus Maildaten entstehen dabei erste Hinweise auf Ansprechpartner, Domains und Zugehörigkeiten, die später mit ERP-Importen und weiteren Quellen zusammengeführt werden.

Besonders wichtig sind Kontaktnotizen. Dort sollen fachlich relevante Verhaltensmuster festgehalten werden können, zum Beispiel typische Rabatt- oder Bonusforderungen eines Einkaufsansprechpartners. Diese Notizen sind kein Beiwerk, sondern operative Kontextinformation für spätere Bearbeitung und Verhandlung.

## 3. Was die Anwendung tun soll

## 3.1 Standardarbeitsmodus: tägliche Abarbeitung

Die Anwendung soll standardmäßig als tägliche Arbeitsliste genutzt werden. Nach dem Öffnen sieht der Nutzer nur die Tätigkeiten, die heute erledigt werden sollen. Damit wird der Fokus bewusst auf das gelegt, was jetzt bearbeitet werden muss.

Zu jeder Tätigkeit ist mindestens sichtbar:

* zu welchem Vorgang sie gehört,
* was zu tun ist,
* wann sie fällig ist,
* und ob sie überfällig, heute fällig oder noch nicht fällig ist.

Die Anwendung soll den Nutzer konsequent von einer nächsten Handlung zur nächsten führen.

## 3.2 Ereignisgesteuerte Bearbeitung

Sobald ein Ereignis eintritt, insbesondere das Eintreffen eines neuen Dokuments oder einer neuen Nachricht, soll die Anwendung den Nutzer unmittelbar unterstützen.

Im ersten Mailslice bedeutet das konkret: Der Nutzer zieht eine Outlook-`.msg`-Datei in die Anwendung. Eine direkte Mailbox-Anbindung oder automatische Ingestion ist dafür noch nicht erforderlich.

Der beabsichtigte Ablauf ist:

1. Der Nutzer wirft ein File in die Anwendung.
2. Die KI liest Inhalt und Metadaten des Files.
3. Die KI versucht, genau einen passenden Vorgang zu identifizieren.
4. Der Nutzer bestätigt oder verwirft diesen Vorschlag.
5. Nach Zuordnung fragt das System:

   * Was soll jetzt getan werden?
   * Wann soll die nächste Wiedervorlage erfolgen?
6. Aus dieser Entscheidung wird eine neue Tätigkeit am Vorgang erzeugt.

Dadurch wird jeder Eingang sofort in operative Arbeit übersetzt.

## 3.3 KI-gestützte Vorgangserkennung

Die Anwendung soll neue Eingänge nicht nur speichern, sondern fachlich einordnen.

Dafür wertet die KI insbesondere folgende Merkmale aus:

* Betreff,
* Absender,
* Empfänger,
* Unternehmen,
* Domain,
* Dokumentinhalt,
* bekannte Kontaktbeziehungen,
* bereits vorhandene Kommunikationsspuren in bestehenden Vorgängen.

Wichtig ist: Die KI soll zunächst genau einen Vorschlag machen. Das reduziert Komplexität und erzeugt einen schnellen, klaren Bearbeitungsfluss. Erst wenn dieser Vorschlag abgelehnt wird, soll eine erweiterte Suche sichtbar werden.

Für den ersten Outlook-MVP ist die Heuristik bewusst enger: Der Betreff der Mail wird normalisiert und fuzzy gegen bekannte Vorgangstitel gematcht. Absender, Empfänger und Domain werden bereits mitgespeichert, dienen aber zunächst vor allem als Kontext für spätere Such- und Anreicherungslogik.

## 3.4 Agentische Suche nach dem richtigen Vorgang

Wenn der Einervorschlag nicht passt, soll die Anwendung in einen zweiten Modus übergehen: eine intelligente Suche nach dem richtigen Vorgang.

Diese Suche soll nicht nur über einfache Textsuche laufen, sondern über ein KI-Modell mit Tool Use. Das Modell darf strukturierte Suchwerkzeuge aufrufen, um aus einer Beschreibung den wahrscheinlich richtigen Vorgang zu finden.

Beispiel:
„Mail vom x.x.x an Fixture-Kontakt von Vendor Example Inc.“

Daraus soll das Modell gezielt Abfragen auslösen können wie:

* Suche nach Vorgängen mit Beteiligung von Vendor Example Inc.
* Suche nach Vorgängen mit Fixture-Kontakt
* Suche nach früheren Kommunikationsartefakten mit passender Absender- oder Empfängerkonstellation
* Suche nach ähnlichen Betreffmustern oder Dokumentarten

Das Ziel ist nicht freie KI-Assoziation, sondern geführte, nachvollziehbare Identifikation eines Vorgangs.

Später soll ein LLM-Agent diese Informationen über wohldefinierte REST-Endpunkte je Vorgang, Unternehmen und Kontakt weiter anreichern. Er liest dann nicht nur den Betreff, sondern auch den Mailinhalt und kann bestehende Daten gezielt verfeinern, ohne die fachlichen Grenzen des Systems zu umgehen.

## 3.5 Erzwingen eines nächsten Schritts

GoldenAge soll Vorgänge nicht bloß dokumentieren, sondern Arbeitsfortschritt herstellen. Deshalb soll beim Bearbeiten einer fälligen Tätigkeit nicht einfach „erledigt“ geklickt werden können, ohne dass klar ist, wie es weitergeht.

Die Anwendung soll bei jeder relevanten Bearbeitung auf eine Entscheidung hinauslaufen:

* Vorgang abgeschlossen,
* nächste Tätigkeit festlegen,
* Wiedervorlage setzen,
* oder bewusst auf Wiedervorlage verzichten, wenn fachlich sinnvoll.

So entsteht ein System, das auf Kontinuität und Nachverfolgbarkeit ausgelegt ist.

## 4. Wie die GUI aussehen soll

## 4.1 Grundaufbau der Standardansicht

Die Standardansicht soll aus zwei dominanten Bereichen bestehen:

### Linke Hauptfläche: die große Todo-Liste

Hier befindet sich die operative Arbeitsliste des Tages. Sie ist der primäre Arbeitsraum.

Jeder Eintrag zeigt in kompakter Form:

* Titel oder Kurzbezeichnung des Vorgangs,
* kurze Tätigkeitsbeschreibung,
* Tätigkeitszeitpunkt,
* Statusfarbe,
* eventuell beteiligte Firma oder Person,
* optional eine kleine Kennzeichnung für Eingang, Rückfrage, Wiedervorlage oder Eskalation.

Die Liste ist standardmäßig auf „heute“ gefiltert. Überfälliges soll dabei sichtbar priorisiert sein.

### Rechte oder danebenliegende Hauptfläche: großes weißes Einwurffeld

Neben der Aufgabenliste befindet sich ein auffälliges großes weißes Feld mit einer ruhigen Wellenanimation. Dieses Feld ist der visuelle Gegenpol zur Liste und signalisiert: Hier kommen neue Eingänge hinein.

Das Einwurffeld hat drei Funktionen:

* Drag-and-drop-Zone für Dateien,
* visueller Einstiegspunkt für neue Ereignisse,
* Symbol für den Übergang von unstrukturiertem Eingang zu geordnetem Vorgang.

Die Fläche soll schlicht, hochwertig und offen wirken. Sie darf nicht aussehen wie ein technischer Upload-Dialog, sondern eher wie eine ruhige Arbeitsfläche.

## 4.2 Farb- und Statuslogik

Die GUI verwendet eine klare Ampellogik:

* Rot: überfällig
* Gelb: heute fällig oder bald fällig
* Grün: noch nicht fällig

Diese Farben sollen nicht allein stehen, sondern zusätzlich textlich oder ikonisch unterstützt werden, damit der Status immer eindeutig lesbar bleibt. Die Anwendung soll nüchtern und professionell wirken, nicht verspielt.

## 4.3 Verhalten der Liste

Die Liste ist nicht nur eine Anzeige, sondern das zentrale Steuerungselement.

Beim Anklicken eines Listeneintrags soll sich entweder inline oder in einer Detailansicht zeigen:

* vollständiger Vorgangsname,
* letzte Aktivität,
* offene Tätigkeit,
* letzte Dokumente oder Mails,
* Eingabefelder für „Was tun?“ und „Wiedervorlage“.

Der Nutzer soll aus der Liste heraus möglichst ohne Navigationsbrüche arbeiten können.

## 4.4 Verhalten des Einwurffelds

Sobald ein File in das Feld gezogen wird, soll die Oberfläche reagieren:

* leichte visuelle Aktivierung,
* klare Rückmeldung, dass das File erfasst wurde,
* anschließend ein Analysezustand.

Nach kurzer Verarbeitung soll kein abstrakter Technikdialog erscheinen, sondern ein fachlicher Vorschlag wie:

„Vermutlich gehört dieses Dokument zu Vorgang X.“

Darunter erscheinen zwei klare Optionen:

* Vorschlag bestätigen
* Vorschlag ablehnen

Wird bestätigt, geht es sofort weiter zur nächsten Frage:

* Was soll jetzt getan werden?
* Wann ist die Wiedervorlage?

Wird abgelehnt, öffnet sich darunter eine Suchoberfläche.

## 4.5 GUI der erweiterten Suche

Die Suchoberfläche soll erst sichtbar werden, wenn der erste Vorschlag verworfen wurde. Dadurch bleibt die Standardinteraktion schnell und aufgeräumt.

Diese Suchansicht soll enthalten:

* ein freies Eingabefeld für Beschreibung oder Suchbegriff,
* eine Ergebnisliste passender Vorgänge,
* pro Ergebnis eine kurze Begründung, warum dieser Vorgang vorgeschlagen wird,
* eine sichere Auswahlmöglichkeit.

Die Ergebnisse sollen knapp und sachlich dargestellt werden, zum Beispiel:

* Vorgangstitel
* beteiligte Firma
* letzte Aktivität
* Grund für Vorschlag, etwa: „gleiche Domain“, „gleicher Ansprechpartner“, „ähnlicher Betreff“

## 4.6 Detaillierter Eindruck der Oberfläche

Die gesamte GUI soll eher wie ein konzentriertes Fallarbeitswerkzeug wirken als wie ein überladenes CRM.

Sie soll:

* weiß oder sehr hell sein,
* mit wenigen Akzentfarben arbeiten,
* großzügige Flächen und klare Hierarchien haben,
* wenig visuelles Rauschen enthalten,
* stark auf Lesbarkeit und Handlung ausgerichtet sein.

Die Anwendung braucht keine klassische Dashboard-Optik mit vielen Kacheln, Charts oder KPI-Elementen. Der Fokus liegt auf Abarbeitung, Zuordnung und nächstem Schritt.

## 5. Zentrale Nutzerflüsse

## 5.1 Nutzerfluss A: Tagesarbeit

1. Nutzer öffnet GoldenAge.
2. Er sieht nur die heute relevanten Tätigkeiten.
3. Er öffnet den obersten oder wichtigsten Eintrag.
4. Er bearbeitet den Vorgang.
5. Er legt den nächsten Schritt und die nächste Wiedervorlage fest.
6. Der Eintrag verschwindet aus der heutigen Liste, wenn er für heute erledigt ist.

## 5.2 Nutzerfluss B: neues Dokument

1. Nutzer zieht ein Dokument ins Einwurffeld.
2. KI analysiert das Dokument.
3. Ein Vorgang wird vorgeschlagen.
4. Nutzer bestätigt.
5. Anwendung fragt nach Tätigkeit und Wiedervorlage.
6. Dokument ist abgelegt, Vorgang fortgeführt.

## 5.3 Nutzerfluss C: falscher Vorschlag

1. Nutzer lehnt den KI-Vorschlag ab.
2. Erweiterte Suche erscheint.
3. Nutzer beschreibt den Vorgang oder den Kontext.
4. KI-Suche nutzt Tools, um passende Vorgänge zu finden.
5. Nutzer wählt den richtigen Vorgang.
6. Anwendung fragt wieder nach Tätigkeit und Wiedervorlage.

## 6. Sicherheits- und Berechtigungsanforderungen

Die Such- und Zuordnungskomponente greift potenziell auf vertrauliche Kommunikation zu. Deshalb ist Sicherheit keine Nebenbedingung, sondern Kernbestandteil des Konzepts.

## 6.1 Keine freie SQL durch das Modell

Das KI-Modell darf keine beliebigen SQL-Abfragen erzeugen oder direkt gegen die Datenbank arbeiten. Stattdessen soll es nur wohldefinierte Suchwerkzeuge verwenden, die intern abgesichert und parametriert sind.

## 6.2 Rechteäquivalenz zum angemeldeten Nutzer

Die KI darf nur das sehen, was auch der angemeldete Nutzer sehen dürfte. Dafür soll die Suchkomponente mit einem technischen Benutzer arbeiten, dessen effektive Rechte auf die des angemeldeten Nutzers begrenzt sind.

## 6.3 Zugriffskontrolle auf Datenbankebene

Die Datenbank muss Benutzer- und Gruppenzugriffe sauber durchsetzen. Es reicht nicht, Ergebnisse erst nachträglich in der Anwendung herauszufiltern. Unberechtigte Daten dürfen idealerweise gar nicht erst in die Antwort der Suchwerkzeuge gelangen.

## 6.4 Nachgelagerte Prüfung und Maskierung

Wenn zusätzliche Prüfungen über LDAP oder ein vergleichbares Verzeichnis nötig sind, sollen unzulässige Treffer aus der Antwort entfernt werden. Stattdessen kann die Anwendung einen neutralen Hinweis geben, dass Ergebnisse ausgeblendet wurden.

Diese Information darf selbst nicht zu einer verdeckten Auskunft über vertrauliche Vorgänge werden. Deshalb sollen kleine Trefferzahlen nicht präzise ausgegeben werden, sondern begrenzt oder verrauscht dargestellt werden.

## 7. Abgrenzung und Produktcharakter

GoldenAge ist nicht in erster Linie:

* ein vollumfängliches Dokumentenmanagementsystem,
* ein klassisches CRM mit umfangreicher Stammdatenpflege,
* ein reines E-Mail-Archiv,
* oder ein allgemeiner Team-Messenger.

GoldenAge ist vor allem ein operatives Wiedervorlage- und Vorgangsführungssystem mit KI-gestützter Eingangseinordnung.

Sein erster und wichtigster Use Case ist daher nicht „alles verwalten“, sondern:

Ein neuer Eingang kommt hinein, wird richtig einem Vorgang zugeordnet, und die Anwendung zwingt sofort zur Festlegung des nächsten Schritts.

## 8. Zusammenfassung

GoldenAge soll eine ruhige, fokussierte Arbeitsanwendung sein, die täglich genutzt wird, um laufende Vorgänge sicher und ohne Verlust des Bearbeitungskontexts fortzuführen.

Die Anwendung verbindet:

* eine tagesbezogene Todo-Liste,
* eine klare Wiedervorlagenlogik,
* ein zentrales Einwurffeld für neue Dateien,
* eine KI-gestützte Einervorschlags-Zuordnung,
* und eine abgesicherte agentische Suche als Fallback.

Die GUI soll diese Logik direkt sichtbar machen: links das, was heute getan werden muss; daneben das Einwurffeld für neue Ereignisse. Alles in der Oberfläche dient letztlich nur einer Frage:

Was ist der richtige Vorgang, was ist jetzt zu tun, und wann muss ich wieder daran erinnert werden?
