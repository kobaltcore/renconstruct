### System ###
import os
import re
import sys
import shutil
import logging
import inspect
from glob import glob
from subprocess import run, Popen, PIPE, STDOUT

### Logging ###
import logzero
from logzero import logger

### Parsing ###
import yaml

### CLI Parsing ###
import click

### Display ###
from termcolor import colored


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


def run_tasks(config, tasks, stage="pre-build"):
    logger.info("Running stage '{}' for active tasks".format(stage))
    for name, task_class, priority in tasks:
        try:
            task = task_class(config)
        except Exception as e:
            logger.error("Task {} failed to initialize:".format(name))
            logger.error(e)
            sys.exit(1)
        try:
            if stage == "pre-build" and hasattr(task, "pre_build"):
                logger.info("Running {}".format(colored(name, "green")))
                task.pre_build()
            elif stage == "post-build" and hasattr(task, "post_build"):
                logger.info("Running {}".format(colored(name, "green")))
                task.post_build()
        except Exception as e:
            logger.error("Task {} failed to execute '{}':".format(name, stage.replace("-", "_")))
            logger.error(e)
            sys.exit(1)


def scan_tasks(config):
    task_files = glob(os.path.join(os.path.dirname(__file__), "tasks", "**", "*.py"), recursive=True)
    if config["tasks"]["path"] and os.path.isdir(config["tasks"]["path"]):
        logger.debug("Using tasks from {}".format(config["tasks"]["path"]))
        task_files += glob(os.path.join(config["tasks"]["path"], "**", "*.py"), recursive=True)

    tmp_dir = os.path.join(os.path.dirname(__file__), "tasklib")
    if os.path.isdir(tmp_dir):
        shutil.rmtree(tmp_dir)
    os.makedirs(tmp_dir, exist_ok=True)
    for file in task_files:
        shutil.copyfile(file, os.path.join(tmp_dir, os.path.basename(file)))
    task_files = [os.path.join("tasklib", os.path.basename(file))
                  for file in glob(os.path.join(tmp_dir, "**", "*.py"), recursive=True)]

    available_tasks = {}
    for file in task_files:
        module_name = ".".join(os.path.splitext(file)[0].split(os.sep))
        task_module = __import__(module_name)  # noqa: F841
        classes = [(name, obj) for name, obj in inspect.getmembers(sys.modules[module_name], inspect.isclass)]
        for name, task_class in classes:
            if name.endswith("Task"):
                new_name = "_".join([item.lower() for item in re.split(r"(?=[A-Z])", name[:-4]) if item])
                available_tasks[new_name] = task_class

    available_task_names = set(available_tasks.keys())
    defined_task_names = set([item for item in config["tasks"].keys() if item != "path"])
    undefined_task_names = available_task_names - defined_task_names
    if undefined_task_names:
        logger.warning("Some tasks were not defined in the config, assuming they are disabled:")
        for name in undefined_task_names:
            logger.warning("- {}".format(name))

    new_tasks = {}
    for name, task_class in available_tasks.items():
        config_value = config["tasks"].get(name, False)
        if not isinstance(config_value, bool):
            logger.error("{} must be 'True' or 'False', got {}".format(name, config_value))
            sys.exit(1)
        else:
            task_class = available_tasks[name]
            new_tasks[name] = (config_value, task_class)

    runnable_tasks = []
    logger.info("Loaded tasks:")
    for name, (enabled, task_class) in new_tasks.items():
        logger.info("{} {}".format(colored("\u2714", "green") if enabled else colored("\u2718", "red"), name))
        if enabled:
            priority = task_class.PRIORITY if hasattr(task_class, "PRIORITY") else 0
            runnable_tasks.append((name, task_class, priority))

    return sorted(runnable_tasks, key=lambda x: x[2], reverse=True)


def validate_config(config):
    if config.get("build", None) is None:
        config["build"] = {"win": True,
                           "mac": True,
                           "android": True}
    if config["build"].get("win", None) is None:
        config["build"]["win"] = True
    if config["build"].get("mac", None) is None:
        config["build"]["mac"] = True
    if config["build"].get("android", None) is None:
        config["build"]["android"] = True

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

    if config.get("tasks", None) is None:
        config["tasks"] = {"path": None}
    if config["tasks"].get("path", None) is None:
        config["tasks"]["path"] = None
    if config["tasks"].get("path", None) is not None:
        config["tasks"]["path"] = os.path.expanduser(config["tasks"]["path"])

    return config


@click.command()
@click.option("-i", "--input", "project", required=True, type=str,
              help="The path to the Ren'Py project to build")
@click.option("-o", "--output", required=True, type=str,
              help="The directory to output build artifacts to")
@click.option("-c", "--config", required=True, type=str,
              help="The configuration file for this run")
@click.option("-d", "--debug", is_flag=True,
              help="If given, shows debug information if")
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

    config = validate_config(config)

    runnable_tasks = scan_tasks(config)

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
        config["renutil"]["version"] = chosen_version

    if config["renutil"]["version"] not in available_versions:
        logger.warning("Ren'Py {} is not installed, installing now...".format(config["renutil"]["version"]))
        p = run("renutil install {}".format(config["renutil"]["version"]), shell=True)

    if runnable_tasks:
        run_tasks(config, runnable_tasks, stage="pre-build")

    if config["build"]["android"]:
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
    if config["build"]["win"]:
        platforms_to_build.append("win")
    if config["build"]["mac"]:
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

    if runnable_tasks:
        run_tasks(config, runnable_tasks, stage="post-build")


if __name__ == '__main__':
    cli()
