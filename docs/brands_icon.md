# Come aggiungere l'icona al brands repository di Home Assistant

Per far apparire l'icona nella pagina Integrazioni di HA devi aprire una Pull Request
sul repository ufficiale `home-assistant/brands`.

## Prerequisiti

- Il tuo repository `alarm_guardian` deve essere pubblico su GitHub
- L'icona deve essere PNG, 256×256 pixel, sfondo trasparente preferito

## Passi

1. Fai un fork di https://github.com/home-assistant/brands

2. Nella tua fork, crea la cartella:
   ```
   custom_integrations/alarm_guardian/
   ```

3. Copia `icon.png` (256×256) in quella cartella.
   Se vuoi anche il logo orizzontale, aggiungi `logo.png` (proporzioni libere, lato corto min 256px).

4. Apri una Pull Request verso `home-assistant/brands` con titolo:
   ```
   Add alarm_guardian custom integration icons
   ```

5. Nel corpo della PR descrivi brevemente l'integrazione e includi il link al tuo repo.

## Struttura attesa nel brands repo

```
custom_integrations/
└── alarm_guardian/
    ├── icon.png      (256×256, quadrata)
    └── logo.png      (opzionale, orizzontale)
```

## Dopo l'accettazione

Una volta accettata la PR, l'icona appare automaticamente in HA per tutti
gli utenti che installano l'integrazione, senza nessuna modifica al codice.

L'URL dell'icona servita da HA sarà:
```
https://brands.home-assistant.io/custom_integrations/alarm_guardian/icon.png
```
