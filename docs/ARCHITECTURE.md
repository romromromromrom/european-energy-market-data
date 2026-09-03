# Architecture

`collectors` privilégie les API structurées et appelle la politique de retry centrale. Chaque requête crée un `scrape_run`, archive le payload JSON compressé et ingère dans une transaction SQLite. `collection_partitions` et `backfill_jobs` assurent la reprise best-effort. SQLite utilise WAL afin que FastAPI puisse lire pendant l'ingestion.

La couche `expected_observations` est indépendante des données reçues. Les grilles locales sont construites en UTC entre deux minuits `Europe/Paris`, ce qui produit naturellement 92/96/100 quarts d'heure aux changements DST. Les gaps sont historisés, jamais supprimés.

Parquet est prévu comme export analytique partitionné ; SQLite demeure la source transactionnelle. L'API ne contient ni SQL arbitraire, ni écriture, ni navigation du système de fichiers.
