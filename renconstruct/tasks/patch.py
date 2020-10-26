### System ###
import os
from glob import glob
from shutil import copyfile

### Logging ###
from renconstruct import logger

### Diffing ###
from diff_match_patch import diff_match_patch


class PatchTask:

    # The higher priority, the earlier the task runs
    # This is relative to all other enabled tasks
    PRIORITY = 1000

    def __init__(self, name, config):
        self.name = name
        self.config = config

    @staticmethod
    def validate_config(config):
        if config.get("path", None) is None:
            raise Exception("Field 'path' missing")
        else:
            config["path"] = os.path.abspath(os.path.expanduser(config["path"]))
            if not os.path.isdir(config["path"]):
                raise Exception("Directory '{}' does not exist".format(config["path"]))

        return config

    def pre_build(self):
        patch_files = glob(
            os.path.join(self.config[self.name]["path"], "**", "*.*"), recursive=True
        )
        amount = len(patch_files)
        logger.debug("Found {} patch file{}.".format(amount, "s" if amount > 1 else ""))

        errors = set()
        dmp = diff_match_patch()
        for patch_file in patch_files:
            with open(patch_file, "r") as f:
                patch_text = f.read()

            try:
                patches = dmp.patch_fromText(patch_text)
            except Exception as e:
                logger.error("Failed to parse patch file {}".format(patch_file))
                logger.error(e)
                errors.add(patch_file)
                continue

            rel_path = os.path.relpath(patch_file, start=self.config[self.name]["path"])
            target_file = os.path.join(self.config["renutil"]["path"], rel_path)
            backup_file = "{}.original".format(target_file)

            if os.path.isfile(backup_file):
                logger.debug(
                    "Original file found, replacing current version with original before patching"
                )
                if os.path.isfile(target_file):
                    os.remove(target_file)
                copyfile(backup_file, target_file)
            else:
                copyfile(target_file, backup_file)

            with open(target_file, "r") as f:
                target_text = f.read()

            try:
                patched_text, hunk_success = dmp.patch_apply(patches, target_text)
            except Exception as e:
                logger.error("Failed to apply patch to file {}".format(target_file))
                logger.error(e)
                errors.add(patch_file)
                continue

            with open(target_file, "w") as f:
                f.write(patched_text)

        if errors:
            for patch_file in patch_files:
                rel_path = os.path.relpath(
                    patch_file, start=self.config[self.name]["path"]
                )
                target_file = os.path.join(self.config["renutil"]["path"], rel_path)
                backup_file = "{}.original".format(target_file)
                os.remove(target_file)
                os.rename(backup_file, target_file)
            raise Exception(
                "Some errors occured while patching Ren'Py, rolled back all changes"
            )
