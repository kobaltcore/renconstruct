### System ###
import os
import sys
import struct
import shutil
import logging
import tempfile
from glob import glob
from multiprocessing.pool import ThreadPool
from subprocess import run, Popen, PIPE, STDOUT
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

### Logging ###
import logzero
from logzero import logger

### Parsing ###
import yaml

### CLI Parsing ###
import click

### Display ###
from tqdm import tqdm


class UpdateableZipFile(ZipFile):

    class DeleteMarker(object):
        pass

    def __init__(self, file, mode="r", compression=ZIP_DEFLATED, allowZip64=False):
        super(UpdateableZipFile, self).__init__(file, mode=mode,
                                                compression=compression,
                                                allowZip64=allowZip64)
        self._replace = {}
        self._allow_updates = False

    def writestr(self, zinfo_or_arcname, bytes, compress_type=None):
        if isinstance(zinfo_or_arcname, ZipInfo):
            name = zinfo_or_arcname.filename
        else:
            name = zinfo_or_arcname
        if self._allow_updates and name in self.namelist():
            temp_file = self._replace[name] = self._replace.get(name, tempfile.TemporaryFile())
            temp_file.write(bytes)
        else:
            super(UpdateableZipFile, self).writestr(zinfo_or_arcname,
                                                    bytes, compress_type=compress_type)

    def write(self, filename, arcname=None, compress_type=None):
        arcname = arcname or filename
        if self._allow_updates and arcname in self.namelist():
            temp_file = self._replace[arcname] = self._replace.get(arcname,
                                                                   tempfile.TemporaryFile())
            with open(filename, "rb") as source:
                shutil.copyfileobj(source, temp_file)
        else:
            super(UpdateableZipFile, self).write(filename,
                                                 arcname=arcname, compress_type=compress_type)

    def __enter__(self):
        self._allow_updates = True
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            super(UpdateableZipFile, self).__exit__(exc_type, exc_val, exc_tb)
            if len(self._replace) > 0:
                self._rebuild_zip()
        finally:
            self._close_all_temp_files()
            self._allow_updates = False

    def _close_all_temp_files(self):
        for temp_file in self._replace.values():
            if hasattr(temp_file, "close"):
                temp_file.close()

    def remove_file(self, path):
        self._replace[path] = self.DeleteMarker()

    def _rebuild_zip(self):
        tempdir = tempfile.mkdtemp()
        try:
            temp_zip_path = os.path.join(tempdir, "new.zip")
            with ZipFile(self.filename, "r") as zip_read:
                with ZipFile(temp_zip_path, "w", compression=self.compression,
                             allowZip64=self._allowZip64) as zip_write:
                    for item in zip_read.infolist():
                        replacement = self._replace.get(item.filename, None)
                        if isinstance(replacement, self.DeleteMarker):
                            del self._replace[item.filename]
                            continue
                        elif replacement is not None:
                            del self._replace[item.filename]
                            replacement.seek(0)
                            data = replacement.read()
                            replacement.close()
                        else:
                            data = zip_read.read(item.filename)
                        zip_write.writestr(item, data)
            shutil.move(temp_zip_path, self.filename)
        finally:
            shutil.rmtree(tempdir)


class AliasedGroup(click.Group):

    def get_command(self, ctx, cmd_name):
        rv = click.Group.get_command(self, ctx, cmd_name)
        if rv is not None:
            return rv
        matches = [x for x in self.list_commands(ctx) if x.startswith(cmd_name)]
        if not matches:
            return None
        elif len(matches) == 1:
            return click.Group.get_command(self, ctx, matches[0])
        ctx.fail("Too many matches: {}".format(", ".join(sorted(matches))))


def pool_process(func, items):
    pool = ThreadPool()

    data = [(file, "{}.webp".format(os.path.splitext(file)[0])) for file in items]
    for output in tqdm(pool.imap_unordered(func, data), total=len(data), unit="files"):
        pass

    pool.close()
    pool.join()


