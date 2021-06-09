### System ###
import os
import base64

### Logging ###
from renconstruct import logger


class OverwriteKeystoreTask:

    # The higher priority, the earlier the task runs
    # This is relative to all other enabled tasks
    PRIORITY = 0

    # A list of files (relative to the base SDK directory)
    # which will be modified by this task
    AFFECTED_FILES = ["rapt/android.keystore"]

    def __init__(self, name, config):
        self.name = name
        self.config = config

    @staticmethod
    def validate_config(config):
        config["keystore"] = config.get("keystore", os.environ.get("RC_KEYSTORE"))
        if config.get("keystore", None) is None:
            raise Exception(
                "The overwrite_keystore task is active, but no keystore was specified. Please specify either the 'keystore' config option or the 'RC_KEYSTORE' environment variable."
            )

        config["keystore"] = base64.b64decode(config["keystore"])

        return config

    def pre_build(self):
        logger.info("Overwriting default keystore with custom one...")
        keystore_file = os.path.join(
            self.config["renutil"]["path"], "rapt", "android.keystore"
        )
        with open(keystore_file, "wb") as f:
            f.write(self.config["overwrite_keystore"]["keystore"])
