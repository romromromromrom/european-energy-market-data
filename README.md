# European Energy Market Data

Collecteur local de données des marchés européens de l'énergie, conçu pour être
reprenable et auditable. Les observations normalisées sont stockées dans SQLite ;
chaque collecte conserve également le payload source compressé et les informations
d'exécution nécessaires pour retracer son origine.

Quatre sources sont réellement collectées :

- **EEX** : prix de règlement quotidiens et snapshots `price-ticker` des futures
  France Base, France Peak et TTF, dont les produits France Month/Quarter ;
- **EPEX SPOT** : enchère France Day-Ahead SDAC en pas de 15 minutes ;
- **RTE** : volumes d'équilibrage français en pas de 15 minutes ;
- **Nord Pool** : statistiques intraday PH, HH et QH pour FR, BE et DE-LU.

Le prix spot France de référence est le Baseload EPEX Day-Ahead publié. Le VWAP
Nord Pool reste explicitement une statistique intraday.

```bash
energy-scraper collect eex --codes F7BM,F7BQ,F7PM,F7PQ --maturities 202610,202701
energy-scraper collect epex --date 2026-09-08
energy-scraper collect rte-balancing --date 2026-09-07
energy-scraper collect rte-prices --date 2026-09-07
```

Les autres sources du catalogue sont des pistes d'intégration. Le collecteur ne
fabrique aucune donnée manquante et ne contourne ni authentification, ni paywall,
ni restriction de licence.

## Fonctionnalités

- ingestion append-only dans SQLite, avec mode WAL ;
- archivage des réponses brutes dans `data/raw/` ;
- reprise d'un backfill par partition et relecture explicite avec `--force` ;
- conservation des valeurs absentes sous forme de `NULL` ;
- grille d'observations attendues tenant compte des journées DST de 92, 96 ou
  100 quarts d'heure ;
- inventaire persistant des gaps et import CSV manuel traçable ;
- tableau de bord local et API FastAPI bornée, sans SQL arbitraire ;
- capture réseau Playwright pour examiner les endpoints publics.

## Prérequis et installation

- Python 3.12 ou plus récent ;
- un accès réseau aux sources choisies ;
- Chromium uniquement pour la découverte réseau.

Depuis la racine du dépôt :

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[test,browser,parquet]'
.venv/bin/playwright install chromium
.venv/bin/python -m energy_scraper init-db
```

Pour une installation minimale, `pip install -e .` suffit. Les extras disponibles
sont `test`, `browser` et `parquet`. L'export Parquet est prévu, mais pas encore
exposé par la CLI.

## Démarrage rapide

Une collecte ciblée peut être lancée sans préparer de plan de backfill :

```bash
# Futures EEX : la période porte sur les dates de cotation.
.venv/bin/python -m energy_scraper collect eex \
  --start 2026-08-25 --end 2026-08-31 \
  --years 2027,2028,2029 --codes F7BY,F7PY,G3BY --ticker

# Intraday Nord Pool : la période porte sur les dates de livraison.
.venv/bin/python -m energy_scraper collect nordpool \
  --start 2026-08-25 --end 2026-08-31 \
  --areas FR,BE,DE-LU

.venv/bin/python -m energy_scraper status
.venv/bin/python -m energy_scraper validate
```

Les codes EEX configurés par défaut sont `F7BY` (France Base Year), `F7PY`
(France Peak Year) et `G3BY` (TTF Year). Sans dates, EEX collecte les sept derniers
jours et Nord Pool la journée courante. `collect all --date YYYY-MM-DD` lance les
deux collecteurs avec leurs périmètres par défaut.

Une partition terminée avec le statut `SUCCESS` ou `SUCCESS_EMPTY` n'est pas
appelée de nouveau. L'option `--force` doit rester réservée à une nouvelle
observation volontaire.

## Backfill reprenable

Le wizard construit `config/backfill_plan.yaml`. Pour chaque jeu de données, il
accepte une date ISO, `max` pour sonder la profondeur disponible, ou `skip` :

```bash
.venv/bin/python -m energy_scraper init-backfill
.venv/bin/python -m energy_scraper run-backfill
```

Pour générer puis valider le plan par défaut sans interaction :

```bash
.venv/bin/python -m energy_scraper init-backfill --non-interactive
```

Le runner exécute uniquement les collecteurs implémentés. Les bornes historiques
constatées, les erreurs d'accès et l'avancement restent enregistrés afin qu'une
exécution ultérieure puisse reprendre le travail.

## API et tableau de bord locaux

```bash
export ENERGY_API_TOKEN='une-valeur-longue-et-aleatoire'
.venv/bin/python -m energy_scraper api serve --host 127.0.0.1 --port 8765
```

Ouvrir ensuite <http://127.0.0.1:8765/>. La documentation interactive de FastAPI
est disponible sur <http://127.0.0.1:8765/docs>.

Exemples de requêtes :

```bash
curl -H "Authorization: Bearer $ENERGY_API_TOKEN" \
  http://127.0.0.1:8765/health

curl -G -H "Authorization: Bearer $ENERGY_API_TOKEN" \
  --data-urlencode 'area=FR' \
  --data-urlencode 'start=2026-08-25' \
  --data-urlencode 'end=2026-08-31' \
  http://127.0.0.1:8765/v1/intraday/contracts
