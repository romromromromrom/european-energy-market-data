# Brief quotidien des marchés européens de l'énergie depuis Google Drive

À chaque exécution à 08:00, heure de Paris :

1. Utilise Google Drive et ouvre le fichier exact
   `Energy Market Morning Brief/morning_brief.csv`.
2. Lis d'abord les lignes `record_type=status` et `record_type=source_status`.
3. Si le fichier est absent, si `generated_at` a plus de 26 heures, si
   `overall_status=stale`, ou si les deux sources sont périmées, indique clairement
   que le brief ne dispose pas de données fraîches et n'invente aucune valeur.
4. Si `overall_status=partial`, précise quelle source est en retard avant de
   continuer avec les données disponibles.
5. Analyse les lignes `record_type=futures` et `record_type=intraday`.
6. Pour chaque chiffre cité, donne la date, le marché, la zone, le produit ou
   contrat, la maturité le cas échéant, la valeur, l'unité et la source.
7. Distingue explicitement les valeurs publiées des variations, moyennes ou
   commentaires que tu calcules.
8. Consulte les lignes `record_type=gap` et signale les gaps ouverts pertinents.

Reste concis, utilise des tableaux pour les prix principaux et termine par les
risques de qualité ou de fraîcheur des données.
