# SCAN TO BIM

POC **Streamlit + PDAL** per acquisire dataset di nuvole di punti, visualizzarli in 3D e prepararli per le successive lavorazioni Scan to BIM. Il percorso guidato ha quattro passi: **Scegli i file → Guarda in 3D → Prepara i dati → Vedi i risultati**.

I pulsanti di navigazione sono raggruppati in fondo al modulo: indietro a sinistra e azione successiva a destra; su schermi stretti sono disposti uno sotto l’altro. Al cambio di passaggio, anche dalla barra numerata, la pagina scorre al primo gruppo di campi della sezione. La modifica di un parametro mantiene invece la posizione di lettura. Tornare indietro dal pannello PDAL non avvia elaborazioni.

## Avvio

Dalla cartella del progetto, con l’ambiente locale già installato:

```bash
.env/bin/python -m streamlit run app.py
```

Apri [l’app locale](http://localhost:8501). Per provare il percorso completo, scegli **Esempi di restauro**, lascia selezionato il palazzo e premi **Carica e apri la vista 3D**. Al termine della verifica appare la nuvola; **Continua: prepara i dati** conduce ai filtri. **Elabora e vedi i risultati** porta al riepilogo e al confronto 3D.

Su un’altra macchina, con Conda:

```bash
conda env create -f environment.yml
conda activate pointcloud-lab
python -m cloudlab.restoration_samples
streamlit run app.py
```

`environment-osx-arm64.lock.txt` contiene le dipendenze fissate per il Mac Apple Silicon. Il worker usa un lock POSIX: questa configurazione è per macOS/Linux. E57 richiede il plugin `libpdal-e57`, incluso nell’ambiente. Il pannello mostra la versione PDAL effettiva e i driver disponibili.

## 1 · Dataset

Scegli una delle due fonti:

- **Cartella locale:** premi **Sfoglia cartelle**, naviga con un clic e conferma **Usa questa cartella**: la ricerca parte automaticamente. Sono disponibili scorciatoie per Casa, Documenti e dischi/NAS, ricerca delle sottocartelle e paginazione. In alternativa, incolla il percorso e premi **Trova le nuvole**. Puoi includere le sottocartelle. Un NAS deve essere già montato e accessibile al computer che esegue Streamlit: se l’app gira su un server, il selettore esplora quel server.
- **Esempi di restauro:** scegli un caso oppure tutti e tre, quindi il formato sorgente LAZ, E57 o LAS. Ogni acquisizione usa una variante per scena.

Assegna un nome e premi **Carica e apri la vista 3D**. La pagina segue automaticamente copia, verifica e creazione dell’anteprima. Un duplicato viene collegato all’originale esistente e rimane visualizzabile. I lotti si ritrovano in **Riapri un lavoro salvato**.

Per rilievi privi di CRS, conferma **coordinate locali in metri** solo quando corretto. Il riferimento incorporato viene letto dal file; unità o riferimento da verificare si completano nel Riepilogo. La conferma documenta i dati e non trasforma le coordinate.

### Esempi inclusi

| Dataset | Elementi rappresentati |
| --- | --- |
| Palazzo storico | Facciata, finestre, portale ad arco, cornici, copertura e lacune simulate dell’intonaco |
| Chiostro | Due ali con colonnato, archi, coperture e pavimentazione lapidea |
| Fontana storica | Bene lapideo con vasche sovrapposte, piedistallo, modanature e lacuna simulata del bordo |

In `data/restoration/` sono presenti **9 file reali: 3 scene × 3 formati**, ciascuno con **90.000 punti**, generati e riletti con PDAL. Il manifest dichiara coordinate cartesiane locali in metri, origine propria di ogni scena, rumore sintetico con deviazione standard di 2 mm e 45 outlier per scena. Colori, classi e alterazioni sono dimostrativi e non rappresentano una diagnosi conservativa. Le scene non sono scansioni registrate di uno stesso sito.

Rigenerazione: `python -m cloudlab.restoration_samples`. I precedenti esempi in `data/samples/` e gli archivi esistenti sono conservati; la nuova selezione propone esclusivamente edifici e beni da restaurare.

## 2 · Anteprima 3D

Seleziona una nuvola e trascina per ruotarla; i controlli del grafico permettono lo zoom. Sono disponibili vista dall’alto, dimensione dei punti e colori per RGB, quota, intensità, classificazione o dimensioni aggiuntive prodotte da PDAL.

L’anteprima contiene **al massimo 30.000 punti**. È un campione di controllo separato: le elaborazioni lavorano sull’intera nuvola acquisita. Dopo un’elaborazione puoi scegliere **Originale acquisito** o **Risultato PDAL** e confrontare le versioni della stessa nuvola.

## 3 · Pannello PDAL

Scegli le nuvole da elaborare e un punto di partenza: **Controllo e conversione**, **Pulizia leggera**, **Nuvola più leggera**, **Analisi delle superfici**. Modifica i parametri prima dell’esecuzione.

Ogni impostazione ha un’icona informativa con significato, unità, effetti delle modifiche ed esempi. Le spiegazioni sono accessibili solo attraverso le icone dei singoli campi, senza guide aggiuntive. I pulsanti di azione hanno dimensioni uniformi; il verde indica il passaggio successivo, segnalato anche da **Passaggio X di 4**; le impostazioni vengono inviate soltanto con **Elabora e vedi i risultati**.

| Controllo guidato | Elaborazione |
| --- | --- |
| Rimuovi punti isolati | Filtro statistico con numero di vicini e tolleranza; elimina la classe 7, inclusi i punti già classificati così |
| Riduzione per celle 3D | Un punto vicino al centro di ogni voxel occupato |
| Un punto ogni N | Decimazione deterministica |
| Intervallo di quota Z | Conservazione dei punti compresi tra minimo e massimo |
| Normali e curvatura | Direzioni locali e curvatura delle superfici |
| Coordinate e riproiezione | Trasformazione verso un CRS di destinazione, con sorgente valida obbligatoria |
| Formato del risultato | LAS, LAZ, E57 o PLY, secondo i writer installati |

Il ritaglio Z, il filtro statistico e la riduzione guidata usano le **unità della sorgente** e precedono l’eventuale riproiezione. Normali e filtri JSON aggiuntivi sono applicati dopo la riproiezione, quando richiesta: le loro distanze usano quindi le unità delle coordinate trasformate. Il CRS dichiarato non può contraddire un CRS incorporato. Assegnare un codice EPSG a coordinate locali non equivale a georeferenziarle.

### Catalogo completo e filtri avanzati

**Esplora tutte le funzionalità PDAL installate** mostra il catalogo reale di reader, filter e writer con ricerca, descrizioni, parametri e documentazione. L’esecuzione tramite questa POC usa i controlli guidati e una selezione esplicita di filtri avanzati; gli altri driver si consultano e si integrano in pipeline dedicate.

**Filtri avanzati · JSON** accetta fino a 20 filtri, con sole opzioni consentite. La selezione comprende espressioni, assegnazione/copia di dimensioni, trasformazioni, campionamento, normali, autovalori, caratteristiche di covarianza, adattamento ai piani, distanze, clustering, DBSCAN e classificazione terreno SMRF/CSF, quando disponibili. L’elenco aggiornato compare nel pannello. Reader, writer e percorsi sono gestiti dall’app; driver che eseguono codice o leggono file arbitrari non sono ammessi.

Esempio:

```json
[
  {"type": "filters.expression", "expression": "Intensity > 1000"}
]
```

Ogni esecuzione salva **un nuovo derivato per ciascuna nuvola**. Il sorgente archiviato viene verificato tramite SHA-256 prima di avviare PDAL e non viene sovrascritto. Per ripetere una pipeline scaricata su un’altra macchina, aggiorna i percorsi assoluti nel JSON.

## 4 · Riepilogo

La pagina riunisce esito dell’acquisizione, file leggibili, punti originali, riferimenti da completare ed elaborazioni PDAL. Durante i job mostra l’avanzamento; al termine espone punti prima/dopo, riduzione e collegamento al confronto 3D.

Puoi scaricare:

- **Report PDAL:** checksum sorgente/risultato, conteggi, bounding box, durata, parametri, CRS, modalità streaming, metadati e avvisi.
- **Pipeline JSON** effettivamente eseguita.
- **Nuvola elaborata:** download preparato su richiesta fino a 100 MB; per file più grandi viene indicato il percorso locale.
- **Riepilogo completo JSON** di asset ed elaborazioni.
- **Manifest degli originali pronti** per la consegna documentata. Include gli originali; i derivati sono descritti nel riepilogo completo e nei report PDAL.

Un rilievo **Da completare** può essere visualizzato e sottoposto a operazioni locali, ma richiede la conferma di riferimento e unità prima della consegna degli originali. Gli errori di acquisizione si possono riprovare; per un’elaborazione non riuscita torna al pannello e correggi i parametri.

## Formati e conservazione dei dati

- **E57, LAS e LAZ in ingresso:** originale archiviato, verifica completa PDAL e anteprima separata. I punti E57 marcati `Omit` rimangono nell’originale e sono esclusi da anteprima e derivato.
- **RCP/RCS:** conservati con stato **Conversione richiesta**. Apri il progetto originale in ReCap Pro, mantenendo tutti gli RCS e le dipendenze, esporta E57 e usa **Acquisisci E57 collegato**. PDAL non valida né converte direttamente RCP/RCS. L’archiviazione per checksum non ricostruisce un progetto ReCap apribile; il legame al derivato è una dichiarazione di provenienza.
- **LAS/LAZ in uscita:** conservano il CRS disponibile e supportano dimensioni aggiuntive, comprese le normali. La scala ordinaria di scrittura è 0,001 unità; per coordinate geografiche X/Y è 0,0000001 gradi.
- **E57 in uscita:** nuvola appiattita; non ricostruisce scansioni, immagini e campi non supportati dal writer. Conserva originale e report per struttura e riferimento; verifica le dimensioni effettivamente esportate.
- **PLY in uscita:** non conserva un CRS geodetico incorporato. Il riferimento rimane documentato nel report.

I report avvisano delle limitazioni del formato e dell’eventuale perdita di normali. Conversione e pulizia non certificano che la nuvola sia allineata o idonea alla modellazione BIM.

## Dataset grandi e persistenza

La cartella locale/NAS permette di acquisire da disco senza un limite applicativo di dimensione per file; il lotto contiene al massimo **5.000 file**. Gli originali sono copiati a blocchi da 8 MB, con checksum e controllo delle modifiche del sorgente durante la copia. La deduplicazione riguarda file binariamente identici nello stesso progetto: LAS, LAZ ed E57 della stessa scena sono file distinti.

La verifica iniziale usa PDAL in streaming. Per il preprocessing viene validata l’intera pipeline: dove possibile usa lo streaming; algoritmi in memoria, tra cui voxel, filtro statistico e normali, hanno un limite di **2 milioni di punti in ingresso per nuvola**, applicato anche quando una riduzione precedente diminuirebbe i punti. PLY rientra nel controllo conservativo degli algoritmi in memoria. Per superare il limite occorre suddividere la sorgente o costruire un flusso dedicato. Il file esportato non viene campionato per aggirarlo.

Il worker locale elabora un job alla volta, prima acquisizioni e poi elaborazioni. Il browser può cambiare pagina o chiudersi mentre il servizio continua. Arrestando il servizio, la coda persiste e i job interrotti ripartono dall’inizio al successivo avvio. Ogni comando di elaborazione PDAL ha un timeout di due ore. Le barre indicano fasi, non il tempo residuo. Occorre spazio disco per originali, risultati e file temporanei.

```text
storage/
  catalog.sqlite3                # Progetti, dataset, asset, job ed elaborazioni
  originals/                     # Originali per SHA-256, resi non scrivibili
  work/<asset>/attempt-N/         # Verifica e anteprima dell’originale
  processing/<run>/              # Derivato, anteprima, pipeline, report e metadati
  deliveries/                    # Manifest degli originali pronti
  worker.lock                    # Un solo worker locale per archivio
```

### Worker separato e configurazione

`CLOUDLAB_STORAGE` cambia la directory dell’archivio; SQLite e lock devono stare su filesystem locale. `PDAL_BIN` sceglie un altro eseguibile PDAL. `CLOUDLAB_WORKER=0` disabilita il worker incorporato.

Con l’ambiente attivo, avvia l’interfaccia:

```bash
export CLOUDLAB_STORAGE=/percorso/archivio-locale
CLOUDLAB_WORKER=0 streamlit run app.py
```

In un secondo terminale, con lo stesso ambiente e la stessa directory:

```bash
python -m cloudlab.worker --storage /percorso/archivio-locale
```

Il worker CLI gestisce acquisizione e preprocessing. Per elaborare entrambe le code fino a esaurimento e terminare:

```bash
python -m cloudlab.worker --storage /percorso/archivio-locale --once
```

Per il backup, arresta UI e worker e copia l’intero archivio. I percorsi persistenti sono assoluti: un trasferimento a un’altra posizione richiede il loro aggiornamento. I manifest di consegna controllano presenza, dimensione e data di modifica; il consumatore deve verificare SHA-256 prima dell’uso.

## Ambito della POC e test

Questa POC locale copre ingestion, controllo, preprocessing e documentazione. Non comprende registrazione automatica tra scansioni, diagnosi del degrado, ricostruzione di superfici o modellazione BIM automatica. Per dataset di produzione restano da progettare storage distribuito, elaborazione per partizioni, visualizzazione multirisoluzione, autenticazione multiutente e gestione delle risorse infrastrutturali.

```bash
.env/bin/python -m pytest -q
```

I test includono lettura dei formati reali, esempi di restauro, conservazione degli originali, deduplicazione, ripresa dei job, operazioni PDAL, riproiezione, limiti di memoria, report persistenti e flusso Streamlit.
