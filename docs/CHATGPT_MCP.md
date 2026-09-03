# Accès privé depuis une tâche ChatGPT

Cette intégration conserve SQLite sur la machine locale. ChatGPT accède uniquement
à quatre outils de lecture via un Worker MCP protégé par Cloudflare Access Managed
OAuth. Le Worker appelle l'origine FastAPI à travers un Cloudflare Tunnel et un
service token distinct.

## 1. Préparer l'API locale

Créer un token différent de toutes les clés Cloudflare :

```bash
mkdir -p ~/.config/energy-scraper
cp deploy/environment.example ~/.config/energy-scraper/environment
chmod 600 ~/.config/energy-scraper/environment
openssl rand -hex 32
```

Reporter la valeur générée dans `ENERGY_API_TOKEN`. Ne jamais ajouter ce fichier
au dépôt. Installer ensuite les unités utilisateur :

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/*.service deploy/systemd/*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now energy-api.service energy-collect.timer
```

Autoriser le maintien des services après déconnexion si nécessaire :

```bash
loginctl enable-linger "$USER"
```

## 2. Créer le tunnel d'origine

Installer `cloudflared`, puis :

```bash
cloudflared tunnel login
cloudflared tunnel create european-energy-origin
cloudflared tunnel route dns european-energy-origin energy-origin.example.com
mkdir -p ~/.config/cloudflared
cp deploy/cloudflared-energy.example.yml ~/.config/cloudflared/energy.yml
```

Remplacer le domaine, l'UUID et le chemin du credential dans `energy.yml`. Dans
Cloudflare Zero Trust, créer une application Access pour
`energy-origin.<domaine>`, refuser l'accès utilisateur direct et autoriser un
service token réservé au Worker. Activer ensuite le tunnel :

```bash
systemctl --user enable --now cloudflared-energy.service
```

Un appel direct sans credentials Access doit être refusé. Le serveur d'origine
reste lié à `127.0.0.1:8765` et exige aussi son bearer token applicatif.

## 3. Déployer le Worker MCP

Le Worker est stateless. Cloudflare Access Managed OAuth protège son domaine ;
il ne faut pas activer une route `workers.dev` parallèle.

```bash
cd mcp-worker
npm install
npx wrangler login
npx wrangler secret put ORIGIN_API_TOKEN
npx wrangler secret put CF_ACCESS_CLIENT_ID
npx wrangler secret put CF_ACCESS_CLIENT_SECRET
```

Dans `wrangler.jsonc` :

- remplacer `ORIGIN_BASE_URL` par `https://energy-origin.<domaine>` ;
- ajouter la route custom domain `energy-mcp.<domaine>` ;
- conserver `workers_dev: false`.

Déployer :

```bash
npm run type-check
npm run deploy
```

`npm run type-check` régénère localement les types Cloudflare avant la compilation ;
le fichier généré `worker-configuration.d.ts` n'est pas versionné.

Dans Cloudflare Access, protéger `energy-mcp.<domaine>` avec **Managed OAuth**,
scope de lecture uniquement et une politique autorisant seulement l'adresse e-mail
du propriétaire. L'URL MCP à fournir aux clients est :

```text
https://energy-mcp.<domaine>/mcp
```

Le Worker transmet au tunnel le service token Cloudflare et à FastAPI le token
`ENERGY_API_TOKEN`. Les trois secrets doivent être tournés indépendamment.

## 4. Vérifier puis connecter ChatGPT

Tester d'abord avec MCP Inspector :

```bash
npx @modelcontextprotocol/inspector@latest
```

Après le flux OAuth, vérifier que seuls ces outils sont visibles :

- `get_data_status` ;
- `get_futures_settlements` ;
- `get_intraday_contracts` ;
- `get_data_gaps`.

Dans ChatGPT, ajouter une app MCP personnalisée pointant vers l'URL `/mcp`, puis
terminer l'autorisation Cloudflare. Créer une tâche ponctuelle cinq minutes plus
tard qui appelle `get_data_status` et restitue `generated_at` ainsi que la dernière
date EEX. Ce test est obligatoire : la disponibilité des apps dans les tâches
planifiées dépend du compte et des réglages ChatGPT.

Si le test réussit, créer la tâche récurrente de 08:00 Europe/Paris avec le contenu
de `deploy/chatgpt-task-prompt.md`. S'il échoue parce que l'app n'est pas proposée
aux tâches, l'API ne peut pas lever cette limitation produit ; ne pas rendre les
données anonymes pour la contourner.

## 5. Exploitation

```bash
systemctl --user status energy-api.service cloudflared-energy.service energy-collect.timer
journalctl --user -u energy-collect.service --since today
curl -H "Authorization: Bearer $ENERGY_API_TOKEN" http://127.0.0.1:8765/v1/brief/status
```

Une collecte partielle reste visible dans `status`. Le brief doit considérer une
source comme périmée au-delà de 26 heures. Les logs de l'API contiennent un ID de
requête, mais aucun bearer token.
