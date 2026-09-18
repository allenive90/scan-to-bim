from io import BytesIO

import pytest
from streamlit.testing.v1 import AppTest
from cloudlab.cloud_uploads import stage_uploads, validate_uploads, MAX_UPLOAD_BYTES
from cloudlab.engine import CloudError, ROOT


class Upload(BytesIO):
    def __init__(self, name, data=b'points'):
        super().__init__(data)
        self.name = name
        self.size = len(data)


def test_upload_names_cannot_escape_inbox(tmp_path):
    paths = stage_uploads(tmp_path, [Upload('../../cloud.las'), Upload('cloud.las', b'other')])
    assert all(p.is_relative_to(tmp_path / 'inbox') for p in paths)
    assert paths[0] != paths[1]
    assert [p.read_bytes() for p in paths] == [b'points', b'other']


def test_upload_limits():
    with pytest.raises(CloudError):
        validate_uploads([Upload('cloud.rcp')])
    large = Upload('cloud.las')
    large.size = MAX_UPLOAD_BYTES + 1
    with pytest.raises(CloudError):
        validate_uploads([large])
    with pytest.raises(CloudError):
        validate_uploads([Upload('cloud.las')] * 6)


def test_cloud_sessions_are_separate(tmp_path, monkeypatch):
    monkeypatch.setenv('CLOUDLAB_CLOUD', '1')
    monkeypatch.setenv('CLOUDLAB_WORKER', '0')
    monkeypatch.setenv('CLOUDLAB_STORAGE', str(tmp_path / 'store'))
    a = AppTest.from_file(ROOT / 'app.py', default_timeout=60).run()
    b = AppTest.from_file(ROOT / 'app.py', default_timeout=60).run()
    assert not a.exception and not b.exception
    assert a.session_state['cloud_project'] != b.session_state['cloud_project']
    assert a.radio[1].options == ['Carica dal computer', 'Esempi di restauro']
    assert not any(w.label == 'Cartella del dataset' for w in a.text_input)
    assert a.session_state['restoration_batch'] is None
    a.radio[1].set_value('Esempi di restauro').run()
    next(w for w in a.button if w.label == 'Carica e apri la vista 3D →').click().run()
    b.run()
    assert not a.exception and not b.exception
    assert a.session_state['restoration_batch']
    assert b.session_state['restoration_batch'] is None
