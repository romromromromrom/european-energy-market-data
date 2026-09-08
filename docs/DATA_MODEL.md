# Modèle de données

Les tables de catalogue et audit sont `sources`, `scrape_runs`, `backfill_jobs`, `collection_partitions`, `expected_observations` et `data_gaps`. Les données normalisées vivent dans `instruments`, `market_prices`, `ticker_snapshots`, `intraday_contract_stats`, `epex_day_ahead_prices`, `epex_day_ahead_indices` et `rte_balancing_volumes`.

Les périodes EPEX sont des intervalles locaux timezone-aware, atomiques par
jour, avec une unicité source + zone + livraison + début de période. Les indices
gardent Baseload et Peakload publiés ainsi que Baseload reconstruit, min, max,
amplitude, TB2 et TB4 non pondérés. RTE utilise un modèle long par période,
direction, catégorie et champ source exact.

`rte_balancing_prices` utilise le même découpage temporel mais sépare
`price_type`, `reserve_type` et `direction`. Les prix de réserve et les prix des
écarts positifs/négatifs sont ainsi interrogeables sans ambiguïté.

La clé EEX est instrument + trading date + type de prix + source. Les tickers sont séparés des settlements. La clé Nord Pool est source + zone + contract ID + `source_update_time`, ce qui préserve les snapshots. Les champs absents restent NULL. Les volumes Nord Pool gardent l'unité annoncée et ne sont pas convertis en énergie.
