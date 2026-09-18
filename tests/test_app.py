import os

import pytest
from streamlit.testing.v1 import AppTest

from cloudlab.engine import ROOT
from cloudlab.catalog import Catalog
from cloudlab.restoration_ui import service, STEPS
from cloudlab.worker import Worker


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv('CLOUDLAB_STORAGE', str(tmp_path / 'store'))
    monkeypatch.setenv('CLOUDLAB_WORKER', '0')
    catalog, worker, project = service(str(tmp_path / 'store'), False)
    catalog.enqueue(project, [ROOT / 'data/restoration/palazzo_storico.laz'], 'Facciata storica', {'reference_confirmed': True})
    while worker.once():
        pass
    return AppTest.from_file(ROOT / 'app.py', default_timeout=90).run()


def widget(app, kind, label):
    return next(w for w in getattr(app, kind) if w.label == label)


def test_only_local_folder_and_restoration_examples(app):
    assert not app.exception
    assert not app.error
    assert app.title[0].value == 'SCAN TO BIM'
    assert widget(app, 'radio', 'Fonte del dataset').options == ['Cartella locale', 'Esempi di restauro']
    assert widget(app, 'radio', 'Fonte del dataset').value == 'Cartella locale'
    widget(app, 'radio', 'Fonte del dataset').set_value('Esempi di restauro').run()
    options = widget(app, 'selectbox', 'Scegli un esempio').options
    assert len(options) == 4
    assert not any('terreno' in name.lower() or 'tunnel' in name.lower() for name in options)


def test_folder_discovery(app):
    widget(app, 'radio', 'Fonte del dataset').set_value('Cartella locale').run()
    widget(app, 'text_input', 'Cartella del dataset').set_value(str(ROOT / 'data/restoration'))
    app.run()
    widget(app, 'button', 'Trova le nuvole').click().run()
    assert not app.exception
    assert len(widget(app, 'multiselect', 'Nuvole del dataset').value) == 9


def test_changing_folder_cannot_acquire_previous_search(app):
    widget(app, 'radio', 'Fonte del dataset').set_value('Cartella locale').run()
    widget(app, 'text_input', 'Cartella del dataset').set_value(str(ROOT / 'data/restoration')).run()
    widget(app, 'button', 'Trova le nuvole').click().run()
    assert not widget(app, 'button', 'Carica e apri la vista 3D →').disabled
    widget(app, 'text_input', 'Cartella del dataset').set_value(str(ROOT / 'does-not-exist')).run()
    assert widget(app, 'button', 'Carica e apri la vista 3D →').disabled
    widget(app, 'button', 'Trova le nuvole').click().run()
    assert app.error
    assert widget(app, 'button', 'Carica e apri la vista 3D →').disabled


def test_step_navigation_shows_direct_3d_preview(app):
    assert not any('Riprendi' in button.label for button in app.button)
    widget(app, 'radio', 'I quattro passaggi').set_value(STEPS[1]).run()
    assert not app.exception
    assert widget(app, 'radio', 'I quattro passaggi').value == STEPS[1]
    assert len(app.get('plotly_chart')) == 1
    assert widget(app, 'selectbox', 'Nuvola da visualizzare').options == ['palazzo_storico.laz']


def test_end_to_end_acquire_preview_pdal_summary(app):
    c = Catalog(os.environ['CLOUDLAB_STORAGE'])
    widget(app, 'radio', 'Fonte del dataset').set_value('Esempi di restauro').run()
    widget(app, 'selectbox', 'Scegli un esempio').set_value('fontana').run()
    widget(app, 'button', 'Carica e apri la vista 3D →').click().run()
    assert not app.exception
    assert widget(app, 'radio', 'I quattro passaggi').value == STEPS[1]
    assert any('Non devi premere altro' in i.value for i in app.info)
    Worker(c).once()
    app.run()
    assert not app.exception
    assert len(app.get('plotly_chart')) == 1
    widget(app, 'button', 'Continua: prepara i dati →').click().run()
    widget(app, 'selectbox', 'Cosa vuoi fare?').set_value('Nuvola più leggera').run()
    widget(app, 'button', 'Elabora e vedi i risultati →').click().run()
    assert not app.exception
    assert not app.error
    assert widget(app, 'radio', 'I quattro passaggi').value == STEPS[3]
    Worker(c).once()
    app.run()
    assert not app.exception
    assert not app.error
    assert any('Elaborazione completata' in i.value for i in app.success)
    assert any(m.label == 'Punti dopo' and int(m.value.replace(',', '')) < 90000 for m in app.metric)
    widget(app, 'button', 'Confronta il risultato in 3D →').click().run()
    assert not app.exception
    assert widget(app, 'radio', 'Versione').value != 'original'
    assert len(app.get('plotly_chart')) == 1


def test_duplicate_opens_existing_preview(app):
    c = Catalog(os.environ['CLOUDLAB_STORAGE'])
    widget(app, 'radio', 'Fonte del dataset').set_value('Esempi di restauro').run()
    widget(app, 'button', 'Carica e apri la vista 3D →').click().run()
    Worker(c).once()
    app.run()
    assert not app.exception
    assert any('già acquisiti' in i.value for i in app.info)
    assert len(app.get('plotly_chart')) == 1


def test_summary_available_without_processing(app):
    widget(app, 'radio', 'I quattro passaggi').set_value(STEPS[3]).run()
    assert not app.exception
    assert app.metric[0].value == '1'
    assert app.metric[2].value == '90,000'
    assert any('Non hai ancora richiesto filtri' in i.value for i in app.info)


def test_back_from_pdal_does_not_submit_invalid_settings(app):
    widget(app, 'radio', 'I quattro passaggi').set_value(STEPS[2]).run()
    widget(app, 'text_area', 'Filtri aggiuntivi').set_value('invalid JSON')
    widget(app, 'button', '← Passaggio precedente').click().run()
    assert not app.exception
    assert not app.error
    assert widget(app, 'radio', 'I quattro passaggi').value == STEPS[1]
    assert Catalog(os.environ['CLOUDLAB_STORAGE']).rows('SELECT id FROM processing_runs') == []
