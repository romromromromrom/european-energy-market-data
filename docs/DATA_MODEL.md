# Modèle de données

Les tables de catalogue et audit sont `sources`, `scrape_runs`, `backfill_jobs`, `collection_partitions`, `expected_observations` et `data_gaps`. Les données normalisées vivent dans `instruments`, `market_prices`, `ticker_snapshots` et `intraday_contract_stats`.

La clé EEX est instrument + trading date + type de prix + source. Les tickers sont séparés des settlements. La clé Nord Pool est source + zone + contract ID + `source_update_time`, ce qui préserve les snapshots. Les champs absents restent NULL. Les volumes Nord Pool gardent l'unité annoncée et ne sont pas convertis en énergie.
