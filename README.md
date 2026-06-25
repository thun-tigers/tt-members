# tt-members

Mitgliederprofile fuer die Tigers-Anwendungen.
Authentifizierung, Rollen und Service-Launch laufen zentral ueber tt-auth.

## Versionierung

- verbindliche Service-Version steht in `VERSION`
- Release-Tags folgen `vMAJOR.MINOR.PATCH`
- `main` publisht Beta-Images nach GHCR mit Tag `beta`
- Produktion deployt feste Release-Tags wie `v0.1.0`

## Aktueller Stand

- SSO-Login ueber tt-auth ist aktiv
- Profile werden in tt-members gepflegt
- Team-Mitgliedschaften und Rollen werden servicebezogen angezeigt
- Theme-Sync ueber Subdomains ist aktiv (globaler Cookie)

## Beta-Umgebung

- Mitglieder-App: https://members-beta.thun-tigers.net
- Auth-Service: https://auth-beta.thun-tigers.net
- Agenda-Service: https://agenda-beta.thun-tigers.net
- Analytics-Service: https://analytics-beta.thun-tigers.net

## Lokal starten

Voraussetzungen:

- Docker mit Docker Compose
- laufendes tt-auth auf http://localhost:8085 (oder gemeinsamer Start ueber tt-infra)

```bash
cp .env.example .env
docker compose --env-file .env up -d --build
```

Die Anwendung ist danach unter `http://localhost:8088` erreichbar.

SSO_SHARED_SECRET und INTERNAL_API_SECRET muessen mit den Werten in tt-auth uebereinstimmen.

Empfohlener Weg fuer den Gesamtstack:

```bash
cd ../tt-infra
cp .env.example .env
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build
```

## Funktionen

- SSO-Anmeldung ueber tt-auth
- Erfassung und Bearbeitung des Mitgliederprofils
- Rueckmeldung des abgeschlossenen Profils an tt-auth
- Anzeige freigegebener und beantragter Team-Mitgliedschaften

## Health Check

```bash
curl http://localhost:8088/health
```