def convert_images(config):
    def convert_lossy(data):
        input_file, output_file = data
        cmd = 'cwebp -q 90 -m 6 -sharp_yuv -pre 4 "{}" -o "{}"'.format(input_file, output_file)
        p = Popen(cmd, shell=True, stdout=PIPE, stderr=STDOUT)
        p.communicate()
        os.remove(input_file)

    def convert_lossless(data):
        input_file, output_file = data
        cmd = 'cwebp -z 9 -m 6 "{}" -o "{}"'.format(input_file, output_file)
        p = Popen(cmd, shell=True, stdout=PIPE, stderr=STDOUT)
        p.communicate()
        os.remove(input_file)

    logger.info("Lossily WebP conversion run")
    files = []
    for directory in ("bg", "anim", "misc", "line_action", "phone_icon", "transitions"):
        files += glob(os.path.join(config["project"], "game", "images", directory, "**", "**.png"), recursive=True)
    files += glob(os.path.join(config["project"], "game", "images", "*.png"))
    files += glob(os.path.join(config["project"], "game", "gui", "intro", "*.png"))
    if files:
        pool_process(convert_lossy, files)
    else:
        logger.info("Already converted")

    logger.info("Lossless WebP conversion run")
    files = glob(os.path.join(config["project"], "game", "images", "cg", "**", "**.png"), recursive=True)
    if files:
        pool_process(convert_lossless, files)
    else:
        logger.info("Already converted")


def set_large_address_aware(filename):
    IMAGE_FILE_LARGE_ADDRESS_AWARE = 0x0020
    PE_HEADER_OFFSET = 60
    CHARACTERISTICS_OFFSET = 18

    f = open(filename, "rb+")

    # Check for MZ Header
    if f.read(2) != b"MZ":
        logger.error("Not MZ for file {}".format(filename))
        sys.exit(1)

    # Get PE header location
    f.seek(PE_HEADER_OFFSET)
    pe_header_loc = struct.unpack("i", f.read(4))[0]

    # Get PE header, check it
    f.seek(pe_header_loc)
    if f.read(4) != b"PE\0\0":
        logger.error("Error in PE header for file {}".format(filename))
        sys.exit(1)

    # Get characteristics, check if IMAGE_FILE_LARGE_ADDRESS_AWARE bit is set
    charac_offset = pe_header_loc + 4 + CHARACTERISTICS_OFFSET
    f.seek(charac_offset)
    bits, = struct.unpack("h", f.read(2))

    if (bits & IMAGE_FILE_LARGE_ADDRESS_AWARE) == IMAGE_FILE_LARGE_ADDRESS_AWARE:
        return True
    else:
        f.seek(charac_offset)
        _bytes = struct.pack("h", (bits | IMAGE_FILE_LARGE_ADDRESS_AWARE))
        f.write(_bytes)
        return False

    f.close()

    return False


