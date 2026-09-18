"""Community Cloud entry point; app.py keeps the desktop folder workflow."""
import os
from pathlib import Path
import shutil
import sys

os.environ['CLOUDLAB_CLOUD'] = '1'

# Community Cloud can start Streamlit without Conda's bin directory in PATH.
if not os.environ.get('PDAL_BIN') and not shutil.which('pdal'):
    prefixes = [Path(sys.prefix), Path(sys.base_prefix)]
    if os.environ.get('CONDA_PREFIX'):
        prefixes.insert(0, Path(os.environ['CONDA_PREFIX']))
    if os.environ.get('CONDA_EXE'):
        prefixes.append(Path(os.environ['CONDA_EXE']).parent.parent)
    prefixes += [Path.home() / 'miniconda3', Path.home() / 'anaconda3', Path('/opt/conda')]
    for prefix in prefixes:
        binary = prefix / 'bin' / 'pdal'
        if binary.is_file() and os.access(binary, os.X_OK):
            os.environ['PDAL_BIN'] = str(binary)
            os.environ.setdefault('PDAL_DRIVER_PATH', str(prefix / 'lib'))
            break

print(f'SCAN TO BIM runtime: Python {sys.version.split()[0]}; PDAL={os.environ.get("PDAL_BIN") or shutil.which("pdal")}', flush=True)

from cloudlab.restoration_ui import main

main()
