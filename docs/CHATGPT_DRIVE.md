# Brief ChatGPT privé depuis Google Drive

Cette variante ne publie aucune API. La machine collecte les données, construit
un CSV atomique de sept jours, puis remplace un fichier privé à nom fixe dans
Google Drive. La tâche ChatGPT lit ce fichier via l'app Google Drive connectée.

## 1. Installer et configurer rclone

Installer `rclone` avec le gestionnaire de paquets du système, puis lancer :

```bash
rclone config
```

Créer un remote Google Drive nommé `gdrive` et terminer l'autorisation OAuth dans
le navigateur. Aucun fichier ne doit être partagé publiquement. Vérifier l'accès :

```bash
rclone lsd gdrive:
rclone mkdir "gdrive:Energy Market Morning Brief"
```

`rclone` utilise l'API Google Drive en interne, mais cette solution ne demande ni
API applicative à développer, ni serveur web, ni tunnel entrant.

## 2. Préparer l'environnement privé

Copier `deploy/environment.example` vers
`~/.config/energy-scraper/environment`, remplacer `/home/replace-me` par le vrai
répertoire personnel et conserver :

```text
ENERGY_BRIEF_OUTPUT=/home/USER/Projets/european-energy-market-data/reports/morning_brief.csv
ENERGY_DRIVE_DESTINATION=gdrive:Energy Market Morning Brief/morning_brief.csv
```

Le CSV local est ignoré par Git. Il contient un enregistrement d'état général,
un état par source, les règlements EEX, les derniers snapshots intraday Nord Pool
de la fenêtre et les gaps ouverts. Le nom distant reste stable ; `rclone copyto`
met à jour le fichier existant au lieu de créer un fichier daté chaque jour.

Tester manuellement sans collecter à nouveau :

```bash
.venv/bin/python -m energy_scraper validate
.venv/bin/python -m energy_scraper export-brief \
  --output reports/morning_brief.csv
rclone copyto reports/morning_brief.csv \
  "gdrive:Energy Market Morning Brief/morning_brief.csv"
```

## 3. Activer la publication quotidienne

Installer les unités utilisateur :

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/*.service deploy/systemd/*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now energy-collect.timer energy-drive-export.timer
```

La collecte démarre à 07:15 Europe/Paris. L'export et l'upload démarrent à 07:30.
Le service valide d'abord SQLite : en cas d'échec de validation, l'ancien fichier
Drive est conservé. Une collecte source partielle produit en revanche un fichier
marqué `partial` ou `stale`, afin que ChatGPT signale le problème.

Vérifier l'automatisation :

```bash
systemctl --user status energy-collect.timer energy-drive-export.timer
systemctl --user start energy-drive-export.service
journalctl --user -u energy-drive-export.service --since today
rclone ls "gdrive:Energy Market Morning Brief/morning_brief.csv"
```

## 4. Configurer la tâche ChatGPT

Connecter Google Drive dans les réglages Apps de ChatGPT. Créer d'abord une tâche
ponctuelle cinq minutes plus tard avec le contenu de
`deploy/chatgpt-drive-task-prompt.md`. Vérifier qu'elle ouvre le CSV, cite son
`generated_at` et restitue les dernières dates EEX et Nord Pool.

Si ce test réussit, créer la tâche récurrente à 08:00 Europe/Paris. Si ChatGPT
trouve le fichier mais ne peut pas lire son contenu, vérifier les permissions du
compte Google connecté et tester le CSV dans une conversation normale avant de
réessayer la tâche.