def process_large_address_aware(config):
    win_zip = glob(os.path.join(config["output"], "*-win.zip"))[0]

    with UpdateableZipFile(win_zip, "a") as f:
        root_level = os.path.commonprefix(f.namelist())
        pythonw_exe_proto = "/".join((root_level.rstrip("/"), "lib/windows-i686/pythonw.exe"))
        main_exe, main_sub_exe, pythonw_exe = None, None, None
        for file in f.namelist():
            if len(file.split("/")) == 2 and os.path.splitext(file)[1] == ".exe":
                main_exe = file
            if file == pythonw_exe_proto:
                pythonw_exe = file
        main_sub_exe_proto = "/".join((root_level.rstrip("/"), "lib/windows-i686", os.path.basename(main_exe)))
        for file in f.namelist():
            if file == main_sub_exe_proto:
                main_sub_exe = file

        if not all((main_exe, main_sub_exe, pythonw_exe)):
            logger.error("Could not find executable to patch!")
            if not main_exe:
                logger.debug("main_exe: {}".format(main_exe))
            if not main_sub_exe:
                logger.debug("main_sub_exe: {}".format(main_sub_exe))
            if not pythonw_exe:
                logger.debug("pythonw_exe: {}".format(pythonw_exe))
            sys.exit(1)

        with f.open(main_exe) as file:
            with open("main.exe", "wb") as tmp:
                tmp.write(file.read())
        already_set = set_large_address_aware("main.exe")
        if not already_set:
            logger.info("Setting LAW flag for {}".format(main_exe))
            f.write("main.exe", main_exe)
        else:
            logger.info("LAW flag was already set for {}, skipping".format(main_exe))
        os.remove("main.exe")

        with f.open(main_sub_exe) as file:
            with open("main_sub.exe", "wb") as tmp:
                tmp.write(file.read())
        already_set = set_large_address_aware("main_sub.exe")
        if not already_set:
            logger.info("Setting LAW flag for {}".format(main_sub_exe))
            f.write("main_sub.exe", main_sub_exe)
        else:
            logger.info("LAW flag was already set for {}, skipping".format(main_sub_exe))
        os.remove("main_sub.exe")

        with f.open(pythonw_exe) as file:
            with open("pythonw.exe", "wb") as tmp:
                tmp.write(file.read())
        already_set = set_large_address_aware("pythonw.exe")
        if not already_set:
            logger.info("Setting LAW flag for {}".format(pythonw_exe))
            f.write("pythonw.exe", pythonw_exe)
        else:
            logger.info("LAW flag was already set for {}, skipping".format(pythonw_exe))
        os.remove("pythonw.exe")


@click.command()
@click.option("-i", "--input", "project", required=True, type=str)
@click.option("-o", "--output", required=True, type=str)
@click.option("-c", "--config", default="config.yaml", type=str)
@click.option("-d/-nd", "--debug/--no-debug", default=False,
              help="Print debug information or only regular output")
