# Tickets et produits E.Leclerc

Les scripts conservent les tickets de l’espace client connecté puis les indexent
dans `~/Archive/leclerc/leclerc.sqlite`, sur le même principe que Lidl et Auchan.
La collecte réutilise le navigateur personnel ouvert ; elle ne lit ni n’exporte
ses cookies ou identifiants. Aucune API payante, aucun OCR.

## Collecter

Installation unique de la dépendance Node, sans télécharger de navigateur :

```bash
npm ci --prefix ~/Code/archivage/scripts/leclerc --ignore-scripts
```

Garder exactement un onglet Chrome sur
<https://www.e.leclerc/espace-client/carte-fidelite/tickets-de-caisse>, connecté
au bon compte, et le port CDP local 9222 disponible. Puis :

```bash
node ~/Code/archivage/scripts/leclerc/collect_leclerc.mjs
python3 ~/Code/archivage/scripts/leclerc/parse_leclerc.py
```

Les deux commandes acceptent `--archive-dir /chemin/leclerc`. Le collecteur accepte
aussi `--cdp-url http://127.0.0.1:9222`. Il ferme les fenêtres de détail ouvertes
pendant la collecte, mais conserve Chrome et l’onglet de départ.

La collecte porte sur **la vue filtrée actuellement affichée**. E.Leclerc limite
la liste à dix tickets : si dix résultats sont présents, le script échoue avant
collecte et demande de réduire la période dans le site. Pour reprendre un
historique, parcourir plusieurs périodes et relancer les deux commandes. Une vue
vide ou une session expirée fait échouer la collecte ; elle ne vide jamais l’index.
Les filtres de dates lus dans le DOM sont enregistrés dans chaque lot. Les bornes
implicites du site ne sont pas déduites lorsque les champs sont vides.

Depuis un agent Codex utilisant son navigateur connecté, le même collecteur peut
recevoir un onglet `cua_repl` sans CDP supplémentaire :

```javascript
var leclercCollector = await import('/home/kenzo/Code/archivage/scripts/leclerc/collect_leclerc.mjs');
var batch = await leclercCollector.collectReceipts(tab, '/tmp/leclerc-capture');
```

`tab` est l’onglet obtenu par l’API navigateur documentée du harnais. Valider les
captures avec le parseur puis les transférer dans l’archive avec les permissions
locales nécessaires.

## Sources et index

```text
~/Archive/leclerc/
├── sources/<ticket_id>-<empreinte>.json  # HTML et texte exacts, URL, date, SHA-256
├── batches/<horodatage>.json            # liste collectée et filtres de dates
├── receipts/                           # PDF facultatifs téléchargés via le site
└── leclerc.sqlite
```

Le code-barres imprimé identifie **le ticket**, pas un produit. Chaque contenu
distinct est conservé sans écraser les captures précédentes. Une seconde collecte
identique réutilise le même fichier. Le HTML reste une source originale, avec ses
liens d’images ; il ne constitue pas un rendu autonome hors ligne. Le PDF généré
par le site contient des images : préférer le DOM pour l’extraction. Le PDF du
panier de 132,21 € du 13 octobre 2026 a aussi été conservé.

L’importeur valide le lot entier avant toute modification des lignes de l’index.
Il vérifie les empreintes disponibles, les montants en centimes, les quantités,
les arrondis des produits au poids et les paiements. Une incohérence provoque un
échec explicite ; les sources et l’index précédent restent disponibles. Les
tickets absents du lot courant restent dans la base. Pour plusieurs versions
d’un même ticket, la capture au `captured_at` le plus récent est indexée.

Tables : `receipts`, `receipt_items`, `products`, `receipt_discounts`, `payments`.
La vue `product_observations` joint les lignes aux dates et magasins. Les montants
canoniques sont des entiers `*_cents` ; les colonnes `total`, `unit_price` et
`price_per_kg` exposent aussi les euros pour les requêtes usuelles. Les poids sont
en kg ; la quantité d’une ligne au poids vaut un article, avec son `weight_kg`.

Les produits sont regroupés par **libellé normalisé**, sans prétendre identifier
une référence commerciale unique. Aucun EAN produit n’apparaît dans les tickets
observés ; `products.ean` reste vide. Les noms peuvent être tronqués. Les marques,
formats ou valeurs nutritionnelles ne sont pas inventés. Les formats inhabituels
(promotions non reconnues, retours, caisse différente) peuvent être refusés par
les contrôles : adapter le parseur à partir de la source conservée.

```sql
SELECT date, store_name, total, item_count FROM receipts ORDER BY date DESC, time DESC;

SELECT date, name, quantity, unit_price, weight_kg, price_per_kg, total
FROM product_observations WHERE name LIKE '%TOMATE%' ORDER BY date;

SELECT product_id, name, COUNT(*) AS observations,
       MIN(unit_price) AS min_euros, MAX(unit_price) AS max_euros
FROM product_observations WHERE unit_price IS NOT NULL GROUP BY product_id;
```

Validation initiale le 13 octobre 2026 : deux tickets CLAMART, 8,99 € et 132,21 €,
29 lignes de produits, 30 articles, 141,20 € au total. Quantités multiples,
0,436 kg de haricots verts et 1,106 kg de tomates reconnus ; sommes concordantes.
Une deuxième collecte conserve deux fichiers source. Les tests synthétiques
couvrent les lots, poids, incohérences, doublons et la conservation des tickets
absents d’un nouveau lot :

```bash
python3 -m unittest discover -s ~/Code/archivage/tests -p test_leclerc.py -v
```
