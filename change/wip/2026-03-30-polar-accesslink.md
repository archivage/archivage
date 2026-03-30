---
started: 2026-03-30
---

# archivage polar — Polar AccessLink integration

Ajouter `archivage polar` pour syncher les exercices et HR seconde par seconde
depuis le Polar H10 via l'API AccessLink v3.

## Context

Le Polar H10 (ceinture cardio ECG, <1% erreur) est bien plus precis que la
Steel HR (optique, 5-11% erreur a l'effort). L'objectif est d'avoir les donnees
HR Polar dans la meme DB archivage pour que workout_calories.py puisse les
utiliser a la place des donnees Withings.

## Approach

Suivre exactement le pattern Withings: `polar.py` (API), `polar_db.py` (SQLite),
commandes CLI `polar setup/auth/fetch/status`.

### API AccessLink v3

- **Auth**: OAuth 2.0 code flow, redirect `http://localhost:8585/callback`
  - Auth URL: `https://flow.polar.com/oauth2/authorization`
  - Token URL: `https://polarremote.com/v2/oauth2/token`
  - Token auth: HTTP Basic (`base64(client_id:client_secret)`)
  - v3 tokens ne expirent pas (pas de refresh necessaire)
  - Apres auth: `POST /v3/users` pour enregistrer l'utilisateur
- **Exercises**: `GET /v3/exercises` (liste), `GET /v3/exercises/{id}` (summary)
- **HR samples**: `GET /v3/exercises/{id}/samples/0` (1 Hz, comma-separated)
- **Limite**: 30 jours d'historique exercices. Sync quotidien obligatoire.
- **Rate**: 520 req/15min pour 1 user. Largement suffisant.

### DB Schema

`~/Archive/polar/polar.sqlite`:

```sql
CREATE TABLE exercises (
    id        INTEGER PRIMARY KEY,
    start     TEXT NOT NULL,        -- UTC ISO
    duration  INTEGER,              -- seconds
    sport     TEXT,
    calories  REAL,
    distance  REAL,
    hr_avg    INTEGER,
    hr_max    INTEGER,
    device    TEXT,
    raw       TEXT                  -- full JSON
);

CREATE TABLE hr_samples (
    exercise_id INTEGER NOT NULL,
    offset_sec  INTEGER NOT NULL,   -- seconds since exercise start
    heart_rate  INTEGER NOT NULL,
    PRIMARY KEY (exercise_id, offset_sec)
);
CREATE INDEX idx_hr_exercise ON hr_samples(exercise_id);
```

### Files

| File | Purpose |
|------|---------|
| `src/archivage/polar.py` | OAuth flow, API client |
| `src/archivage/polar_db.py` | SQLite schema, insert/query |
| `src/archivage/cli.py` | `polar` group: setup, auth, fetch, status |
| `src/archivage/config.py` | Add `[polar]` section, token path |

### CLI

```
archivage polar setup          # Store client_id + client_secret
archivage polar auth           # OAuth browser flow + register user
archivage polar fetch          # Sync exercises + HR samples
archivage polar status         # Show recent exercises
```

## Tasks

- [ ] Register app on admin.polaraccesslink.com
- [ ] Add `[polar]` config to config.py
- [ ] Implement polar.py (credentials, OAuth, API endpoints)
- [ ] Implement polar_db.py (schema, inserts, queries)
- [ ] Add CLI commands to cli.py
- [ ] Test fetch with today's run
- [ ] Compare HR Polar vs Withings pour la course d'aujourd'hui

## Open Questions

- Faut-il aussi fetcher le continuous HR (5-min intervals) ou seulement exercises?
  Probablement exercises only pour commencer.
- Adapter workout_calories.py pour utiliser Polar HR quand disponible?
  Oui, mais dans un second temps.