def cli(project, output, config, debug):
    """A utility script to automatically build Ren'Py applications for multiple platforms.
    """
    logzero.loglevel(logging.DEBUG if debug else logging.INFO)

    if not os.path.isdir(project):
        logger.error("The path to the project is incorrect.")
        sys.exit(1)

    if not os.path.exists(output):
        logger.warning("The output directory does not exist, creating it...")
        os.makedirs(output, exist_ok=True)
    if not os.path.isdir(output):
        logger.error("The output path is not a directory.")
        sys.exit(1)

    if not os.path.exists(config):
        logger.error("The path to the config file is incorrect.")
        sys.exit(1)

    with open(config, "r") as f:
        config = yaml.full_load(f)

    config["project"] = project
    config["output"] = output
    config["debug"] = debug

    if config.get("general", None) is None:
        config["general"] = {"convert_images": False}
    if config["general"].get("convert_images", None) is None:
        config["general"]["convert_images"] = False

    if config.get("build", None) is None:
        config["build"] = {"win": {"build": True, "set_extended_memory_limit": True},
                           "mac": {"build": True, "notarize": True},
                           "android": {"build": True}}
    if config["build"].get("win", None) is None:
        config["build"]["win"] = {"build": True, "set_extended_memory_limit": True}
    if config["build"]["win"].get("set_extended_memory_limit", None) is None:
        config["build"]["win"]["set_extended_memory_limit"] = True
    if config["build"]["win"].get("build", None) is None:
        config["build"]["win"]["build"] = True
    if config["build"].get("mac", None) is None:
        config["build"]["mac"] = {"build": True, "notarize": True}
    if config["build"]["mac"].get("notarize", None) is None:
        config["build"]["mac"]["notarize"] = True
    if config["build"]["win"].get("build", None) is None:
        config["build"]["win"]["build"] = True
    if config["build"].get("android", None) is None:
        config["build"]["android"] = {"build": True}
    if config["build"]["android"].get("build", None) is None:
        config["build"]["android"]["build"] = True

    if config.get("renutil", None) is None:
        config["renutil"] = {"version": "latest"}
    if config["renutil"].get("version", None) is None:
        config["renutil"]["version"] = "latest"

    if config.get("renotize", None) is None:
        config["renotize"] = {}
    for key in ("apple_id", "password", "identity", "bundle"):
        if config["renotize"].get(key, None) is None:
            logger.error("'{}' is a required key for 'renotize'!".format(key))
            sys.exit(1)
    if config["renotize"].get("altool_extra", None) is None:
        config["renotize"]["altool_extra"] = ""

    p = run("renutil --help", capture_output=True, shell=True)
    if not (b"Usage: renutil" in p.stdout and p.returncode == 0):
        logger.error("Please install 'renutil' before continuing!")
        sys.exit(1)

    p = run("renotize --help", capture_output=True, shell=True)
    if not (b"Usage: renotize" in p.stdout and p.returncode == 0):
        logger.error("Please install 'renotize' before continuing!")
        sys.exit(1)

    logger.info("Checking available Ren'Py versions")
    p = run("renutil list", capture_output=True, shell=True)
    available_versions = [item.strip() for item in p.stdout.decode("utf-8").split("\n")]

    if config["renutil"]["version"] == "latest":
        p = run("renutil list --all", capture_output=True, shell=True)
        chosen_version = [item.strip() for item in p.stdout.decode("utf-8").split("\n")][0]
    else:
        chosen_version = config["renutil"]["version"]

    if chosen_version not in available_versions:
        logger.warning("Ren'Py {} is not installed, installing now...".format(config["renutil"]["version"]))
        p = run("renutil install {}".format(config["renutil"]["version"]), shell=True)

    if config["general"]["convert_images"]:
        logger.info("Converting image assets")
        convert_images(config)

    if config["build"]["android"]["build"]:
        logger.info("Building Android package")
        cmd = "renutil launch {} android_build {} assembleRelease --destination {}".format(config["renutil"]["version"],
                                                                                           config["project"],
                                                                                           config["output"])
        proc = Popen(cmd, shell=True, stdout=PIPE, stderr=STDOUT)
        for line in proc.stdout:
            line = str(line.strip(), "utf-8")
            if line:
                logger.debug(line)

    platforms_to_build = []
    if config["build"]["win"]["build"]:
        platforms_to_build.append("win")
    if config["build"]["mac"]["build"]:
        platforms_to_build.append("mac")
    if len(platforms_to_build) == 1:
        logger.info("Building {} package".format(platforms_to_build[0]))
    elif len(platforms_to_build) > 1:
        logger.info("Building {} packages".format(", ".join(platforms_to_build)))
    if platforms_to_build:
        cmd = "renutil launch {} distribute {} --destination {}".format(config["renutil"]["version"],
                                                                        config["project"],
                                                                        config["output"])
        for package in platforms_to_build:
            cmd += " --package {}".format(package)
        proc = Popen(cmd, shell=True, stdout=PIPE, stderr=STDOUT)
        for line in proc.stdout:
            line = str(line.strip(), "utf-8")
            if line:
                logger.debug(line)

    if config["build"]["win"]["build"] and config["build"]["win"]["set_extended_memory_limit"]:
        logger.info("Enabling LAW flag for windows package")
        process_large_address_aware(config)

    if config["build"]["mac"]["build"] and config["build"]["mac"]["notarize"]:
        logger.info("Notarizing macOS package")
        with open("renotize.yml", "w") as f:
            f.write(yaml.dump(config["renotize"]))

        mac_zip = glob(os.path.join(config["output"], "*-mac.zip"))[0]

        cmd = "renotize -c renotize.yml {} full-run".format(mac_zip)
        proc = Popen(cmd, shell=True, stdout=PIPE, stderr=STDOUT)
        for line in proc.stdout:
            line = str(line.strip(), "utf-8")
            if line:
                logger.debug(line)

        os.remove("renotize.yml")

    logger.info("Cleaning up")
    p = run("renutil clean {}".format(config["renutil"]["version"]), capture_output=True, shell=True)

    unused_apks = [item for item in glob(os.path.join(config["output"], "*.apk"))
                   if not item.endswith("-universal-release.apk")]
    for file in unused_apks:
        os.remove(file)


if __name__ == '__main__':
    cli()
