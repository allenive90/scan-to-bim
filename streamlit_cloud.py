"""Community Cloud entry point; app.py keeps the desktop folder workflow."""
import os

os.environ['CLOUDLAB_CLOUD'] = '1'

from cloudlab.restoration_ui import main

main()
