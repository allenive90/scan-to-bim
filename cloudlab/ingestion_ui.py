from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
from datetime import datetime

import numpy as np
import streamlit as st

from cloudlab.catalog import Catalog, LABELS, discover, uid
from cloudlab.engine import ROOT, CloudError, capabilities
from cloudlab.viewer import point_figure
from cloudlab.worker import Worker


def size_label(value):
    value = float(value or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024


@st.cache_resource
def service(storage, background=True):
    catalog = Catalog(storage)
    if not catalog.projects():
        project_id = catalog.create_project("Campus · dimostrazione", "Sistema locale del rilievo sintetico", units="m")
        paths = [ROOT / "data/samples" / name for name in ("campus.e57", "terrain.laz", "tunnel.las")]
        if all(path.exists() for path in paths):
            catalog.enqueue(project_id, paths, "Lotto dimostrativo · 3 rilievi", {
                "building": "Campus", "level": "Più livelli", "supplier": "Generatore sintetico PDAL",
                "source_type": "demo", "reference_confirmed": True,
                "notes": "Geometrie sintetiche già note in metri, riferimento locale; non scansioni reali."})
    worker = Worker(catalog)
    if background:
        worker.start()
    return catalog, worker


@st.cache_data(ttl=120)
def runtime():
    try:
        return capabilities()
    except CloudError as exc:
        return {"error": str(exc)}


def display_table(rows):
    if not rows:
        st.info("Nessun rilievo corrisponde ai filtri.")
        return
    st.dataframe([{"File": r["name"], "Formato": r["suffix"][1:].upper(), "Lotto": r["batch_name"],
                   "Dimensione": size_label(r["size"]), "Stato": LABELS[r["status"]],
                   "Ricevuto (UTC)": r["created"].replace("T", " ")[:19]} for r in rows],
                 hide_index=True, width="stretch")


def navigate(section, **state):
    """Apply navigation on the next full run, before widgets are instantiated."""
    st.session_state["navigation_request"] = {"section": section, **state}
    st.rerun(scope="app")


def open_batch(project_id, batch_id):
    navigate("Nuovo lotto", active_batch={"project_id": project_id, "batch_id": batch_id})


@st.fragment(run_every="3s")
def batch_progress(catalog, project_id, batch_id):
    batches = catalog.rows("SELECT * FROM batches WHERE id=? AND project_id=?", (batch_id, project_id))
    if not batches:
        st.warning("Questo lotto non è disponibile nel progetto selezionato.")
        return
    rows = catalog.rows("""SELECT a.*,b.name batch_name,j.phase,j.progress FROM assets a
        JOIN batches b ON b.id=a.batch_id JOIN jobs j ON j.asset_id=a.id
        WHERE a.batch_id=? AND a.project_id=? ORDER BY a.rowid""", (batch_id, project_id))
    groups = {state: [r for r in rows if r["status"] == state] for state in LABELS}
    pending = groups["QUEUED"] + groups["RUNNING"]
    done, total = len(rows) - len(pending), len(rows)
    st.subheader("Stiamo acquisendo i tuoi file" if pending else "Verifica del lotto terminata")
    st.write(batches[0]["name"])
    if pending:
        st.info("Non devi premere altro. I file vengono archiviati e controllati automaticamente.")
        st.progress((done + sum(r["progress"]/100 for r in groups["RUNNING"])) / max(total, 1),
                    text=f"{done} di {total} file verificati")
        if groups["RUNNING"]:
            current = groups["RUNNING"][0]
            st.caption(f'In corso: {current["name"]} · {current["phase"]}')
        st.caption("Puoi lasciare questa pagina: il lavoro continua finché il servizio è acceso. L’esito si aggiorna automaticamente.")
    else:
        st.progress(1.0, text=f"{total} di {total} file controllati")
    ready_ids = [r["id"] for r in groups["READY"]]
    actionable = groups["REVIEW"] + groups["FAILED"] + groups["CONVERSION"]
    if actionable:
        st.markdown("#### Il prossimo passo")
    if groups["REVIEW"]:
        count = len(groups["REVIEW"])
        st.warning(f"{'Un rilievo richiede' if count == 1 else f'{count} rilievi richiedono'} riferimento e unità: completa questi dati prima della consegna.")
        if st.button("Completa i dati →", type="primary", key=f"review-{batch_id}"):
            navigate("Catalogo rilievi", catalog_batch=batch_id, catalog_state="REVIEW", catalog_search="", catalog_page=1)
    if groups["FAILED"]:
        st.error(f'{len(groups["FAILED"])} file non sono stati acquisiti correttamente.')
        if st.button("Vedi gli errori e riprova", key=f"errors-{batch_id}"):
            navigate("Catalogo rilievi", catalog_batch=batch_id, catalog_state="FAILED", catalog_search="", catalog_page=1)
    if groups["CONVERSION"]:
        st.warning("I file RCP/RCS richiedono l’esportazione in E57 da ReCap.")
        if st.button("Apri i file da convertire", key=f"convert-{batch_id}"):
            navigate("Catalogo rilievi", catalog_batch=batch_id, catalog_state="CONVERSION", catalog_search="", catalog_page=1)
    if ready_ids:
        st.success(f"{'Un rilievo pronto' if len(ready_ids) == 1 else f'{len(ready_ids)} rilievi pronti'}. Puoi preparare la consegna al preprocessing.")
        if st.button("Prepara la consegna →", type="primary" if not actionable else "secondary", key=f"ready-{batch_id}"):
            navigate("Consegna a PDAL", handoff_preset=ready_ids)
    if groups["DUPLICATE"]:
        st.info(f'{"Questo file era già presente" if len(groups["DUPLICATE"]) == 1 else str(len(groups["DUPLICATE"])) + " file erano già presenti"}. Non occorre ripetere l’acquisizione.')
        if st.button("Apri i rilievi già presenti", key=f"duplicate-{batch_id}"):
            original = catalog.asset(groups["DUPLICATE"][0]["duplicate_of"])
            navigate("Catalogo rilievi", catalog_batch="", catalog_state="", catalog_search=original["name"],
                     catalog_page=1, catalog_selected=original["id"])
    with st.expander(f"Dettaglio dei {total} file", expanded=bool(pending)):
        st.dataframe([{"File": r["name"], "Stato": LABELS[r["status"]]} for r in rows], hide_index=True, width="stretch")
    st.divider()
    a, b = st.columns(2)
    if a.button("Apri tutti i file del lotto", key=f"all-{batch_id}"):
        navigate("Catalogo rilievi", catalog_batch=batch_id, catalog_state="", catalog_search="", catalog_page=1)
    if b.button("Acquisisci un altro lotto", key=f"new-{batch_id}"):
        navigate("Nuovo lotto", active_batch=None, batch_form_version=st.session_state.get("batch_form_version", 0) + 1)


@st.fragment(run_every="3s")
def overview(catalog, project_id):
    recent = catalog.rows("SELECT id,name FROM batches WHERE project_id=? ORDER BY created DESC,rowid DESC LIMIT 1", (project_id,))
    if recent:
        if st.button(f'Vedi esito ultimo lotto · {recent[0]["name"]}', type="primary"):
            open_batch(project_id, recent[0]["id"])
    else:
        if st.button("Acquisisci il primo lotto →", type="primary"):
            navigate("Nuovo lotto", active_batch=None)
    counts = {r["status"]: r for r in catalog.counts(project_id)}
    total = sum(r["count"] for r in counts.values())
    ready = counts.get("READY", {}).get("count", 0)
    attention = sum(counts.get(s, {}).get("count", 0) for s in ("REVIEW", "FAILED", "CONVERSION"))
    volume = sum(r["bytes"] for key, r in counts.items() if key != "DUPLICATE")
    a, b, c, d = st.columns(4)
    a.metric("Rilievi ricevuti", total)
    b.metric("Pronti per PDAL", ready)
    c.metric("Da verificare", attention)
    d.metric("Volume acquisito", size_label(volume))
    st.markdown("### Il dataset, prima della modellazione")
    a, b, c = st.columns(3)
    with a, st.container(border=True):
        st.markdown("**01 / Acquisisci**")
        st.caption("Lotti da cartelle locali e NAS montati. Originali conservati e identificati con SHA-256.")
    with b, st.container(border=True):
        st.markdown("**02 / Verifica**")
        st.caption("Lettura completa con PDAL, metadati, controllo duplicati e riferimento spaziale.")
    with c, st.container(border=True):
        st.markdown("**03 / Consegna**")
        st.caption("Manifest dei soli rilievi pronti, con provenienza e riferimenti agli originali per il preprocessing.")
    pending = sum(counts.get(s, {}).get("count", 0) for s in ("QUEUED", "RUNNING"))
    if pending:
        st.info(f"{pending} rilievi in coda o in lavorazione. Puoi continuare a usare l’interfaccia.")
    st.markdown("### Ultimi ingressi")
    display_table(catalog.assets(project_id, limit=8))
    st.caption("Volume esclusi gli ingressi marcati come duplicati. Aggiornamento ogni 3 secondi. Pronto significa acquisito e documentato: allineamento, pulizia e modellazione appartengono alle fasi successive.")


def stage_uploads(catalog, uploaded):
    if sum(f.size for f in uploaded) > 500 * 1024**2:
        raise CloudError("Il lotto browser supera 500 MB. Usa l’importazione da cartella per i dataset grandi.")
    folder = catalog.root / "inbox" / uid()
    folder.mkdir()
    paths = []
    try:
        for index, item in enumerate(uploaded):
            if item.size > 200 * 1024**2:
                raise CloudError("Massimo 200 MB per file nel browser.")
            subfolder = folder / str(index)
            subfolder.mkdir()
            # Preserve the display name while preventing uploaded path traversal.
            path = subfolder / Path(item.name.replace("\\", "/")).name
            item.seek(0)
            with path.open("wb") as destination:
                shutil.copyfileobj(item, destination, length=8 * 1024**2)
            paths.append(path)
        return paths
    except Exception:
        shutil.rmtree(folder)
        raise


def new_batch(catalog, project):
    active = st.session_state.get("active_batch")
    if active and active["project_id"] == project["id"]:
        batch_progress(catalog, project["id"], active["batch_id"])
        return
    st.markdown("### 1. Scegli i file")
    st.caption("Seleziona le nuvole, poi premi Acquisisci lotto. Ti mostreremo automaticamente l’avanzamento e il prossimo passo.")
    recent = catalog.rows("SELECT id,name FROM batches WHERE project_id=? ORDER BY created DESC,rowid DESC LIMIT 1", (project["id"],))
    if recent and st.button(f'Riprendi ultimo lotto · {recent[0]["name"]}'):
        open_batch(project["id"], recent[0]["id"])
    version = st.session_state.get("batch_form_version", 0)
    mode = st.radio("Dove sono i file?", ["Cartella / NAS", "Upload multiplo", "Esempi sintetici"], horizontal=True, key=f"source-mode-{version}")
    paths, uploads = [], []
    if mode == "Cartella / NAS":
        st.info("Per dataset grandi indica un percorso accessibile al server. Un NAS deve essere già montato; i file non transitano dal browser.")
        location = st.text_input("Percorso della cartella", placeholder="/Volumes/Rilievi/Edificio_A/2026-09-15", key=f"folder-{version}")
        recursive = st.checkbox("Includi sottocartelle", value=True)
        if st.button("Esamina cartella"):
            try:
                candidates = discover(location, recursive, excluded=catalog.root) if location.strip() else []
                st.session_state["discovery"] = {"location": location, "recursive": recursive, "paths": [str(p) for p in candidates]}
            except (CloudError, OSError) as exc:
                st.error(str(exc))
        discovery = st.session_state.get("discovery", {})
        if discovery.get("location") == location and discovery.get("recursive") == recursive:
            paths = [Path(p) for p in discovery["paths"]]
            st.caption(f"{len(paths)} file compatibili trovati · E57, LAS, LAZ, RCP, RCS. Massimo 5.000 file per lotto.")
            if paths:
                st.dataframe({"File da acquisire": [str(p) for p in paths[:100]]}, hide_index=True, height=180)
                if len(paths) > 100:
                    st.caption("Mostrati i primi 100; il lotto includerà tutti i file trovati.")
    elif mode == "Upload multiplo":
        uploads = st.file_uploader("File del lotto", type=["e57", "las", "laz", "rcp", "rcs"], accept_multiple_files=True, key=f"uploads-{version}")
        st.caption("Fino a 200 MB per file e 500 MB complessivi. Per file più grandi usa Cartella / NAS.")
    else:
        options = sorted((ROOT / "data/samples").glob("*"))
        options = [p for p in options if p.suffix in (".e57", ".las", ".laz")]
        paths = st.multiselect("Nuvole da inserire nel lotto", options, format_func=lambda p: p.name,
                               default=[p for p in options if p.name == "campus.e57"], key=f"samples-{version}")
        st.caption("Le varianti di formato sono file distinti: il checksum riconosce duplicati identici byte per byte, non nuvole geometricamente equivalenti.")
    count = len(uploads) if mode == "Upload multiplo" else len(paths)
    st.markdown("### 2. Avvia l’acquisizione")
    with st.form(f"batch_metadata-{version}"):
        name = st.text_input("Nome lotto", value=f'Lotto {datetime.now():%d/%m/%Y %H:%M}', key=f"batch-name-{version}")
        with st.expander("Aggiungi edificio, data e altre informazioni (facoltativo)"):
            a, b = st.columns(2)
            building = a.text_input("Edificio / zona")
            level = b.text_input("Piano / settore")
            supplier = a.text_input("Fornitore / operatore")
            date = b.date_input("Data del rilievo", value=None)
            notes = st.text_area("Note di acquisizione", height=80)
        with st.expander("Conferma il riferimento spaziale (puoi farlo anche dopo)"):
            st.caption(f'{project["reference"]} · {project["crs"] or "locale, senza EPSG"} · unità {project["units"]}')
            confirmed = st.checkbox("Confermo riferimento e unità per questi file", value=False)
            st.caption("Se non lo confermi ora, ti guideremo nel completamento dopo l’acquisizione.")
        st.write(f"**{count} file selezionati**")
        submitted = st.form_submit_button("Acquisisci lotto →", type="primary", disabled=count == 0)
        if not count:
            st.caption("Per continuare, scegli almeno un file nel passaggio 1.")
    if submitted:
        staged = []
        try:
            if not name.strip():
                raise CloudError("Assegna un nome al lotto.")
            if mode == "Upload multiplo":
                staged = stage_uploads(catalog, uploads) if uploads else []
                paths = staged
            batch_id = catalog.enqueue(project["id"], paths, name, {"building": building, "level": level,
                "supplier": supplier, "survey_date": date.isoformat() if date else None, "notes": notes,
                "reference_confirmed": confirmed, "source_type": mode})
            open_batch(project["id"], batch_id)
        except (CloudError, OSError) as exc:
            for path in staged:
                path.unlink(missing_ok=True)
            st.error(str(exc))


def asset_detail(catalog, asset_id):
    asset = catalog.asset(asset_id)
    meta = asset["metadata"]
    st.divider()
    st.subheader(asset["name"])
    st.caption(f'{LABELS[asset["status"]]} · {asset["batch_name"]} · ID {asset["id"]}')
    details, preview_tab, history = st.tabs(["Scheda di ingestion", "Anteprima 3D", "Tracciabilità"])
    with details:
        if asset["status"] == "FAILED":
            st.error(asset["error"])
            if st.button("Riprova ingestion", key=f"retry-{asset_id}"):
                catalog.retry(asset_id)
                st.rerun()
        if asset["status"] == "DUPLICATE":
            st.info(f'Il contenuto è già presente: originale {asset["duplicate_of"]}. Questo ingresso è escluso dalla consegna.')
        if asset["status"] in ("QUEUED", "RUNNING"):
            st.info("La verifica è in corso. Aggiorna il catalogo per vedere i risultati.")
        if asset["status"] == "REVIEW":
            with st.form(f"reference-{asset_id}"):
                st.markdown("**Completa il riferimento della sorgente**")
                st.caption("Registra una dichiarazione verificata. Questa azione non modifica coordinate o CRS nell’originale; un CRS rilevato rimane sempre nei metadati.")
                ref = st.text_input("Riferimento della nuvola", value=meta.get("file_crs") or asset["context"]["project_reference"])
                unit_options = ["m", "mm", "ft", "degree"]
                unit_default = ("degree" if "degree" in (meta.get("file_units") or "").lower() else
                                "ft" if "foot" in (meta.get("file_units") or "").lower() else asset["context"]["project_units"])
                units = st.selectbox("Unità delle coordinate", unit_options, index=unit_options.index(unit_default))
                note = st.text_input("Nota di verifica", placeholder="Verificato con il fornitore / caposaldo / convenzione locale…")
                if st.form_submit_button("Conferma e rendi pronto", type="primary"):
                    try:
                        catalog.confirm_reference(asset_id, ref, units, note)
                        st.rerun()
                    except CloudError as exc:
                        st.error(str(exc))
        for warning in meta.get("warnings", []):
            st.warning(warning)
        if meta.get("points"):
            a, b, c = st.columns(3)
            a.metric("Punti verificati", f'{meta["points"]:,}'.replace(",", "."))
            b.metric("Anteprima", f'{meta["preview_points"]:,}'.replace(",", "."))
            c.metric("Originale", size_label(asset["size"]))
            st.write("**CRS rilevato nel file:**", meta.get("file_crs") or "Assente")
            if meta.get("declaration"):
                st.write("**Riferimento dichiarato:**", meta["declaration"])
            st.dataframe([{"Asse": k, "Minimo": v[0], "Massimo": v[1]} for k, v in meta["bounds"].items()], hide_index=True)
            st.caption("Attributi: " + ", ".join(meta["dimensions"]))
        if asset["status"] == "CONVERSION":
            st.markdown("**Associa l’E57 esportato da ReCap Pro**")
            st.caption("Conserva il progetto RCP insieme alle sue dipendenze. L’associazione registra la provenienza dichiarata; non verifica l’equivalenza geometrica.")
            converted = st.text_input("Percorso E57 sul server", key=f"converted-{asset_id}")
            if st.button("Acquisisci E57 collegato", key=f"link-{asset_id}"):
                try:
                    catalog.enqueue(asset["project_id"], [Path(converted)], f'Conversione · {asset["name"]}',
                                    {**asset["context"], "reference_confirmed": False, "source_type": "ReCap export"}, parent_id=asset_id)
                    st.success("E57 messo in coda e collegato all’originale Autodesk.")
                except (CloudError, OSError) as exc:
                    st.error(str(exc))
            children = catalog.rows("SELECT name,status FROM assets WHERE parent_id=?", (asset_id,))
            if children:
                st.dataframe(children, hide_index=True)
        with st.expander("Contesto del lotto"):
            st.json(asset["context"])
    with preview_tab:
        if asset["preview_path"] and Path(asset["preview_path"]).is_file():
            with np.load(asset["preview_path"]) as arrays:
                points = {key: arrays[key] for key in arrays.files}
            color = st.selectbox("Colore anteprima", ["Quota Z", "RGB", "Intensità", "Classificazione"])
            st.plotly_chart(point_figure(points, color, 1.5), width="stretch", config={"displaylogo": False, "scrollZoom": False})
            st.caption("Campione deterministico fino a 30.000 punti. Nessun filtro applicato all’originale. Assi relativi; coordinate sorgenti nell’hover.")
        else:
            st.info("L’anteprima sarà disponibile dopo la verifica di un E57, LAS o LAZ valido.")
    with history:
        st.write("**Origine registrata:**", asset["source"])
        st.write("**Originale conservato:**", asset["raw_path"] or "In attesa di archiviazione")
        st.write("**SHA-256:**", asset["sha256"] or "In calcolo")
        if asset["parent_id"]:
            st.write("**Originale Autodesk collegato:**", asset["parent_id"])
        st.dataframe(catalog.rows("SELECT created,action,detail FROM events WHERE asset_id=? ORDER BY id DESC", (asset_id,)), hide_index=True)
        st.download_button("Scarica scheda JSON", json.dumps(asset, indent=2, ensure_ascii=False), f"{asset_id}.json", mime="application/json")
        with st.expander("Metadati tecnici della verifica"):
            st.json(meta)


def library(catalog, project_id):
    st.markdown("### Catalogo dei rilievi")
    batches = catalog.rows("SELECT id,name FROM batches WHERE project_id=? ORDER BY created DESC,rowid DESC", (project_id,))
    batch_labels = {"": "Tutti i lotti", **{r["id"]: r["name"] for r in batches}}
    if st.session_state.get("catalog_batch", "") not in batch_labels:
        st.session_state["catalog_batch"] = ""
    batch_id = st.selectbox("Lotto", list(batch_labels), format_func=batch_labels.get, key="catalog_batch")
    if batch_id and st.button("← Torna all’esito del lotto"):
        open_batch(project_id, batch_id)
    a, b, c = st.columns([3, 2, 1])
    search = a.text_input("Cerca file", key="catalog_search")
    state = b.selectbox("Stato", [""] + list(LABELS), format_func=lambda s: LABELS.get(s, "Tutti gli stati"), key="catalog_state")
    page = c.number_input("Pagina", min_value=1, value=1, step=1, key="catalog_page")
    st.button("Aggiorna catalogo")
    rows = catalog.assets(project_id, search, state, limit=100, offset=(page-1)*100, batch_id=batch_id)
    if batch_id and state == "REVIEW" and not catalog.assets(project_id, state="REVIEW", batch_id=batch_id, limit=1):
        st.success("I dati di questo lotto sono completi. Puoi procedere alla consegna.")
        ready_ids = [r["id"] for r in catalog.assets(project_id, state="READY", batch_id=batch_id, limit=5000)]
        if ready_ids and st.button("Continua con i rilievi pronti →", type="primary"):
            navigate("Consegna a PDAL", handoff_preset=ready_ids)
    if state in {"REVIEW", "FAILED", "CONVERSION"}:
        with st.expander("Elenco dei file da gestire"):
            display_table(rows)
    else:
        display_table(rows)
    if state == "REVIEW" and rows:
        st.info("Completa il riferimento del rilievo qui sotto. Dopo la conferma passerai al prossimo file da completare.")
    if rows:
        labels = {r["id"]: f'{r["name"]} · {LABELS[r["status"]]} · {r["id"][:8]}' for r in rows}
        if st.session_state.get("catalog_selected") not in labels:
            st.session_state["catalog_selected"] = next(iter(labels))
        selected = st.selectbox("Apri scheda rilievo", list(labels), format_func=labels.get, key="catalog_selected")
        asset_detail(catalog, selected)


@st.fragment(run_every="3s")
def queue(catalog, project_id):
    st.markdown("### Coda e attività")
    st.caption("Un worker locale elabora un file per volta. La coda persiste su disco e riprende i lavori interrotti al riavvio del servizio.")
    rows = catalog.rows("""SELECT j.*,a.name,a.error FROM jobs j JOIN assets a ON a.id=j.asset_id
        WHERE a.project_id=? ORDER BY CASE j.status WHEN 'RUNNING' THEN 0 WHEN 'QUEUED' THEN 1 ELSE 2 END,j.created DESC LIMIT 100""", (project_id,))
    for row in rows:
        if row["status"] == "RUNNING":
            st.markdown(f'**{row["name"]}**')
            st.progress(min(1.0, row["progress"]/100), text=row["phase"])
            st.caption("Avanzamento per fasi: durante la lettura PDAL la percentuale rimane ferma fino al completamento.")
    if rows:
        st.dataframe([{"File": r["name"], "Stato job": r["status"], "Fase": r["phase"], "Tentativi": r["attempts"],
                       "Aggiornato (UTC)": r["updated"], "Errore": r["error"]} for r in rows], hide_index=True, width="stretch")
    else:
        st.info("Nessun lavoro registrato per questo progetto.")


def handoff(catalog, project_id):
    st.markdown("### Consegna al preprocessing PDAL")
    st.caption("Crea un manifest persistente dei rilievi selezionati. La consegna prepara gli input: non avvia pulizia, riduzione, registrazione o generazione BIM.")
    rows = catalog.assets(project_id, state="READY", limit=5000)
    options = {r["id"]: f'{r["name"]} · {r["id"][:8]}' for r in rows}
    if "handoff_preset" in st.session_state:
        st.session_state["handoff_selection"] = [i for i in st.session_state.pop("handoff_preset") if i in options]
    elif "handoff_selection" in st.session_state:
        st.session_state["handoff_selection"] = [i for i in st.session_state["handoff_selection"] if i in options]
    selection = st.multiselect("Rilievi pronti da consegnare", list(options), format_func=options.get, key="handoff_selection")
    if st.button("Crea consegna →", type="primary", disabled=not selection):
        try:
            path = catalog.delivery(project_id, selection)
            st.success(f"Consegna creata: {path.stem[:8]} · {len(selection)} rilievi.")
        except CloudError as exc:
            st.error(str(exc))
    deliveries = catalog.rows("SELECT * FROM deliveries WHERE project_id=? ORDER BY created DESC", (project_id,))
    if not rows:
        st.info("Completa i rilievi in stato Da completare per abilitarne la consegna.")
    st.markdown("#### Consegne registrate")
    for delivery in deliveries[:20]:
        with st.container(border=True):
            st.write(f'{delivery["id"][:8]} · {delivery["count"]} rilievi · {delivery["created"]}')
            path = Path(delivery["path"])
            if path.is_file():
                st.download_button("Scarica manifest", path.read_bytes(), path.name, mime="application/json", key=delivery["id"])
            else:
                st.warning("Manifest non disponibile sul disco.")
    st.caption("Ogni manifest include SHA-256, percorso dell’originale, coordinate, schema, provenienza e dichiarazioni sul riferimento. I percorsi si riferiscono a questo server.")


def main():
    st.set_page_config(page_title="Scan to BIM · Ingestion", page_icon="◈", layout="wide")
    st.markdown("""<style>
    .block-container {padding-top: 3.5rem; max-width: 1540px;}
    h1 {letter-spacing: -.045em;}
    [data-testid="stMetric"] {background:#141e2c;border:1px solid #26354a;border-radius:12px;padding:18px;}
    [data-testid="stMetricLabel"] {color:#92a7bb;}
    .eyebrow {color:#5ee0bc;letter-spacing:.18em;font-size:11px;font-weight:700;margin-bottom:10px;}
    .flow {display:flex;gap:12px;margin:16px 0 28px;flex-wrap:wrap;font-size:12px;color:#8193a9;}
    .flow span {border:1px solid #26354a;padding:8px 14px;border-radius:20px;}
    .flow .active {background:#173b36;color:#75edc9;border-color:#2d7465;}
    </style>""", unsafe_allow_html=True)
    storage = str(Path(os.environ.get("CLOUDLAB_STORAGE", ROOT / "storage")).resolve())
    background = os.environ.get("CLOUDLAB_WORKER", "1") != "0"
    catalog, worker = service(storage, background)
    if background:
        worker.start()
    request = st.session_state.pop("navigation_request", {})
    for key, value in request.items():
        st.session_state[key] = value
    with st.sidebar:
        st.markdown("### ◈ SCAN TO BIM")
        st.caption("DATA WORKSPACE")
        projects = catalog.projects()
        names = {p["id"]: p["name"] for p in projects}
        project_id = st.selectbox("Progetto", list(names), format_func=names.get)
        section = st.radio("Navigazione", ["Panoramica", "Nuovo lotto", "Catalogo rilievi", "Consegna a PDAL"], key="section")
        with st.expander("Crea progetto"):
            with st.form("new_project"):
                name = st.text_input("Nome progetto")
                reference = st.text_input("Riferimento spaziale", placeholder="Caposaldo edificio A / coordinate di sito")
                crs = st.text_input("CRS / EPSG, se disponibile", placeholder="EPSG:32632")
                units = st.selectbox("Unità di progetto", ["m", "mm", "ft", "degree"])
                if st.form_submit_button("Crea progetto"):
                    try:
                        catalog.create_project(name, reference, crs, units)
                        st.rerun()
                    except CloudError as exc:
                        st.error(str(exc))
        st.divider()
        status = runtime()
        if "error" in status:
            st.error(status["error"])
        else:
            st.caption(status["version"])
            st.caption("E57 · LAS · LAZ" if status["e57"] else "LAS · LAZ; plugin E57 mancante")
        st.caption("RCP/RCS → conversione esterna ReCap")
        st.caption(f'Spazio disponibile: {size_label(shutil.disk_usage(catalog.root).free)}')
    project = catalog.project(project_id)
    st.markdown('<div class="eyebrow">SCAN TO BIM / DATA FOUNDATION</div>', unsafe_allow_html=True)
    st.title("Ingestion hub")
    st.caption(f'{project["name"]} · {project["reference"]} · {project["units"]}')
    st.caption("Acquisisci i file → Completa i dati → Prepara la consegna")
    if section == "Panoramica":
        overview(catalog, project_id)
        with st.expander("Attività di elaborazione · dettagli tecnici"):
            queue(catalog, project_id)
    elif section == "Nuovo lotto":
        new_batch(catalog, project)
    elif section == "Catalogo rilievi":
        library(catalog, project_id)
    else:
        handoff(catalog, project_id)