```

Principaux endpoints : `/v1/catalog`, `/v1/coverage`, `/v1/latest`, `/v1/series`,
`/v1/futures`, `/v1/intraday/contracts`, `/v1/gaps` et `/v1/export`. Les listes
sont paginées par curseur et limitées à 5 000 lignes par requête dans la
configuration fournie.

Le tableau de bord peut démarrer un backfill en arrière-plan, uniquement depuis
un client local. Sans `ENERGY_API_TOKEN`, l'API reste utilisable sans en-tête mais
la CLI refuse tout bind non local. Pour un accès distant, utiliser un tunnel HTTPS
authentifié et un export borné ; ne jamais exposer directement le fichier SQLite.

### App privée pour une tâche ChatGPT

La solution la plus simple ne publie aucune API : un export CSV quotidien peut
être synchronisé vers un fichier privé Google Drive, puis lu par une tâche
ChatGPT via l'app Google Drive. Suivre le guide
[ChatGPT depuis Google Drive](docs/CHATGPT_DRIVE.md).

```bash
.venv/bin/python -m energy_scraper export-brief \
  --output reports/morning_brief.csv
```

Le CSV regroupe l'état de fraîcheur, les futures, l'intraday et les gaps sur une
fenêtre glissante de sept jours. Sa génération est atomique et la fenêtre peut
être ajustée entre 1 et 90 jours avec `--days`.

Pour un accès interactif aux données sans fichier intermédiaire, le dépôt contient
également une passerelle MCP Cloudflare read-only donnant à ChatGPT un accès
authentifié aux données fraîches sans exposer SQLite. Les routes
`/v1/brief/*` sont limitées à 90 jours et la commande suivante actualise les deux
sources pour le brief du matin :

```bash
.venv/bin/python -m energy_scraper collect daily
```

Le déploiement nécessite un domaine Cloudflare, un tunnel vers l'API locale et
Managed OAuth devant le Worker. Suivre le guide [ChatGPT MCP privé](docs/CHATGPT_MCP.md).

## Exports et gaps

Exporter une plage au format CSV :

```bash
.venv/bin/python -m energy_scraper export \
  futures 2026-08-25 2026-08-31 --output reports/futures.csv
```

Les datasets acceptés sont `futures` et `intraday`.

Pour préparer un remplissage manuel :

```bash
.venv/bin/python -m energy_scraper gaps export-template
.venv/bin/python -m energy_scraper import-gap-fill reports/gap_fill_template.csv --dry-run
.venv/bin/python -m energy_scraper import-gap-fill reports/gap_fill_template.csv --commit
```

Chaque ligne doit correspondre à un gap unique. Le fichier importé, son SHA-256,
sa provenance et sa date d'import sont conservés. Une observation provenant déjà
d'une source n'est jamais remplacée.

## Configuration

La configuration des sources se trouve dans `config/sources.yaml` et le plan dans
`config/backfill_plan.yaml`. Ces variables d'environnement permettent de déplacer
les données ou d'ajuster les requêtes :

| Variable | Valeur par défaut | Rôle |
|---|---|---|
| `ENERGY_SCRAPER_ROOT` | racine du dépôt | racine de configuration et de sortie |
| `ENERGY_DB_PATH` | `data/power_europe_market_history.sqlite` | base SQLite |
| `ENERGY_RAW_DIR` | `data/raw/` | archives brutes |
| `ENERGY_PARQUET_DIR` | `data/parquet/` | future sortie analytique |
| `ENERGY_HTTP_TIMEOUT` | `30` | timeout HTTP en secondes |
| `ENERGY_MAX_ATTEMPTS` | `3` | nombre maximal de tentatives |
| `ENERGY_USER_AGENT` | identifiant du projet | User-Agent HTTP |
| `ENERGY_API_TOKEN` | non défini | bearer token de l'API locale |

## Vérification et développement

```bash
.venv/bin/pytest
.venv/bin/python -m energy_scraper --help
```

Les tests couvrent notamment les transitions DST, les parseurs EEX et Nord Pool,
la conservation des `NULL`, l'intégrité de la base et le filtrage de l'API.

## Organisation du dépôt

```text
config/                  catalogue des sources et plan de backfill
data/                    SQLite, archives brutes et futures sorties Parquet
docs/                    architecture, modèle de données, sources et notes légales
reports/                 rapports de validation et modèles d'import
src/energy_scraper/      CLI, collecteurs, cœur, API et interface web
tests/                   tests automatisés
```

Pour aller plus loin, consulter [l'architecture](docs/ARCHITECTURE.md), le
[modèle de données](docs/DATA_MODEL.md), le [catalogue des sources](docs/SOURCES.md)
et les [notes légales et de licence](docs/LEGAL_AND_LICENSE_NOTES.md).

## Limites actuelles

- ENTSO-E, Energy-Charts, RTE, ENTSOG, EPEX et Electricity Maps ne disposent pas
  encore de collecteur actif ;
- la profondeur historique dépend de ce que chaque source expose au moment du run ;
- les droits de redistribution ne sont pas accordés par ce logiciel et doivent
  être vérifiés auprès de chaque fournisseur ;
- SQLite est la source transactionnelle ; l'export Parquet reste à implémenter.
