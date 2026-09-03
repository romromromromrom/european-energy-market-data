# Sources

| Source | Endpoint | Accès | Granularité | Statut |
|---|---|---|---|---|
| EEX | `api.eex-group.com/pub/market-data/table-data` | API directe publique, UA configurable | settlement quotidien | implémenté |
| EEX ticker | `.../price-ticker` | API directe | snapshot | implémenté |
| Nord Pool | `dataportal-api.../IntradayMarketStatistics` | API directe | PH/HH/QH | implémenté |
| ENTSO-E | Transparency API | token `ENTSOE_API_TOKEN` | variable | planifié |
| Energy-Charts | API officielle | ouvert selon licence | horaire/15 min | planifié |
| RTE | Open Data | fichiers/API officiels | 15/30 min | planifié |
| ENTSOG | Transparency/Power BI | à qualifier | quotidien | non implémenté |
| Electricity Maps | application/API sous licence | ne pas contourner | variable | discovery seulement |

Les profondeurs historiques sont constatées durant le backfill, jamais supposées. Toute redistribution doit respecter les licences applicables.
