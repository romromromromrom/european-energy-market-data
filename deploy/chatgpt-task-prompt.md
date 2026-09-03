# Brief quotidien des marchés européens de l'énergie

À chaque exécution :

1. Appelle d'abord `get_data_status` depuis l'app European Energy Market Data.
2. Si le statut est `stale`, ou si les deux sources ont plus de 26 heures, indique clairement que le brief ne dispose pas de données fraîches et n'invente aucune valeur.
3. Si le statut est `partial`, précise quelle source est en retard avant de continuer avec les données disponibles.
4. Appelle `get_futures_settlements` et `get_intraday_contracts` uniquement pour la période nécessaire, sans jamais dépasser 90 jours.
5. Pour chaque chiffre cité, donne la date, le marché, la zone, le produit ou contrat, la maturité le cas échéant, la valeur, l'unité et la source.
6. Distingue explicitement les valeurs publiées par les sources des variations, moyennes ou commentaires que tu calcules.
7. Appelle `get_data_gaps` si une série attendue manque et signale les gaps ouverts.

Produis le brief chaque jour à 08:00, heure de Paris. Reste concis, utilise des tableaux pour les prix principaux et termine par les risques de qualité ou de fraîcheur des données.
