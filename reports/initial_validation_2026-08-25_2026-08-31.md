# Validation initiale — 25/08/2026 au 31/08/2026

Exécution du 01/09/2026. Ce rapport d'amorçage ne prétend pas couvrir les sources encore non implémentées et n'assimile jamais « non testé » à zéro.

| Dataset | Expected | Received | Missing | Duplicates | Coverage | Status |
|---|---:|---:|---:|---:|---:|---|
| EEX FR Base CAL-2027 | 5 jours de publication observés | 5 | 0 observé | 0 | 100% de la réponse API | PASS |
| Nord Pool intraday FR (31/08 seulement) | source-dependent contracts | 159 | non calculé | 0 | réponse valide | PASS |
| EEX FR Peak CAL+1/+2/+3 | non exécuté avant choix wizard | — | — | — | — | PENDING |
| EEX TTF CAL+1/+2/+3 | non exécuté avant choix wizard | — | — | — | — | PENDING |
| Nord Pool BE / DE-LU | non exécuté avant choix wizard | — | — | — | — | PENDING |
| France load/production/flows/DA | collecteur source à intégrer | — | — | — | — | NOT_IMPLEMENTED |
| Gas storage EU/FR/DE/IT/NL | collecteur source à intégrer | — | — | — | — | NOT_IMPLEMENTED |

Notes : l'API EEX renvoie les dates dans l'ordre décroissant ; le parseur vérifie la monotonie et l'unicité puis normalise l'ordre. Nord Pool a annoncé les unités dans le payload archivé ; les NULL ont été conservés. Les payloads bruts et métadonnées SHA-256 se trouvent sous `data/raw/` (ignorés par Git).
