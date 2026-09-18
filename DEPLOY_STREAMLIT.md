# Pubblicazione su Streamlit Community Cloud

## Configurazione

1. Caricare su un repository GitHub i file dell’app, `cloudlab/`, `environment.yml`, `.streamlit/config.toml` e `data/restoration/`.
2. Accedere a https://share.streamlit.io/ con l’account autorizzato sul repository.
3. Creare una nuova app scegliendo repository e branch, con **Main file path: `streamlit_cloud.py`**.
4. Nelle impostazioni avanzate scegliere **Python 3.12** e pubblicare.
5. Nei log controllare l’installazione Conda di `libpdal-core` e `libpdal-e57`. L’ambiente Linux remoto deve essere verificato: il lock macOS non va utilizzato.
6. Acquisire l’esempio Palazzo in LAZ e poi in E57, aprire le anteprime, elaborare con «Nuvola più leggera» e scaricare il risultato. Questo è il controllo finale da effettuare sul servizio reale.

Non caricare `.env/`, `.mamba/`, `.tools/`, `storage/`, credenziali, `.streamlit/secrets.toml` o dati personali. Il pacchetto `dist/scan-to-bim-streamlit.zip` contiene solo codice, configurazione, test e dati sintetici scelti esplicitamente.

## Comportamento online

- `streamlit_cloud.py` attiva la modalità online. `app.py` mantiene il flusso con cartelle del computer che esegue il server.
- L’interfaccia cloud accetta E57, LAS e LAZ dal browser: massimo 5 file, 50 MB per file, 100 MB per acquisizione. RCP/RCS vanno esportati in E57 tramite ReCap.
- Ogni nuova sessione browser riceve un progetto distinto; le liste di dataset non mostrano quelli delle altre sessioni. Non è un sistema di account con accesso persistente.
- Il worker condiviso elabora un lavoro alla volta. I caricamenti sono salvati con nomi normalizzati in directory distinte prima dell’acquisizione e rimossi dal worker dopo l’archiviazione.
- L’ammissione di nuovi upload controlla uno spazio indicativo di 1 GB (incluso margine pari a tre volte i nuovi ingressi). Non è una quota rigida per risultati futuri o richieste concorrenti.
- Scaricare i risultati prima di uscire. Il disco del servizio non è un archivio permanente e una nuova sessione non recupera i progetti precedenti. La demo non cancella automaticamente i vecchi archivi: il gestore deve pulirli a servizio fermo quando esauriti.
- Restano i limiti di memoria e di elaborazione della POC. Il servizio gratuito è destinato a dimostrazioni con dataset contenuti, non a un archivio di produzione.

## Verifica locale della modalità cloud

```sh
.env/bin/python -m streamlit run streamlit_cloud.py --server.address 127.0.0.1
.env/bin/python -m pytest -q tests/test_cloud_uploads.py tests/test_app.py
```

Il binding del server non è fissato a localhost nella configurazione condivisa, per consentire l’avvio in hosting. Per limitare l’app locale al solo Mac usare `--server.address 127.0.0.1`.

Riferimento: https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies
