### System ###
import os
import base64
from glob import glob
from subprocess import Popen, PIPE, STDOUT

### Logging ###
from renconstruct import logger

### Parsing ###
import yaml


class NotarizeTask:

    # The higher priority, the earlier the task runs
    # This is relative to all other enabled tasks
    PRIORITY = 0

    def __init__(self, name, config):
        self.name = name
        self.config = config

    @staticmethod
    def validate_config(config):
        config["sign_cert"] = config.get("sign_cert", os.environ.get("RC_SIGN_CERT"))
        if config.get("sign_cert", None) is None:
            raise Exception(
                "The notarize task is active, but no signing certificate was specified. Please specify either the 'sign_cert' config option or the 'RC_SIGN_CERT' environment variable."
            )

        config["sign_cert_pwd"] = config.get(
            "sign_cert_pwd", os.environ.get("RC_SIGN_CERT_PWD")
        )
        if config.get("sign_cert_pwd", None) is None:
            raise Exception(
                "The notarize task is active, but no certificate password was specified. Please specify either the 'sign_cert_pwd' config option or the 'RC_SIGN_CERT_PWD' environment variable."
            )

        config["apple_id"] = config.get("apple_id", os.environ.get("RC_APPLE_ID"))
        if config.get("apple_id", None) is None:
            raise Exception(
                "The notarize task is active, but no Apple ID was specified. Please specify either the 'apple_id' config option or the 'RC_APPLE_ID' environment variable."
            )

        config["password"] = config.get("password", os.environ.get("RC_APPLE_PWD"))
        if config.get("password", None) is None:
            raise Exception(
                "The notarize task is active, but no Apple ID password was specified. Please specify either the 'password' config option or the 'RC_APPLE_PWD' environment variable."
            )

        config["identity"] = config.get("identity", os.environ.get("RC_SIGN_IDENTITY"))
        if config.get("identity", None) is None:
            raise Exception(
                "The notarize task is active, but no bundle identity was specified. Please specify either the 'identity' config option or the 'RC_SIGN_IDENTITY' environment variable."
            )

        config["sign_cert"] = base64.b64decode(config["sign_cert"])

        config["altool_extra"] = config.get("altool_extra", None)

        return config

    def run_cmd(self, cmd):
        proc = Popen(cmd, shell=True, stdout=PIPE, stderr=STDOUT)
        for line in proc.stdout:
            line = str(line.strip(), "utf-8")
            if line:
                logger.debug(line)

    def post_build(self):
        logger.info("Setting up developer certificate...")
        # write decoded file to disk
        with open("certificate.p12", "wb") as f:
            f.write(self.config["notarize"]["sign_cert"])

        # create new keychain
        self.run_cmd(
            "security delete-keychain -p {} build.keychain".format(
                self.config["notarize"]["sign_cert_pwd"]
            )
        )
        self.run_cmd(
            "security create-keychain -p {} build.keychain".format(
                self.config["notarize"]["sign_cert_pwd"]
            )
        )

        # set new keychain as default
        self.run_cmd("security default-keychain -s build.keychain")

        # unlock the keychain by default (prevents password prompt)
        self.run_cmd(
            "security unlock-keychain -p {} build.keychain".format(
                self.config["notarize"]["sign_cert_pwd"]
            )
        )

        # import the decoded certificate
        self.run_cmd(
            "security import certificate.p12 -k build.keychain -P {} -T /usr/bin/codesign -T /usr/bin/xcrun".format(
                self.config["notarize"]["sign_cert_pwd"]
            )
        )

        # add codesign and altool to ACL
        self.run_cmd(
            "security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k {} build.keychain".format(
                self.config["notarize"]["sign_cert_pwd"]
            )
        )
        self.run_cmd(
            "security set-key-partition-list -S apple-tool:,apple:,xcrun: -s -k {} build.keychain".format(
                self.config["notarize"]["sign_cert_pwd"]
            )
        )

        # remove certificate file after import
        os.remove("certificate.p12")

        logger.info("Creating reNotize config file...")
        with open("renotize.yml", "w") as f:
            f.write(yaml.dump(self.config["notarize"]))

        mac_zip = glob(os.path.join(self.config["output"], "*-mac.zip"))[0]

        cmd = "renotize -c renotize.yml {} full-run".format(mac_zip)
        proc = Popen(cmd, shell=True, stdout=PIPE, stderr=STDOUT)
        for line in proc.stdout:
            line = str(line.strip(), "utf-8")
            if line:
                logger.debug(line)

        os.remove("renotize.yml")
