### System ###
import os
from glob import glob
from subprocess import run

### Logging ###
from logzero import logger  # noqa: F401


class CleanTask():

    # The higher priority, the earlier the task runs
    # This is relative to all other enabled tasks
    PRIORITY = -1000

    def __init__(self, config):
        self.config = config

    def post_build(self):
        p = run("renutil clean {}".format(self.config["renutil"]["version"]),  # noqa: F841
                capture_output=True, shell=True)

        unused_apks = [item for item in glob(os.path.join(self.config["output"], "*.apk"))
                       if not item.endswith("-universal-release.apk")]
        for file in unused_apks:
            os.remove(file)
