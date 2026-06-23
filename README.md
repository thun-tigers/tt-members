# tt-members

Mitgliederprofile fuer die Tigers-Anwendungen. Die Anmeldung und Berechtigungen
werden zentral von `tt-auth` bereitgestellt.

## Lokal starten

Voraussetzungen:

- Docker mit Docker Compose
- laufendes `tt-auth` auf `http://localhost:8085`

```bash
cp .env.example .env
docker compose --env-file .env up -d --build
```

Die Anwendung ist danach unter `http://localhost:8088` erreichbar.

`SSO_SHARED_SECRET` und `INTERNAL_API_SECRET` muessen mit den entsprechenden
Werten in `tt-auth` uebereinstimmen.

## Funktionen

- SSO-Anmeldung ueber `tt-auth`
- Erfassung und Bearbeitung des Mitgliederprofils
- Rueckmeldung des abgeschlossenen Profils an `tt-auth`
- Anzeige freigegebener und beantragter Team-Mitgliedschaften

## Health Check

```bash
curl http://localhost:8088/health
```
