# Sources

| Source | Endpoint | Accès | Granularité | Statut |
|---|---|---|---|---|
| EEX | `api.eex-group.com/pub/market-data/table-data` | API directe publique, UA configurable | settlement quotidien | implémenté |
| EEX ticker | `.../price-ticker` | API directe | snapshot | implémenté |
| Nord Pool | `dataportal-api.../IntradayMarketStatistics` | API directe | PH/HH/QH | implémenté |
| EPEX SPOT | page publique `market-results` + AJAX Drupal dynamique | session publique | 15 min + indices | implémenté |
| RTE équilibrage | `balancing_volumes_prices/volumes/table` | API publique sans cookie | 15 min | implémenté |
| ENTSO-E | Transparency API | token `ENTSOE_API_TOKEN` | variable | planifié |
| Energy-Charts | API officielle | ouvert selon licence | horaire/15 min | planifié |
| RTE | Open Data | fichiers/API officiels | 15/30 min | planifié |
| ENTSOG | Transparency/Power BI | à qualifier | quotidien | non implémenté |
| Electricity Maps | application/API sous licence | ne pas contourner | variable | discovery seulement |

Les champs directionnels RTE (`rise`/`drop`) sont dépliés en lignes et
`source_field` conserve exactement la clé publiée : `fcr`, `afrr`, `mfrr`, `rr`,
`rr_standard`, `activation_non_pc`, `deltap`, `igcc`,
`countertrading_xb_redispatching`, `tso_mutual_emergency`, `xb_balancing`,
`volume_mfrr_SA` ou `volume_mfrr_DA`. L'endpoint observé est une table de volumes,
stockés en MWh; les JSON `null` restent SQL NULL.

EPEX est interrogé après lecture dynamique du formulaire : aucun cookie ou
`form_build_id` n'est codé en dur. Les profondeurs historiques sont constatées
durant le backfill, jamais supposées. Toute redistribution doit respecter les
licences EPEX SPOT et RTE applicables.
