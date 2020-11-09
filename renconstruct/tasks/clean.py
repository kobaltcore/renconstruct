### System ###
import os
from glob import glob
from subprocess import run

### Logging ###
from renconstruct import logger


class CleanTask:

    # The higher priority, the earlier the task runs
    # This is relative to all other enabled tasks
    PRIORITY = -1000

    def __init__(self, name, config):
        self.name = name
        self.config = config

    def post_build(self):
        run(
            "renutil clean {}".format(self.config["renutil"]["version"]),
            capture_output=True,
            shell=True,
        )

        unused_apks = [
            item
            for item in glob(os.path.join(self.config["output"], "*.apk"))
            if not item.endswith("-universal-release.apk")
        ]
        for file in unused_apks:
            logger.debug("Removing file '{}'".format(os.path.basename(file)))
            os.remove(file)
