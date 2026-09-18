"""Italian field guidance shown through each field’s information icon."""

HELP = {
    "local_reference": """**Che cos’è il CRS?**
CRS significa «sistema di riferimento delle coordinate»: descrive come interpretare la posizione dei punti, il riferimento geografico e le unità associate. Non è una libreria: è un’informazione che può essere contenuta nel file del rilievo.

**Che ruolo ha PDAL?**
Durante l’acquisizione, l’app usa PDAL per leggere la nuvola e i suoi metadati, compreso il CRS quando disponibile. L’app usa queste informazioni per controllare il riferimento del rilievo. PDAL non può dedurre con certezza un CRS o le unità se il file non li dichiara.

**A cosa serve per lo SCAN TO BIM?**
Conoscere le unità permette di interpretare correttamente dimensioni e distanze: per esempio, una cella di riduzione da 0,03 corrisponde a 3 cm solo se le coordinate sono in metri. Il riferimento serve anche a confrontare rilievi e a impostare eventuali trasformazioni verso il sistema del progetto BIM.

**Quando selezionare la conferma?**
Solo per un rilievo senza CRS, quando sai che usa coordinate locali in metri, per esempio misurate rispetto a un’origine scelta nell’edificio. Una differenza di 10 nelle coordinate deve corrispondere a 10 metri. Se il rilievo è in millimetri, non confermare: 1000 millimetri equivalgono a 1 metro.

**Che cosa cambia selezionandola?**
L’app registra la tua dichiarazione di riferimento locale in metri per i controlli di acquisizione. La conferma non converte le coordinate, non assegna una posizione geografica e non allinea automaticamente le nuvole. Se non conosci le unità, lascia la casella deselezionata: puoi acquisire i file, ma il riferimento resterà da verificare.""",
    "clouds": """**Quali file verranno elaborati?**
Seleziona una o più nuvole già acquisite. Gli stessi parametri vengono applicati a ciascuna nuvola, producendo un risultato separato: i file non vengono uniti né allineati.

Più file selezioni, più elaborazioni entrano in coda. Togliere un file dalla selezione lo esclude solo da questa esecuzione. Gli originali restano nell’archivio.

**Esempio:** selezionando palazzo e chiostro ottieni due derivati. Se hanno unità o riferimenti diversi, elaborali separatamente con parametri adatti.""",
    "profile": """**Una configurazione iniziale da personalizzare.**
- **Controllo e conversione:** nessun filtro guidato attivo; prepara il formato scelto.
- **Pulizia leggera:** attiva la rimozione statistica dei punti isolati, con 16 vicini e tolleranza 2.
- **Nuvola più leggera:** attiva celle 3D di lato 0,03 nelle unità della sorgente.
- **Analisi delle superfici:** attiva normali e curvatura, con 16 vicini.

Il profilo non avvia PDAL. Cambiandolo si passa a un modulo con i suoi valori: ricontrolla tutte le impostazioni prima di eseguire. Puoi combinare manualmente più operazioni.

**Per iniziare:** sui nostri esempi in metri, 0,03 corrisponde a 3 cm; sul tuo rilievo verifica prima le unità.""",
    "outlier": """**A cosa serve:** riconosce punti insolitamente lontani dai vicini, come misure isolate nello spazio. La POC li classifica come rumore e li elimina dal derivato; elimina anche i punti già in classe 7.

**Attivo:** il risultato può contenere meno punti. **Disattivo:** questa pulizia non viene eseguita; numero di vicini e tolleranza sono ignorati.

**Nel restauro:** una cornice sottile o una zona poco rilevata può sembrare isolata pur essendo reale. Confronta il risultato 3D con l’originale, soprattutto su bordi, ferri e decorazioni. Non ripara lacune e non riconosce automaticamente il degrado.

Richiede il caricamento dei punti in memoria: limite della POC di 2 milioni di punti originali per nuvola.""",
    "mean_k": """**Significato:** quanti punti vicini vengono considerati per stimare la distanza media attorno a ogni punto. È un conteggio, non una distanza in metri. Si usa solo con «Rimuovi punti isolati» attivo.

**Aumentando il valore:** si considera un intorno più ampio, con più calcoli; possono essere coinvolte superfici vicine ma diverse, per esempio parete e cornice.

**Diminuendolo:** l’analisi diventa più locale e sensibile a piccole variazioni di densità. Non esiste una relazione garantita «più vicini = più punti eliminati».

**Esempio:** confronta 8, 16 e 32 vicini mantenendo la stessa tolleranza; controlla quali elementi cambiano nella vista 3D.""",
    "multiplier": """**Significato:** moltiplicatore della deviazione standard usato nella soglia statistica. La soglia è la media globale delle distanze medie dai vicini, più questo valore moltiplicato per la loro deviazione standard. Non è una percentuale né una distanza.

**Aumentando il valore**, a parità degli altri parametri, la soglia sale: la pulizia è meno severa e tende a conservare più punti.

**Diminuendolo:** la soglia scende e possono essere eliminati anche punti validi di zone rade o dettagli sottili.

**Esempio:** con 16 vicini, passare da 2 a 3 rende il filtro più tollerante; passare da 2 a 1 lo rende più severo. Questa impostazione conta solo se la pulizia è attiva.""",
    "reduction": """**Scegli come alleggerire la nuvola.**
- **Nessuna:** questo passaggio conserva tutti i punti rimasti dopo ritaglio e pulizia.
- **Per celle 3D (voxel):** divide lo spazio in cubi e conserva, in ogni cubo occupato, il punto originale più vicino al centro. Rende la densità più uniforme; non crea un punto medio.
- **Un punto ogni N:** conserva periodicamente un punto secondo l’ordine del file. È veloce, ma non garantisce una distribuzione uniforme nello spazio.

La riduzione elimina punti dal derivato: non riempie buchi e non ricostruisce superfici. Il lato della cella conta solo in modalità voxel; N conta solo per il campionamento periodico. Per rilievi grandi la decimazione può usare lo streaming; il voxel richiede memoria.""",
    "voxel": """**Significato:** lunghezza dello spigolo di ciascun cubo della griglia 3D. Si usa soltanto scegliendo «Per celle 3D (voxel)».

**Unità:** quelle delle coordinate originali, prima della riproiezione. In metri, 0,03 = 3 cm; se il file usa millimetri, per 3 cm devi inserire 30. Con coordinate in gradi il valore è in gradi, non in metri.

**Cella più grande:** più punti condividono un cubo; in genere il risultato si alleggerisce, ma piccoli rilievi e modanature possono perdere definizione.

**Cella più piccola:** conserva più dettaglio e normalmente più punti, con un file più grande. Il conteggio esatto dipende dalla distribuzione dei punti e dalla griglia.

**Esempio:** su una facciata in metri confronta 0,03 e 0,10: stai passando da cubi di 3 cm a cubi di 10 cm. Se vuoi misure metriche da un CRS geografico, riproietta prima in un’elaborazione separata.""",
    "stride": """**Significato:** conserva un punto ogni N nell’ordine della nuvola, solo con la modalità «Un punto ogni N».

**N più grande:** meno punti, file generalmente più piccolo e minor dettaglio. **N più piccolo:** più punti e maggiore dettaglio.

**Esempio:** da 90.000 punti, N=2 ne conserva circa 45.000; N=10 circa 9.000, se nessun altro filtro ne ha già eliminati. Il risultato preciso dipende dai punti in ingresso a questo passaggio.

Non indica centimetri tra i punti: se l’ordine del rilievo segue linee di scansione, può produrre strisce o zone meno rappresentate. Può essere eseguito in streaming.""",
    "crop": """**A cosa serve:** conserva solo i punti la cui coordinata Z è compresa tra quota minima e massima, estremi inclusi. Il ritaglio avviene prima di pulizia, riduzione e riproiezione.

**Attivo:** i punti fuori dall’intervallo sono esclusi dal derivato. **Disattivo:** i due limiti sono ignorati.

**Esempio:** con Z locale in metri, 2–5 conserva una fascia di 3 m. Non è necessariamente l’altezza dal pavimento: Z può essere una quota assoluta o riferita a un’origine locale.

Si applicano gli stessi limiti a tutti i file selezionati. Verifica che abbiano lo stesso riferimento verticale. Non seleziona una stanza né ritaglia X e Y.""",
    "z_min": """**Limite inferiore della coordinata Z**, nelle unità della sorgente. Conta solo se il ritaglio è attivo.

**Aumentandolo:** alzi il piano inferiore e scarti altri punti nella parte bassa. **Diminuendolo:** includi una fascia più bassa, fino ai punti disponibili nel sorgente.

**Esempio:** mantenendo il massimo a 5 m, passare da minimo 0 a 2 m esclude i punti con Z inferiore a 2 m.

Deve essere inferiore alla quota massima. Il valore iniziale deriva dall’estensione delle nuvole selezionate. Un intervallo esterno al rilievo può lasciare zero punti e non produrre un risultato utile.""",
    "z_max": """**Limite superiore della coordinata Z**, nelle unità della sorgente. Conta solo se il ritaglio è attivo.

**Diminuendolo:** abbassi il piano superiore e scarti altri punti nella parte alta. **Aumentandolo:** includi una fascia più alta, fino ai punti presenti nella sorgente.

**Esempio:** con minimo 0 m, passare da massimo 12 a 8 m può escludere il tetto e lasciare la parte inferiore della facciata, se il rilievo usa quel riferimento.

Deve essere superiore alla quota minima. Il numero rappresenta Z, non l’altezza dell’edificio. Un intervallo senza punti non genera una nuvola utilizzabile.""",
    "normals": """**A cosa serve:** aggiunge una direzione perpendicolare stimata alla superficie per ogni punto e un indicatore locale di curvatura. Può aiutare a distinguere l’orientamento di pareti, pavimenti e coperture.

**Attivo:** aggiunge NormalX, NormalY, NormalZ e Curvature, consultabili come colori nell’anteprima se conservati nel formato esportato. Non sposta i punti e non crea una mesh o un modello BIM.

**Disattivo:** questi attributi non vengono ricalcolati. Il numero di vicini per le normali è ignorato.

Il calcolo segue riduzione e riproiezione, quindi dipende dai punti rimasti e dalle coordinate trasformate. Preferisci coordinate cartesiane metriche per un’analisi geometrica. Normali e curvatura sono stime, non una misura certificata del degrado. Richiede memoria; per conservare gli attributi usa LAS/LAZ.""",
    "knn": """**Significato:** numero di vicini usati per stimare il piano locale, la normale e la curvatura. Conta solo quando «Calcola normali e curvatura» è attivo.

**Più vicini:** stima generalmente più stabile rispetto al rumore, ma può mediare insieme spigoli e piccole modanature; richiede più lavoro di calcolo.

**Meno vicini:** segue dettagli più piccoli ma è più sensibile a rumore, buchi e densità irregolare.

**Esempio:** confronta 8 e 32 vicini sulla cornice di una finestra. Se prima riduci molto i punti, gli stessi 16 vicini coprono una porzione fisica più ampia della facciata. Il valore è un conteggio, non un raggio in metri.""",
    "input_crs": """**Il CRS descrive come interpretare le coordinate:** sistema di riferimento, proiezione e unità. Inseriscilo solo se conosci il riferimento effettivo e manca nel file o nelle dichiarazioni già registrate.

**Vuoto:** l’app cerca il CRS nel file e nelle dichiarazioni valide. Un rilievo locale può restare senza un CRS geodetico per le operazioni locali.

**Compilato:** dichiara il significato delle coordinate di ingresso; da solo non le sposta. Un valore che contraddice il CRS incorporato viene rifiutato.

**Esempio:** EPSG:32632 significa WGS 84 / UTM zona 32N, in metri. Usalo soltanto per dati effettivamente in quel sistema. Assegnarlo a un palazzo con origine arbitraria (0,0,0) non lo georeferenzia.""",
    "output_crs": """**È il sistema in cui vuoi ottenere le coordinate del derivato.**

**Vuoto:** nessuna riproiezione; si mantengono le coordinate in ingresso. **Compilato:** PDAL calcola nuove coordinate nel sistema di destinazione e deve conoscere un CRS sorgente valido.

**Esempio:** da EPSG:32632 a EPSG:32633 cambi zona UTM e valori X/Y; non stai allineando due scansioni. EPSG:4326 produce coordinate orizzontali in gradi: la stessa distanza non ha più un valore numerico in metri.

Il ritaglio e il voxel guidati lavorano prima della trasformazione; normali e filtri JSON aggiuntivi dopo. Una trasformazione orizzontale non garantisce una conversione del riferimento altimetrico: quest’ultima richiede sistemi verticali e risorse adeguati.""",
    "extra": """**Per costruire una sequenza personalizzata:** scrivi un array JSON di filtri tra quelli ammessi nel pannello. `[]` non aggiunge operazioni.

I filtri si eseguono nell’ordine scritto, dopo i controlli guidati, incluse riproiezione e normali, e prima dell’esportazione. Cambiare l’ordine può cambiare il risultato; non aggiungere involontariamente un secondo filtro già attivato sopra.

**Esempio:** `[{"type":"filters.expression","expression":"Intensity > 1000"}]` conserva i punti con intensità superiore a 1000. Aumentare la soglia restringe la selezione. La scala dell’intensità dipende dallo strumento: 1000 è solo un esempio, non una soglia di degrado.

I filtri possono eliminare punti, cambiare coordinate o aggiungere attributi. Sono ammessi al massimo 20 filtri con opzioni validate; lettori, scrittori e percorsi sono gestiti dall’app. Consulta i parametri del driver nel catalogo prima di usarlo; gli algoritmi in memoria restano soggetti al limite di 2 milioni di punti.""",
    "format": """**Sceglie il contenitore del nuovo file, non un livello di dettaglio.**
- **LAZ:** LAS compresso senza perdita rispetto agli stessi dati LAS; comodo per risparmiare spazio.
- **LAS:** formato non compresso, in genere più voluminoso. Come LAZ, conserva il CRS disponibile e dimensioni extra, incluse normali e curvatura.
- **E57:** utile per scambiare nuvole; l’export della POC appiattisce le scansioni e può perdere immagini e attributi non supportati. Conserva originale e report.
- **PLY:** utile in flussi geometrici; non incorpora un CRS geodetico. Il riferimento resta nel report. Nella POC è soggetto al limite di memoria.

Cambiare formato da solo non riduce intenzionalmente il numero dei punti. La riscrittura può quantizzare le coordinate o perdere attributi: LAS/LAZ usa normalmente passi di 0,001 unità, oppure 0,0000001 gradi per X/Y geografici. Il riepilogo segnala i limiti dell’export e ricontrolla conteggi ed estensione del file.""",
    "search": """Filtra il catalogo dei driver installati cercando nel nome e nella descrizione. Prova «normal», «crop» o «las». Una ricerca più specifica mostra meno voci; il campo vuoto le mostra tutte. Consultare un driver non lo aggiunge alla pipeline.""",
    "driver": """Un driver è un lettore, un filtro o uno scrittore PDAL. Seleziona una voce e premi «Mostra i parametri del driver» per leggere le opzioni della versione installata. La selezione serve solo alla consultazione. Per eseguire un filtro ammesso, configuralo nei controlli guidati o nel JSON; gli altri richiedono una pipeline dedicata.""",
}
