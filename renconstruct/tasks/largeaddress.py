### System ###
import os
import sys
import struct
import shutil
import tempfile
from glob import glob
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

### Logging ###
from renconstruct import logger


class UpdateableZipFile(ZipFile):
    class DeleteMarker(object):
        pass

    def __init__(self, file, mode="r", compression=ZIP_DEFLATED, allowZip64=False):
        super(UpdateableZipFile, self).__init__(
            file, mode=mode, compression=compression, allowZip64=allowZip64
        )
        self._replace = {}
        self._allow_updates = False

    def writestr(self, zinfo_or_arcname, bytes, compress_type=None):
        if isinstance(zinfo_or_arcname, ZipInfo):
            name = zinfo_or_arcname.filename
        else:
            name = zinfo_or_arcname
        if self._allow_updates and name in self.namelist():
            temp_file = self._replace[name] = self._replace.get(
                name, tempfile.TemporaryFile()
            )
            temp_file.write(bytes)
        else:
            super(UpdateableZipFile, self).writestr(
                zinfo_or_arcname, bytes, compress_type=compress_type
            )

    def write(self, filename, arcname=None, compress_type=None):
        arcname = arcname or filename
        if self._allow_updates and arcname in self.namelist():
            temp_file = self._replace[arcname] = self._replace.get(
                arcname, tempfile.TemporaryFile()
            )
            with open(filename, "rb") as source:
                shutil.copyfileobj(source, temp_file)
        else:
            super(UpdateableZipFile, self).write(
                filename, arcname=arcname, compress_type=compress_type
            )

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
                with ZipFile(
                    temp_zip_path,
                    "w",
                    compression=self.compression,
                    allowZip64=self._allowZip64,
                ) as zip_write:
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


class SetExtendedMemoryLimitTask:

    # The higher priority, the earlier the task runs
    # This is relative to all other enabled tasks
    PRIORITY = 0

    def __init__(self, name, config):
        self.name = name
        self.config = config
        self.active = config["build"]["pc"]

    def set_large_address_aware(self, filename):
        IMAGE_FILE_LARGE_ADDRESS_AWARE = 0x0020
        PE_HEADER_OFFSET = 60
        CHARACTERISTICS_OFFSET = 18

        f = open(filename, "rb+")

        # Check for MZ Header
        if f.read(2) != b"MZ":
            logger.error("No MZ for file '{}'".format(filename))
            sys.exit(1)

        # Get PE header location
        f.seek(PE_HEADER_OFFSET)
        pe_header_loc = struct.unpack("i", f.read(4))[0]

        # Get PE header, check it
        f.seek(pe_header_loc)
        if f.read(4) != b"PE\0\0":
            logger.error("Error in PE header for file '{}'".format(filename))
            sys.exit(1)

        # Get characteristics, check if IMAGE_FILE_LARGE_ADDRESS_AWARE bit is set
        charac_offset = pe_header_loc + 4 + CHARACTERISTICS_OFFSET
        f.seek(charac_offset)
        (bits,) = struct.unpack("h", f.read(2))

        if (bits & IMAGE_FILE_LARGE_ADDRESS_AWARE) == IMAGE_FILE_LARGE_ADDRESS_AWARE:
            return True
        else:
            f.seek(charac_offset)
            _bytes = struct.pack("h", (bits | IMAGE_FILE_LARGE_ADDRESS_AWARE))
            f.write(_bytes)
            return False

        f.close()

        return False

    def post_build(self):
        if not self.active:
            return

        win_zip = glob(os.path.join(self.config["output"], "*-pc.zip"))[0]

        with UpdateableZipFile(win_zip, "a") as f:
            root_level = os.path.commonprefix(f.namelist())
            pythonw_exe_proto = "/".join(
                (root_level.rstrip("/"), "lib/windows-i686/pythonw.exe")
            )
            main_exe, main_sub_exe, pythonw_exe = None, None, None
            for file in f.namelist():
                if len(file.split("/")) == 2 and os.path.splitext(file)[1] == ".exe":
                    main_exe = file
                if file == pythonw_exe_proto:
                    pythonw_exe = file
            main_sub_exe_proto = "/".join(
                (root_level.rstrip("/"), "lib/windows-i686", os.path.basename(main_exe))
            )
            for file in f.namelist():
                if file == main_sub_exe_proto:
                    main_sub_exe = file

            if not all((main_exe, main_sub_exe, pythonw_exe)):
                logger.error("Could not find executable to patch!")
                if not main_exe:
                    logger.debug("main_exe: '{}'".format(main_exe))
                if not main_sub_exe:
                    logger.debug("main_sub_exe: '{}'".format(main_sub_exe))
                if not pythonw_exe:
                    logger.debug("pythonw_exe: '{}'".format(pythonw_exe))
                sys.exit(1)

            with f.open(main_exe) as file:
                with open("main.exe", "wb") as tmp:
                    tmp.write(file.read())
            already_set = self.set_large_address_aware("main.exe")
            if not already_set:
                logger.info("Setting LAA flag for '{}'".format(main_exe))
                f.write("main.exe", main_exe)
            else:
                logger.info(
                    "LAA flag was already set for '{}', skipping".format(main_exe)
                )
            os.remove("main.exe")

            with f.open(main_sub_exe) as file:
                with open("main_sub.exe", "wb") as tmp:
                    tmp.write(file.read())
            already_set = self.set_large_address_aware("main_sub.exe")
            if not already_set:
                logger.info("Setting LAA flag for '{}'".format(main_sub_exe))
                f.write("main_sub.exe", main_sub_exe)
            else:
                logger.info(
                    "LAA flag was already set for '{}', skipping".format(main_sub_exe)
                )
            os.remove("main_sub.exe")

            with f.open(pythonw_exe) as file:
                with open("pythonw.exe", "wb") as tmp:
                    tmp.write(file.read())
            already_set = self.set_large_address_aware("pythonw.exe")
            if not already_set:
                logger.info("Setting LAA flag for '{}'".format(pythonw_exe))
                f.write("pythonw.exe", pythonw_exe)
            else:
                logger.info(
                    "LAA flag was already set for '{}', skipping".format(pythonw_exe)
                )
            os.remove("pythonw.exe")
