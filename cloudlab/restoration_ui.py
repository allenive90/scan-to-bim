"""A four-step, dataset-centred Scan to BIM workspace for restoration surveys."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from uuid import uuid4

import numpy as np
import streamlit as st

from cloudlab.catalog import Catalog, LABELS, discover
from cloudlab.engine import ROOT, CloudError, run_pdal
from cloudlab.restoration_samples import SCENES
from cloudlab.viewer import point_figure
from cloudlab.worker import Worker
from cloudlab import pdal_workbench as workbench
from cloudlab.pdal_help import HELP
from cloudlab.cloud_uploads import validate_uploads, stage_uploads

STEPS = ["1 · Scegli i file", "2 · Guarda in 3D", "3 · Prepara i dati", "4 · Vedi i risultati"]
STEP_HINTS = [
    "Scegli una cartella o un esempio, poi premi Carica e apri la vista 3D.",
    "Controlla la nuvola ruotandola. Quando sei pronto, continua con la preparazione dei dati.",
    "Scegli un’attività già configurata e premi Elabora. Personalizza i parametri solo se necessario.",
    "Controlla l’esito, scarica i file o torna alla vista 3D per confrontare il risultato.",
]
OLD_STEPS = ["1 · Dataset", "2 · Anteprima 3D", "3 · Pannello PDAL", "4 · Riepilogo"]
EXAMPLES = ROOT / "data/restoration"


def size_label(value):
    value = float(value or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024


@st.cache_resource
def service(storage, background=True):
    catalog = Catalog(storage)
    workbench.ensure_schema(catalog)
    projects = catalog.rows("SELECT * FROM projects WHERE name=? ORDER BY created LIMIT 1", ("Restauro architettonico",))
    project_id = projects[0]["id"] if projects else catalog.create_project(
        "Restauro architettonico", "Coordinate locali: origine propria di ciascun dataset", units="m")
    worker = Worker(catalog)
    if background:
        worker.start()
    return catalog, worker, project_id


@st.cache_data(ttl=120)
def drivers():
    return workbench.driver_catalog()


def go(step, **values):
    request_scroll()
    st.session_state["restoration_navigation"] = {"restoration_step": STEPS[step - 1], **values}
    st.rerun(scope="app")


def request_scroll():
    st.session_state["scroll_request"] = uuid4().hex


def landing_anchor():
    ticket = st.session_state.get("scroll_request", "idle")
    st.html(f'<div data-scroll-ticket="{ticket}" tabindex="-1" aria-label="Inizio della sezione" '
            'style="height:0;scroll-margin-top:88px;outline:none"></div>')


def apply_navigation_scroll():
    ticket = st.session_state.pop("scroll_request", None)
    if ticket:
        script = (Path(__file__).with_name("navigation_scroll.js")).read_text()
        # Only a server-generated UUID is substituted; no user data enters JavaScript.
        st.html("<script>" + script.replace("__SCROLL_TICKET__", json.dumps(ticket)) + "</script>",
                unsafe_allow_javascript=True)


def previous_button(step):
    if st.button("← Passaggio precedente", key=f"previous-{step}", width="stretch"):
        go(step - 1)


def batch_assets(catalog, project_id, batch_id):
    if not batch_id:
        return []
    return [catalog.asset(r["id"]) for r in catalog.assets(project_id, batch_id=batch_id, limit=5000)]


def effective_assets(catalog, assets):
    result, seen = [], set()
    for asset in assets:
        if asset["status"] == "DUPLICATE" and asset["duplicate_of"]:
            asset = catalog.asset(asset["duplicate_of"])
        if asset["id"] not in seen and asset["status"] in {"READY", "REVIEW"}:
            result.append(asset)
            seen.add(asset["id"])
    return result


def runs_for(catalog, project_id, assets):
    ids = {a["id"] for a in effective_assets(catalog, assets)}
    return [r for r in workbench.list_runs(catalog, project_id) if r["asset_id"] in ids]


def scan_local_folder(catalog, location, recursive):
    # A failed search must never leave an older, selectable result behind.
    st.session_state.pop("folder_scan", None)
    with st.spinner("Cerco le nuvole nella cartella…"):
        found = discover(location, recursive, excluded=catalog.root)
    st.session_state["folder_scan"] = (location, recursive, [str(p) for p in found])


def enter_folder(path):
    st.session_state["folder_browser"] = str(path)


@st.dialog("Scegli la cartella delle nuvole", width="large")
def browse_folder(catalog, recursive):
    default_folder = Path.home() / "Documents"
    if not default_folder.is_dir():
        default_folder = Path.home()
    current = Path(st.session_state.get("folder_browser", str(default_folder))).expanduser().resolve()
    st.caption("Apri una cartella con un clic. Quando trovi quella giusta, premi Usa questa cartella.")
    home, documents, disks = st.columns(3)
    home.button("Casa", on_click=enter_folder, args=(Path.home(),), width="stretch")
    documents.button("Documenti", on_click=enter_folder, args=(Path.home() / "Documents",), width="stretch")
    disks.button("Dischi e NAS", on_click=enter_folder, args=(Path("/Volumes"),), width="stretch")
    st.markdown("**Ti trovi in**")
    st.code(str(current), language=None, wrap_lines=True)
    st.button("↑ Cartella superiore", disabled=current == current.parent,
              on_click=enter_folder, args=(current.parent,), width="stretch")
    accessible = True
    try:
        children = sorted((p for p in current.iterdir() if p.is_dir() and not p.name.startswith(".")),
                          key=lambda p: p.name.casefold())
        query = st.text_input("Cerca una sottocartella", key=f"folder-filter-{current}", placeholder="Scrivi una parte del nome…")
        filtered = [p for p in children if query.casefold() in p.name.casefold()]
        if filtered:
            pages = (len(filtered) + 7) // 8
            page = int(st.number_input("Pagina", 1, pages, 1, key=f"folder-page-{current}-{query}")) if pages > 1 else 1
            st.caption(f"{len(filtered)} cartelle · pagina {page} di {pages}")
            with st.container(key="folder_choices", border=True):
                for child in filtered[(page-1)*8:page*8]:
                    label = re.sub(r"([\\`*_\[\]])", r"\\\1", child.name)
                    st.button(label + " →", icon=":material/folder:", key=f"folder-open-{child}",
                              on_click=enter_folder, args=(child,), width="stretch")
        else:
            st.info("Nessuna cartella corrisponde al nome cercato." if query else
                    "Non ci sono sottocartelle. Puoi selezionare questa cartella per cercare le nuvole al suo interno.")
    except OSError:
        accessible = False
        st.warning("Non riesco ad aprire questa cartella. Scegli un’altra posizione o verifica i permessi di accesso.")
    st.caption("Verranno cercati file E57, LAS, LAZ, RCP e RCS" + (", anche nelle sottocartelle." if recursive else ", solo in questa cartella."))
    cancel, confirm = st.columns(2)
    if cancel.button("Annulla", width="stretch"):
        st.rerun(scope="app")
    if confirm.button("Usa questa cartella", type="primary", width="stretch", disabled=not accessible):
        try:
            scan_local_folder(catalog, str(current), recursive)
            st.session_state["pending_folder"] = str(current)
            st.rerun(scope="app")
        except (CloudError, OSError) as exc:
            st.error(str(exc))


def source_page(catalog, project_id, batch_id):
    st.subheader("Da dove vuoi iniziare?")
    st.caption("Scegli le nuvole. L’app le archivia, le verifica con PDAL e prepara la vista 3D.")
    landing_anchor()
    cloud = os.environ.get("CLOUDLAB_CLOUD") == "1"
    source = st.radio("Fonte del dataset", ["Carica dal computer" if cloud else "Cartella locale", "Esempi di restauro"], index=0, horizontal=True)
    uploads = []
    paths = []
    reference_confirmed = False
    if source == "Esempi di restauro":
        if (EXAMPLES / "overview.png").is_file():
            st.image(str(EXAMPLES / "overview.png"), caption="Tre rilievi sintetici dedicati al restauro", width="stretch")
        names = {"all": "Tutti gli esempi di restauro", **{key: val[0] for key, val in SCENES.items()}}
        scene = st.selectbox("Scegli un esempio", list(names), format_func=names.get, index=1)
        st.info(SCENES[scene][1] if scene != "all" else "Facciata storica, chiostro e fontana: tre casi per esplorare il lavoro sui rilievi di restauro.")
        st.caption("Dati sintetici, in metri. Lacune, alterazioni e rumore sono simulati per la dimostrazione e non costituiscono diagnosi conservative.")
        with st.expander("Formato dei file di esempio"):
            fmt = st.selectbox("Formato sorgente", ["LAZ", "E57", "LAS"])
        chosen = list(SCENES) if scene == "all" else [scene]
        paths = [EXAMPLES / f"{key}.{fmt.lower()}" for key in chosen]
        if not all(p.is_file() for p in paths):
            st.error("Gli esempi devono essere generati: python -m cloudlab.restoration_samples")
            paths = []
        name_default = names[scene]
        reference_confirmed = True
    elif cloud:
        st.caption("Seleziona i file dal tuo computer. Massimo 5 file, 50 MB ciascuno e 100 MB complessivi.")
        uploads = st.file_uploader("Nuvole da acquisire", type=["e57", "las", "laz"], accept_multiple_files=True, max_upload_size=50)
        st.caption("Per RCP/RCS, esporta prima un E57 da Autodesk ReCap.")
        try:
            validate_uploads(uploads)
        except CloudError as exc:
            st.error(str(exc))
            uploads = []
        name_default = "Nuovo rilievo"
        with st.expander("Riferimento spaziale e unità"):
            st.caption("PDAL legge il CRS presente nel file. Conferma solo se il rilievo senza CRS usa coordinate locali in metri.")
            reference_confirmed = st.checkbox("Confermo coordinate locali in metri", value=False, help=HELP["local_reference"])
    else:
        pending = st.session_state.pop("pending_folder", None)
        if pending is not None:
            st.session_state["dataset_folder"] = pending
        with st.container(border=True, key="local_folder_card"):
            st.markdown("### Scegli la cartella del rilievo")
            st.caption("Sfoglia le cartelle oppure incolla un percorso. I file restano sul computer durante la selezione.")
            recursive = st.checkbox("Includi sottocartelle", value=True,
                                    help="Cerca anche nelle cartelle contenute in quella selezionata. Disattiva per cercare solo al primo livello.")
            location = st.text_input("Cartella del dataset", key="dataset_folder", placeholder="Incolla qui il percorso, oppure premi Sfoglia cartelle",
                                     help="La cartella deve essere accessibile al computer che esegue l’app. Un disco esterno o un NAS deve essere già collegato.")
            browse, search = st.columns(2)
            if browse.button("Sfoglia cartelle", icon=":material/folder_open:", width="stretch"):
                if location.strip() and Path(location).expanduser().is_dir():
                    enter_folder(Path(location).expanduser().resolve())
                browse_folder(catalog, recursive)
            if search.button("Trova le nuvole", width="stretch", disabled=not location.strip()):
                try:
                    scan_local_folder(catalog, location, recursive)
                except (CloudError, OSError) as exc:
                    st.error(str(exc))
            last_scan = st.session_state.get("folder_scan")
            if last_scan and last_scan[0] == location and last_scan[1] != recursive:
                try:
                    scan_local_folder(catalog, location, recursive)
                except (CloudError, OSError) as exc:
                    st.error(str(exc))
            if not location.strip():
                st.caption("Non hai ancora scelto una cartella.")
            elif not st.session_state.get("folder_scan") or st.session_state["folder_scan"][0] != location:
                st.info("Premi Trova le nuvole per controllare questo percorso.")
        scan = st.session_state.get("folder_scan")
        if scan and scan[0] == location and scan[1] == recursive:
            available = [Path(p) for p in scan[2]]
            if available:
                st.success(f"Cartella pronta: {len(available)} file trovati. Puoi proseguire con il pulsante verde in fondo.")
                formats = sorted({p.suffix[1:].upper() for p in available})
                st.caption("Formati trovati: " + " · ".join(formats))
            with st.expander("Scegli i file da acquisire", expanded=len(available) < 10):
                paths = st.multiselect("Nuvole del dataset", available, default=available,
                                       format_func=lambda p: str(p.relative_to(Path(location).expanduser().resolve())))
            if not available:
                st.info("La cartella non contiene nuvole supportate. Prova a includere le sottocartelle oppure scegli un’altra cartella.")
        name_default = Path(location).name if location.strip() else "Nuovo rilievo"
        with st.expander("Riferimento spaziale e unità"):
            st.caption("Il CRS (sistema di riferimento delle coordinate) presente nel file viene letto automaticamente tramite PDAL. Per un rilievo locale senza CRS, conferma qui solo se le coordinate sono in metri.")
            reference_confirmed = st.checkbox("Confermo coordinate locali in metri", value=False,
                                              help=HELP["local_reference"])
    name = st.text_input("Nome del dataset", value=name_default, key=f"name-{source}-{name_default}")
    if paths:
        st.caption(f"{len(paths)} file · {size_label(sum(p.stat().st_size for p in paths if p.exists()))}")
        st.markdown("**Prossimo passo · Acquisisci il dataset per esplorarlo in 3D.**")
    with st.container(key="workflow_actions_1", border=True):
        start = st.button("Carica e apri la vista 3D →", type="primary", width="stretch", disabled=not (paths or uploads))
    if start:
        try:
            if uploads:
                paths = stage_uploads(catalog.root, uploads)
            batch = catalog.enqueue(project_id, paths, name, {"source_type": source,
                "reference_confirmed": reference_confirmed, "domain": "restauro", "synthetic": source == "Esempi di restauro"})
            go(2, restoration_batch=batch, preview_asset=None, compare_run=None)
        except (CloudError, OSError) as exc:
            st.error(str(exc))


@st.fragment(run_every="3s")
def acquisition_progress(catalog, project_id, batch_id):
    assets = batch_assets(catalog, project_id, batch_id)
    pending = [a for a in assets if a["status"] in {"QUEUED", "RUNNING"}]
    if not pending:
        st.rerun(scope="app")
    st.subheader("Stiamo preparando la nuvola 3D")
    st.info("L’acquisizione è avviata. Non devi premere altro: al termine comparirà l’anteprima.")
    st.progress((len(assets)-len(pending))/max(1, len(assets)), text=f"{len(assets)-len(pending)} di {len(assets)} file verificati")
    for asset in pending[:5]:
        job = catalog.rows("SELECT phase FROM jobs WHERE asset_id=?", (asset["id"],))[0]
        st.caption(f'{asset["name"]} · {job["phase"]}')
    if st.button("Vedi il riepilogo durante l’attesa"):
        go(4)


def load_points(path):
    with np.load(path) as data:
        return {key: data[key] for key in data.files}


def render_cloud(path, key):
    points = load_points(path)
    if not len(points.get("X", [])):
        st.info("La selezione non contiene punti.")
        return
    color_options = ["RGB", "Quota Z", "Intensità", "Classificazione"]
    color_options += [d for d in points if d not in {"X", "Y", "Z", "Red", "Green", "Blue", "Intensity", "Classification"}]
    a, b, c = st.columns([2, 2, 1])
    color = a.selectbox("Colora per", color_options, index=0 if np.any(points.get("Red", [])) else 1, key=f"color-{key}")
    point_size = b.slider("Dimensione punti", 1.0, 5.0, 1.5, .5, key=f"size-{key}")
    overhead = c.toggle("Dall’alto", key=f"top-{key}")
    st.plotly_chart(point_figure(points, color, point_size, overhead), width="stretch",
                    key=f"cloud-{key}", config={"displaylogo": False, "scrollZoom": False})
    st.caption(f"{len(points['X']):,} punti in anteprima. Trascina per ruotare; usa i controlli del grafico per lo zoom. Le coordinate originali si leggono passando sui punti.")


def acquisition_issues(catalog, assets):
    for asset in assets:
        if asset["status"] == "FAILED":
            with st.container(border=True):
                st.error(f'{asset["name"]}: {asset["error"]}')
                if st.button("Riprova acquisizione", key=f'retry-{asset["id"]}'):
                    catalog.retry(asset["id"])
                    st.rerun()
        if asset["status"] == "CONVERSION":
            with st.expander(f'{asset["name"]} · serve l’esportazione E57', expanded=True):
                st.warning("Apri il progetto originale in ReCap Pro, con tutti i suoi RCS, ed esporta in E57. PDAL non legge direttamente RCP/RCS.")
                path = st.text_input("Percorso dell’E57 esportato", key=f'converted-{asset["id"]}')
                if st.button("Acquisisci E57 collegato", key=f'link-{asset["id"]}'):
                    try:
                        batch = catalog.enqueue(asset["project_id"], [Path(path)], f'Esportazione di {asset["name"]}',
                                                {"reference_confirmed": False, "source_type": "ReCap"}, parent_id=asset["id"])
                        go(2, restoration_batch=batch, preview_asset=None)
                    except (CloudError, OSError) as exc:
                        st.error(str(exc))


def preview_page(catalog, project_id, batch_id, assets):
    if any(a["status"] in {"QUEUED", "RUNNING"} for a in assets):
        landing_anchor()
        acquisition_progress(catalog, project_id, batch_id)
        with st.container(key="workflow_actions_2", border=True):
            previous_button(2)
        return
    effective = effective_assets(catalog, assets)
    if not effective:
        landing_anchor()
        st.info("Non ci sono ancora nuvole visualizzabili in questo dataset.")
        acquisition_issues(catalog, assets)
        if st.button("← Scegli un altro dataset"):
            go(1)
        return
    st.subheader("Esplora il rilievo")
    if any(a["status"] == "DUPLICATE" for a in assets):
        st.info("Alcuni file erano già acquisiti: stai visualizzando le nuvole già conservate.")
    labels = {a["id"]: a["name"] for a in effective}
    if st.session_state.get("preview_asset") not in labels:
        st.session_state["preview_asset"] = effective[0]["id"]
    landing_anchor()
    asset_id = st.selectbox("Nuvola da visualizzare", list(labels), format_func=labels.get, key="preview_asset")
    asset = next(a for a in effective if a["id"] == asset_id)
    runs = [r for r in runs_for(catalog, project_id, assets) if r["asset_id"] == asset_id and r["status"] == "DONE" and r.get("preview_path")]
    versions = {"original": "Originale acquisito", **{r["id"]: f'Risultato PDAL · {r["created"][:19]}' for r in runs}}
    if st.session_state.get("compare_run") not in versions:
        st.session_state["compare_run"] = "original"
    version = st.radio("Versione", list(versions), format_func=versions.get, horizontal=True, key="compare_run")
    path = asset["preview_path"] if version == "original" else next(r["preview_path"] for r in runs if r["id"] == version)
    metadata = asset["metadata"]
    declaration = metadata.get("declaration", {})
    reference = metadata.get("file_crs") or declaration.get("reference") or "riferimento da verificare"
    st.caption(f'{metadata["points"]:,} punti nell’originale · {reference}')
    if path and Path(path).is_file():
        render_cloud(path, f"{asset_id}-{version}")
    else:
        st.warning("File di anteprima non disponibile. Consulta il riepilogo.")
    if asset["status"] == "REVIEW":
        st.caption("La nuvola è leggibile. Il riferimento spaziale va completato nel Riepilogo prima della consegna finale.")
    with st.expander("Altri esiti dell’acquisizione"):
        acquisition_issues(catalog, assets)
        if st.button("Vedi i risultati disponibili", width="stretch"):
            go(4)
    with st.container(key="workflow_actions_2", border=True):
        st.caption("Prossimo passo: scegli come preparare i dati.")
        back, forward = st.columns(2)
        with back:
            previous_button(2)
        if forward.button("Continua: prepara i dati →", type="primary", width="stretch"):
            go(3)


@st.cache_data(ttl=300)
def stage_options(name):
    return run_pdal(["--options", name], timeout=30)


def pdal_page(catalog, project_id, assets):
    effective = effective_assets(catalog, assets)
    if not effective:
        landing_anchor()
        st.info("Acquisisci un dataset leggibile per usare gli strumenti PDAL.")
        if st.button("Vai all’acquisizione →"):
            go(1)
        return
    st.subheader("Scegli come preparare le nuvole")
    st.caption("PDAL lavora sull’intera nuvola e salva un nuovo file. L’originale acquisito rimane disponibile per il confronto.")
    st.caption("Per capire un’impostazione, clicca sulla sua icona informativa. Le spiegazioni si aprono senza modificare i valori.")
    labels = {a["id"]: a["name"] for a in effective}
    landing_anchor()
    selected = st.multiselect("Nuvole da elaborare", list(labels), default=list(labels), format_func=labels.get, help=HELP["clouds"])
    profile = st.selectbox("Cosa vuoi fare?", ["Controllo e conversione", "Pulizia leggera", "Nuvola più leggera", "Analisi delle superfici"], help=HELP["profile"])
    description = {"Controllo e conversione": "Conserva i punti e converte il formato di lavoro.",
                   "Pulizia leggera": "Individua e rimuove i punti isolati con un filtro statistico.",
                   "Nuvola più leggera": "Riduce i punti mantenendo un punto vicino al centro di ciascuna cella.",
                   "Analisi delle superfici": "Calcola normali e curvatura per lo studio delle superfici."}
    st.info(description[profile] + " Le impostazioni sono già pronte; puoi personalizzarle nelle sezioni facoltative.")
    inventory = drivers()
    bounds = [a["metadata"]["bounds"]["Z"] for a in effective]
    with st.form(f"pdal-settings-{profile}"):
        with st.expander("Personalizza pulizia e quantità di punti"):
            outlier = st.checkbox("Rimuovi punti isolati", value=profile == "Pulizia leggera", help=HELP["outlier"])
            a, b = st.columns(2)
            mean_k = a.number_input("Vicini per il filtro statistico", min_value=2, max_value=128, value=16, help=HELP["mean_k"])
            multiplier = b.number_input("Tolleranza del filtro", min_value=.1, max_value=10.0, value=2.0, step=.1, help=HELP["multiplier"])
            reduction = st.selectbox("Riduzione dei punti", ["Nessuna", "Per celle 3D (voxel)", "Un punto ogni N"], index=1 if profile == "Nuvola più leggera" else 0, help=HELP["reduction"])
            a, b = st.columns(2)
            voxel = a.number_input("Lato della cella · unità della sorgente", min_value=.001, max_value=100.0, value=.03, step=.01, format="%.3f", help=HELP["voxel"])
            stride = b.number_input("N · passo di campionamento", min_value=2, max_value=10000, value=2, help=HELP["stride"])
        with st.expander("Facoltativo · Seleziona una parte o analizza le superfici"):
            crop = st.checkbox("Conserva solo un intervallo di quota Z", help=HELP["crop"])
            a, b = st.columns(2)
            z_min = a.number_input("Quota minima", value=float(min(b[0] for b in bounds)), help=HELP["z_min"])
            z_max = b.number_input("Quota massima", value=float(max(b[1] for b in bounds)), help=HELP["z_max"])
            normals = st.checkbox("Calcola normali e curvatura", value=profile == "Analisi delle superfici", help=HELP["normals"])
            knn = st.number_input("Vicini per le normali", min_value=3, max_value=128, value=16, help=HELP["knn"])
        with st.expander("Avanzato · Cambia il sistema di coordinate"):
            input_crs = st.text_input("CRS sorgente, solo se non presente", placeholder="EPSG:32632", help=HELP["input_crs"])
            output_crs = st.text_input("CRS di destinazione", placeholder="EPSG:32633", help=HELP["output_crs"])
        with st.expander("Avanzato · Filtri personalizzati (JSON)"):
            st.caption("Aggiungi filtri PDAL alla pipeline. Lettori, scrittori e percorsi sono gestiti dall’app; l’elenco sotto indica i filtri eseguibili in questo pannello.")
            st.write(", ".join(inventory.get("advanced_filters", [])))
            extra_text = st.text_area("Filtri aggiuntivi", value="[]", height=130,
                                      help=HELP["extra"])
        output_format = st.selectbox("Formato del risultato", inventory.get("output_formats", ["laz", "las"]), format_func=str.upper, help=HELP["format"])
        with st.container(key="workflow_actions_3", border=True):
            st.caption("Prossimo passo: elabora le nuvole e consulta i risultati.")
            back, forward = st.columns(2)
            previous = back.form_submit_button("← Passaggio precedente", width="stretch")
            submitted = forward.form_submit_button("Elabora e vedi i risultati →", type="primary", width="stretch", disabled=not selected)
    if previous:
        go(2)
    if submitted:
        try:
            settings = {"z_range": [z_min, z_max] if crop else None,
                        "reduction": {"Nessuna": "none", "Per celle 3D (voxel)": "voxel", "Un punto ogni N": "decimation"}[reduction],
                        "decimation_step": int(stride), "voxel_size": voxel, "outlier": outlier,
                        "outlier_mean_k": int(mean_k), "outlier_multiplier": multiplier,
                        "normals": normals, "normal_knn": int(knn), "input_crs": input_crs,
                        "output_crs": output_crs, "output_format": output_format,
                        "extra_filters": json.loads(extra_text), "nonstream_point_limit": 2_000_000}
            run_ids = workbench.enqueue_run(catalog, selected, settings)
            go(4, recent_runs=run_ids)
        except (CloudError, ValueError, OSError) as exc:
            st.error(f"Controlla i parametri: {exc}")
    with st.expander("Esplora tutte le funzionalità PDAL installate"):
        st.caption("Il catalogo riflette i driver presenti su questo computer. I controlli guidati coprono le operazioni di ingestion più comuni; gli altri driver si consultano qui e si integrano in pipeline dedicate.")
        rows = inventory.get("drivers", [])
        query = st.text_input("Cerca un driver", placeholder="normal, crop, copc, smrf…", help=HELP["search"])
        matching = [r for r in rows if query.lower() in (r["name"] + r.get("description", "")).lower()]
        if matching:
            stage = st.selectbox("Driver PDAL", [r["name"] for r in matching], help=HELP["driver"])
            row = next(r for r in matching if r["name"] == stage)
            st.write(row.get("description", ""))
            if st.button("Mostra i parametri del driver"):
                try:
                    st.code(stage_options(stage), language="text")
                except CloudError as exc:
                    st.warning(str(exc))
            st.markdown(f"[Documentazione PDAL: {stage}](https://pdal.io/en/latest/stages/{stage}.html)")
        else:
            st.info("Nessun driver corrisponde alla ricerca.")


def complete_reference(catalog, asset):
    meta = asset["metadata"]
    with st.expander(f'Completa riferimento e unità · {asset["name"]}'):
        with st.form(f'reference-{asset["id"]}'):
            ref = st.text_input("Riferimento della nuvola", value=meta.get("file_crs") or asset["context"]["project_reference"])
            units = st.selectbox("Unità delle coordinate", ["m", "mm", "ft", "degree"])
            note = st.text_input("Nota di verifica", placeholder="Riferimento verificato con il fornitore…")
            if st.form_submit_button("Conferma i dati"):
                try:
                    catalog.confirm_reference(asset["id"], ref, units, note)
                    st.rerun()
                except CloudError as exc:
                    st.error(str(exc))


@st.fragment(run_every="3s")
def processing_progress(catalog, project_id, batch_id):
    assets = batch_assets(catalog, project_id, batch_id)
    pending = [r for r in runs_for(catalog, project_id, assets) if r["status"] in {"QUEUED", "RUNNING"}]
    if not pending:
        st.rerun(scope="app")
    st.info("PDAL sta elaborando le nuvole. Il riepilogo si aggiornerà al termine.")
    for run in pending[:10]:
        st.progress(min(1.0, max(0.0, float(run.get("progress", 0))/100)), text=f'{run["name"]} · {run["phase"]}')


def result_downloads(run):
    a, b = st.columns(2)
    for column, field, label, filename in ((a, "report_path", "Scarica report PDAL", "report.json"),
                                          (b, "pipeline_path", "Scarica pipeline", "pipeline.json")):
        path = Path(run[field]) if run.get(field) else None
        if path and path.is_file():
            column.download_button(label, path.read_bytes(), filename, mime="application/json", key=f'{field}-{run["id"]}')
    if run.get("output_path"):
        path = Path(run["output_path"])
        if path.is_file():
            st.caption(f"File elaborato: {size_label(path.stat().st_size)}")
            if path.stat().st_size <= 100 * 1024**2:
                if st.checkbox("Prepara il download della nuvola", key=f'download-{run["id"]}'):
                    st.download_button("Scarica la nuvola elaborata", path.read_bytes(), path.name, key=f'cloud-download-{run["id"]}')
            else:
                st.caption("Per questo file grande usa il percorso sul computer, senza trasferirlo nella memoria del browser.")
            with st.expander("Percorso del risultato sul computer"):
                st.code(str(path), language="text")


def summary_page(catalog, project_id, batch_id, assets):
    st.subheader("I tuoi risultati")
    landing_anchor()
    effective = effective_assets(catalog, assets)
    counts = {state: sum(a["status"] == state for a in assets) for state in LABELS}
    a, b, c = st.columns(3)
    a.metric("File ricevuti", len(assets))
    b.metric("Nuvole visualizzabili", len(effective))
    c.metric("Punti originali", f'{sum(a["metadata"].get("points", 0) for a in effective):,}')
    st.dataframe([{"File": a["name"], "Formato": a["suffix"][1:].upper(), "Esito": LABELS[a["status"]],
                   "Dimensione": size_label(a["size"]), "Punti": a["metadata"].get("points"),
                   "Riferimento": a["metadata"].get("file_crs") or ("Locale dichiarato" if a["metadata"].get("declaration") else "Da verificare")}
                  for a in assets], hide_index=True, width="stretch")
    if counts["QUEUED"] + counts["RUNNING"]:
        st.info("Alcuni file sono ancora in acquisizione.")
        if st.button("Segui l’acquisizione →"):
            go(2)
    acquisition_issues(catalog, assets)
    for asset in effective:
        if asset["status"] == "REVIEW":
            complete_reference(catalog, asset)
    runs = runs_for(catalog, project_id, assets)
    st.markdown("### Elaborazioni PDAL")
    if any(r["status"] in {"QUEUED", "RUNNING"} for r in runs):
        processing_progress(catalog, project_id, batch_id)
    if not runs:
        st.info("PDAL ha verificato gli originali. Non hai ancora richiesto filtri o conversioni aggiuntive.")
        if st.button("Scegli gli strumenti PDAL →", type="primary", width="stretch", disabled=not effective):
            go(3)
    else:
        statuses = {"QUEUED": "In coda", "RUNNING": "In elaborazione", "DONE": "Completata", "FAILED": "Da correggere"}
        labels = {r["id"]: f'{r["name"]} · {statuses.get(r["status"], r["status"])} · {r["created"][:19]}' for r in runs}
        run_id = st.selectbox("Elaborazione da esaminare", list(labels), format_func=labels.get)
        run = workbench.get_run(catalog, run_id)
        if run["status"] == "FAILED":
            st.error(run["error"])
            if st.button("Correggi i parametri e riprova"):
                go(3)
        elif run["status"] == "DONE":
            report = run.get("report", {})
            for warning in report.get("warnings", []):
                st.warning(warning)
            before, after = report.get("points_input"), report.get("points_output")
            a, b, c = st.columns(3)
            a.metric("Punti prima", f"{before:,}" if before is not None else "—")
            b.metric("Punti dopo", f"{after:,}" if after is not None else "—")
            c.metric("Riduzione", f"{(1-after/before)*100:.1f}%" if before and after is not None else "—")
            st.success("Elaborazione completata. Il risultato e l’originale sono conservati separatamente.")
            if st.button("Confronta il risultato in 3D →", type="primary", width="stretch"):
                go(2, preview_asset=run["asset_id"], compare_run=run["id"])
            result_downloads(run)
            with st.expander("Parametri, statistiche e dettagli PDAL"):
                st.json(report)
    report = {"schema_version": "restoration.ingestion/1", "project": catalog.project(project_id), "batch_id": batch_id,
              "assets": assets, "processing_runs": runs}
    st.divider()
    st.download_button("Scarica riepilogo completo JSON", json.dumps(report, indent=2, ensure_ascii=False), "riepilogo_ingestion.json", mime="application/json")
    ready_ids = [a["id"] for a in effective if a["status"] == "READY"]
    with st.expander("Consegna dei dati documentati alla fase successiva"):
        st.caption("Il manifest include gli originali pronti. Il riepilogo completo include anche i risultati delle elaborazioni PDAL.")
        if st.button("Crea manifest degli originali pronti", disabled=not ready_ids):
            try:
                path = catalog.delivery(project_id, ready_ids)
                st.download_button("Scarica manifest", path.read_bytes(), path.name, mime="application/json")
            except CloudError as exc:
                st.error(str(exc))


def main():
    st.set_page_config(page_title="SCAN TO BIM", page_icon="◈", layout="wide", initial_sidebar_state="collapsed")
    st.markdown("""<style>
    .block-container {padding-top:3.4rem;max-width:1400px;}
    h1 {letter-spacing:-.05em;}
    [data-testid="stMetric"] {background:#151e2c;border:1px solid #2a3748;border-radius:12px;padding:14px;}
    .st-key-folder_choices [data-testid="stButton"] button {max-width:none;justify-content:flex-start;min-height:44px;}
    .step-current {font-size:14px;letter-spacing:.1em;color:#5ee0bc;font-weight:800;margin-top:16px;}
    .st-key-workflow_navigation {background:#141e2c;border:1px solid #2a3748;border-radius:12px;padding:12px;}
    .st-key-workflow_navigation [role="radiogroup"] {display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;}
    .st-key-workflow_navigation [data-testid="stRadioOption"] {padding:8px;border-radius:8px;min-height:44px;}
    .st-key-workflow_navigation [data-selected="true"] {background:#233e34;}
    @media (max-width:700px) {
        .st-key-workflow_navigation [role="radiogroup"] {grid-template-columns:repeat(2,minmax(0,1fr));}
    }
    button[aria-label^="Help for "] {width:28px;height:28px;display:inline-grid;place-items:center;color:#a4cbbd;}
    button[aria-label^="Help for "] svg {display:none;}
    button[aria-label^="Help for "]::before {
        content:"i";display:grid;place-items:center;width:18px;height:18px;
        border:1.5px solid currentColor;border-radius:50%;font:700 14px Georgia,serif;
    }
    [data-testid="stButton"], [data-testid="stFormSubmitButton"], [data-testid="stDownloadButton"] {width:100%;}
    [data-testid="stButton"] button,
    [data-testid="stFormSubmitButton"] button,
    [data-testid="stDownloadButton"] button {
        box-sizing:border-box;width:100%;max-width:360px;min-height:56px;
        padding:12px 18px;border-radius:12px;
    }
    [data-testid="stButton"] button p,
    [data-testid="stFormSubmitButton"] button p,
    [data-testid="stDownloadButton"] button p {font-size:1rem;line-height:1.4;font-weight:650;}
    [class*="st-key-workflow_actions_"] {background:#131f29;border-radius:12px;}
    [class*="st-key-workflow_actions_"] [data-testid="stColumn"]:last-child [data-testid="stButton"],
    [class*="st-key-workflow_actions_"] [data-testid="stColumn"]:last-child [data-testid="stFormSubmitButton"] {
        display:flex;justify-content:flex-end;
    }
    @media (max-width:640px) {
        [class*="st-key-workflow_actions_"] [data-testid="stButton"] button,
        [class*="st-key-workflow_actions_"] [data-testid="stFormSubmitButton"] button {max-width:none;}
    }

    [role="tooltip"] {width:min(440px,calc(100vw - 32px)) !important;}
    [data-testid="stTooltipContent"] {
        box-sizing:border-box;width:100%;max-width:100%;
        max-height:min(360px,45vh);overflow-y:auto;overflow-wrap:anywhere;
    }
    [data-testid="stTooltipContent"] p {white-space:normal;}
    button[data-testid="stBaseButton-primary"],
    button[data-testid="stBaseButton-primaryFormSubmit"] {
        min-height:56px;padding:12px 18px;border-radius:12px;
        background:#5ef1a6;color:#062b1c;border:2px solid #a2ffd0;
        box-shadow:0 5px 20px #5ef1a640;transition:background .15s,box-shadow .15s;
    }
    button[data-testid="stBaseButton-primary"] p,
    button[data-testid="stBaseButton-primaryFormSubmit"] p {
        color:inherit;font-size:1rem;font-weight:650;line-height:1.4;
    }
    button[data-testid="stBaseButton-primary"]:hover:not(:disabled),
    button[data-testid="stBaseButton-primaryFormSubmit"]:hover:not(:disabled) {
        background:#91ffc2;border-color:#c9ffe2;color:#062b1c;
        box-shadow:0 5px 26px #5ef1a670;
    }
    button[data-testid="stBaseButton-primary"]:focus-visible,
    button[data-testid="stBaseButton-primaryFormSubmit"]:focus-visible {
        outline:3px solid #ffffff;outline-offset:4px;
    }
    button[data-testid="stBaseButton-primary"]:disabled,
    button[data-testid="stBaseButton-primaryFormSubmit"]:disabled {
        background:#253a32;border-color:#40584b;color:#a4b5ac;box-shadow:none;
    }
    @media (prefers-reduced-motion:reduce) {
        button[data-testid="stBaseButton-primary"],
        button[data-testid="stBaseButton-primaryFormSubmit"] {transition:none;}
    }
    </style>""", unsafe_allow_html=True)
    background = os.environ.get("CLOUDLAB_WORKER", "1") != "0"
    catalog, worker, project_id = service(str(Path(os.environ.get("CLOUDLAB_STORAGE", ROOT / "storage")).resolve()), background)
    cloud = os.environ.get("CLOUDLAB_CLOUD") == "1"
    if cloud:
        if "cloud_project" not in st.session_state:
            st.session_state["cloud_project"] = catalog.create_project(
                "Sessione " + uuid4().hex, "Coordinate locali: origine propria del dataset", units="m")
        project_id = st.session_state["cloud_project"]
    if background:
        worker.start()
    for key, value in st.session_state.pop("restoration_navigation", {}).items():
        st.session_state[key] = value
    st.title("SCAN TO BIM")
    if cloud:
        st.info("Demo online · Scarica i risultati prima di uscire. Ricaricando la pagina puoi perdere l’accesso alla sessione; i dati non sono un archivio permanente.")
    st.caption("Prepara le tue nuvole di punti in 4 semplici passaggi. Segui il pulsante verde per continuare.")
    batches = catalog.rows("SELECT id,name FROM batches WHERE project_id=? ORDER BY created DESC,rowid DESC", (project_id,))
    batch_labels = {b["id"]: b["name"] for b in batches}
    if st.session_state.get("restoration_batch") not in batch_labels:
        st.session_state["restoration_batch"] = batches[0]["id"] if batches else None
    previous_step = st.session_state.get("restoration_step")
    if previous_step in OLD_STEPS:
        st.session_state["restoration_step"] = STEPS[OLD_STEPS.index(previous_step)]
    elif previous_step not in STEPS:
        st.session_state["restoration_step"] = STEPS[0]
    with st.container(key="workflow_navigation"):
        step = st.radio("I quattro passaggi", STEPS, horizontal=True, key="restoration_step", label_visibility="collapsed", on_change=request_scroll)
    step_number = STEPS.index(step) + 1
    st.markdown(f'<div class="step-current">PASSAGGIO {step_number} DI 4</div>', unsafe_allow_html=True)
    st.write("Carica i file dal computer o scegli un esempio, poi premi il pulsante verde." if cloud and step_number == 1 else STEP_HINTS[step_number - 1])
    batch_id = st.session_state.get("restoration_batch")
    assets = batch_assets(catalog, project_id, batch_id)
    if step == STEPS[0]:
        source_page(catalog, project_id, batch_id)
    elif not assets:
        landing_anchor()
        st.info("Scegli prima una cartella locale o un esempio di restauro.")
        if st.button("Scegli il dataset →", type="primary", width="stretch"):
            go(1)
    elif step == STEPS[1]:
        preview_page(catalog, project_id, batch_id, assets)
    elif step == STEPS[2]:
        pdal_page(catalog, project_id, assets)
    else:
        summary_page(catalog, project_id, batch_id, assets)
    if step_number == 4:
        with st.container(key="workflow_actions_4", border=True):
            back, forward = st.columns(2)
            with back:
                previous_button(4)
            if forward.button("Carica un altro dataset →", type="primary", width="stretch"):
                go(1)
    apply_navigation_scroll()
