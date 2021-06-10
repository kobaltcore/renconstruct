### System ###
import os
import re
import sys
import shutil
import logging
import inspect
from glob import glob
from contextlib import contextmanager
from subprocess import run, Popen, PIPE, STDOUT
from importlib import import_module, invalidate_caches

### Logging ###
from rich.text import Text
from rich.logging import RichHandler

LOG_LEVELS = {
    logging.DEBUG: "::debug::{message}",
    logging.WARNING: "::warning file={name},line={line}::{message}",
    logging.ERROR: "::error file={name},line={line}::{message}",
}


class GHAFormatter(logging.Formatter):
    def format(self, record):
        level_template = LOG_LEVELS.get(record.levelno, "{message}")
        return level_template.format(
            name=os.path.basename(record.pathname),
            line=record.lineno,
            message=Text.from_markup(record.msg),
        )


if os.environ.get("GITHUB_ACTIONS") == "true":
    handler = logging.StreamHandler()
    handler.setFormatter(GHAFormatter())
else:
    handler = RichHandler(rich_tracebacks=True, markup=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[handler],
)

logger = logging.getLogger("rich")

### Parsing ###
import yaml

### CLI Parsing ###
import click


@contextmanager
def gha_group(title):
    """
    A context manager which groups all console output
    when running on GitHub Actions CI.
    In all other cases, this is a NOOP.
    """
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::group::{title}")
        try:
            yield
        finally:
            print("::endgroup::")
    else:
        yield


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
        ctx.fail(
            "Too many matches: [green]{}[/green]".format(", ".join(sorted(matches)))
        )


def run_tasks(config, tasks, stage="pre-build"):
    logger.info("Running stage '{}' for active tasks".format(stage))
    for name, task_class, priority in tasks:
        try:
            task = task_class(name, config)
        except Exception:
            logger.exception("Task '{}' failed to initialize:".format(name))
            sys.exit(1)
        try:
            if stage == "pre-build" and hasattr(task, "pre_build"):
                logger.info("Running '{}'".format(name))
                task.pre_build()
            elif stage == "post-build" and hasattr(task, "post_build"):
                logger.info("Running '{}'".format(name))
                task.post_build()
        except Exception:
            logger.exception(
                "Task '{}' failed to execute '{}':".format(
                    name, stage.replace("-", "_")
                )
            )
            sys.exit(1)


def scan_tasks(config):
    task_files = glob(
        os.path.join(os.path.dirname(__file__), "tasks", "**", "*.py"), recursive=True
    )
    if config["tasks"]["path"] and os.path.isdir(config["tasks"]["path"]):
        logger.debug("Using tasks from {}".format(config["tasks"]["path"]))
        task_files += glob(
            os.path.join(config["tasks"]["path"], "**", "*.py"), recursive=True
        )

    tmp_dir = os.path.join(os.path.dirname(__file__), "renconstruct_tasklib")
    logger.debug("Temporary task directory (rel): {}".format(tmp_dir))
    logger.debug("Temporary task directory (abs): {}".format(os.path.abspath(tmp_dir)))

    if os.path.isdir(tmp_dir):
        shutil.rmtree(tmp_dir)
    os.makedirs(tmp_dir, exist_ok=True)

    for file in task_files:
        shutil.copyfile(file, os.path.join(tmp_dir, os.path.basename(file)))
        logger.debug(
            "Copying task {} to {}".format(
                file, os.path.join(tmp_dir, os.path.basename(file))
            )
        )

    task_files = [
        os.path.join("renconstruct_tasklib", os.path.basename(file))
        for file in glob(os.path.join(tmp_dir, "**", "*.py"), recursive=True)
        if not file.endswith("__init__.py")
    ]
    logger.debug(
        "Found task files: [green]{}[/green]".format(
            ", ".join([os.path.basename(path) for path in task_files])
        )
    )

    invalidate_caches()

    available_tasks = {}
    for file in task_files:
        name = os.path.splitext(file)[0].split(os.sep)
        logger.debug("Got task file [green]{}[/green]".format(file))
        try:
            module_name = "renconstruct." + ".".join(name)
            logger.debug("Trying to load [yellow]{}[/yellow]".format(module_name))
            task_module = import_module(module_name)  # noqa: F841
        except:  # noqa: E722
            module_name = ".".join(name)
            logger.debug(
                "Trying to load fallback [salmon]{}[/salmon]".format(module_name)
            )
            task_module = import_module(module_name)  # noqa: F841
        classes = [
            (name, obj)
            for name, obj in inspect.getmembers(
                sys.modules[module_name], inspect.isclass
            )
        ]
        for name, task_class in classes:
            if name.endswith("Task"):
                new_name = "_".join(
                    [item.lower() for item in re.split(r"(?=[A-Z])", name[:-4]) if item]
                )
                available_tasks[new_name] = task_class

    if not available_tasks:
        logger.warning(
            "Did not find any tasks, something is likely off with the task library location"
        )

    available_task_names = set(available_tasks.keys())
    defined_task_names = set(
        [item for item in config["tasks"].keys() if item != "path"]
    )
    undefined_task_names = available_task_names - defined_task_names
    if undefined_task_names:
        logger.warning(
            "Some tasks were not defined in the config, assuming they are disabled:"
        )
        for name in undefined_task_names:
            logger.warning("- {}".format(name))

    new_tasks = {}
    for name, task_class in available_tasks.items():
        config_value = config["tasks"].get(name, False)
        if not isinstance(config_value, bool):
            logger.error(
                "'{}' must be 'True' or 'False', got '{}'".format(name, config_value)
            )
            sys.exit(1)
        else:
            task_class = available_tasks[name]
            new_tasks[name] = (config_value, task_class)

    runnable_tasks = []
    affected_files = []
    logger.info("Loaded tasks:")
    for name, (enabled, task_class) in new_tasks.items():
        logger.info(
            "{} {}".format(
                "[green]\u2714[/green]" if enabled else "[red]\u2718[/red]", name
            )
        )
        if enabled:
            if hasattr(task_class, "validate_config"):
                try:
                    task_config = task_class.validate_config(config.get(name, {}))
                    config[name] = task_config
                except Exception:
                    logger.exception(
                        "Task '{}' failed to validate its config section:".format(name)
                    )
                    sys.exit(1)
            priority = task_class.PRIORITY if hasattr(task_class, "PRIORITY") else 0
            for file in (
                task_class.AFFECTED_FILES
                if hasattr(task_class, "AFFECTED_FILES")
                else []
            ):
                if file in affected_files:
                    logger.exception(
                        "Task '{}' specifies an affected file '{}' which was already specified by another task.".format(
                            name, file
                        )
                    )
                    sys.exit(1)
                affected_files.append(file)
            runnable_tasks.append((name, task_class, priority))

    return sorted(runnable_tasks, key=lambda x: x[2], reverse=True), affected_files


def validate_config(config):
    if config.get("build", None) is None:
        config["build"] = {"pc": True, "mac": True, "android": True}
    if config["build"].get("pc", None) is None:
        config["build"]["pc"] = True
    if config["build"].get("mac", None) is None:
        config["build"]["mac"] = True
    if config["build"].get("android", None) is None:
        config["build"]["android"] = True

    if config.get("renutil", None) is None:
        config["renutil"] = {"version": "latest"}
    if config["renutil"].get("version", None) is None:
        config["renutil"]["version"] = "latest"
    if config["renutil"].get("registry", None) is None:
        config["renutil"]["registry"] = None

    if config.get("tasks", None) is None:
        config["tasks"] = {"path": None}
    if config["tasks"].get("path", None) is None:
        config["tasks"]["path"] = None
    if config["tasks"].get("path", None) is not None:
        config["tasks"]["path"] = os.path.expanduser(config["tasks"]["path"])

    return config


@click.command()
@click.option(
    "-i",
    "--input",
    "project",
    required=True,
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
    help="The path to the Ren'Py project to build",
)
@click.option(
    "-o",
    "--output",
    required=True,
    type=click.Path(file_okay=False, resolve_path=True, writable=True),
    help="The directory to output build artifacts to",
)
@click.option(
    "-c",
    "--config",
    required=True,
    type=click.Path(exists=True, dir_okay=False, resolve_path=True),
    help="The configuration file for this run",
)
@click.option(
    "-d", "--debug", is_flag=True, help="If given, shows debug information if"
)
def cli(project, output, config, debug):
    """A utility script to automatically build Ren'Py applications for multiple platforms."""
    logger.setLevel(logging.DEBUG if debug else logging.INFO)

    if not os.path.exists(output):
        logger.warning("The output directory does not exist, creating it...")
        os.makedirs(output, exist_ok=True)

    with open(config, "r") as f:
        config = yaml.full_load(f)

    config["project"] = project
    config["output"] = output
    config["debug"] = debug

    config = validate_config(config)

    runnable_tasks, affected_files = scan_tasks(config)

    p = run("renutil --help", capture_output=True, shell=True)
    if not (b"Usage: renutil" in p.stdout and p.returncode == 0):
        logger.error("Please install 'renutil' before continuing!")
        sys.exit(1)

    p = run("renotize --help", capture_output=True, shell=True)
    if not (b"Usage: renotize" in p.stdout and p.returncode == 0):
        logger.error("Please install 'renotize' before continuing!")
        sys.exit(1)

    registry_cmd = ""
    if config["renutil"]["registry"]:
        registry_cmd = "-r '{}'".format(config["renutil"]["registry"])

    logger.info("Checking available Ren'Py versions")
    p = run("renutil {} list".format(registry_cmd), capture_output=True, shell=True)
    available_versions = [
        item.strip() for item in p.stdout.decode("utf-8").split("\n") if item.strip()
    ]

    if config["renutil"]["version"] == "latest":
        p = run(
            "renutil {} list --all".format(registry_cmd),
            capture_output=True,
            shell=True,
        )
        chosen_version = [
            item.strip() for item in p.stdout.decode("utf-8").split("\n")
        ][0]
        config["renutil"]["version"] = chosen_version

    if config["renutil"]["version"] not in available_versions:
        logger.warning(
            "Ren'Py [green]{}[/green] is not installed, installing now...".format(
                config["renutil"]["version"]
            )
        )
        p = run(
            "renutil {} install {}".format(registry_cmd, config["renutil"]["version"]),
            shell=True,
        )

    p = run(
        "renutil {} show {}".format(registry_cmd, config["renutil"]["version"]),
        capture_output=True,
        shell=True,
    )
    output = p.stdout.decode("utf-8").split("\n")
    config["renutil"]["path"] = (
        [item.strip() for item in output][1].lstrip("Install Location:").strip()
    )

    if affected_files:
        logger.info(
            "Found {} affected files requiring backup...".format(len(affected_files))
        )
        for file in affected_files:
            full_path = os.path.join(config["renutil"]["path"], file)
            if not os.path.isfile(full_path):
                logger.warning("'{}' could not be found".format(file))
                continue
            backup_path = os.path.join(
                os.path.dirname(full_path),
                "{}.original".format(os.path.basename(full_path)),
            )
            if os.path.isfile(backup_path):
                logger.info("File '{}' already has a backup, restoring...".format(file))
                shutil.copyfile(backup_path, full_path)
                continue
            logger.info("Backing up '{}'...".format(file))
            shutil.copyfile(full_path, backup_path)

    if runnable_tasks:
        with gha_group("Pre-Build Tasks"):
            run_tasks(config, runnable_tasks, stage="pre-build")

    if config["build"]["android"]:
        with gha_group("Build Android"):
            logger.info("Building Android package")
            cmd = "renutil {} launch {} -h android_build \
            {} assembleRelease --destination {}".format(
                registry_cmd,
                config["renutil"]["version"],
                config["project"],
                config["output"],
            )
            proc = Popen(cmd, shell=True, stdout=PIPE, stderr=STDOUT)
            for line in proc.stdout:
                line = str(line.strip(), "utf-8")
                if line:
                    logger.debug(line)

    platforms_to_build = []
    if config["build"]["pc"]:
        platforms_to_build.append("pc")
    if config["build"]["mac"]:
        platforms_to_build.append("mac")

    if platforms_to_build:
        with gha_group(f"Build {', '.join(platforms_to_build)}"):
            if len(platforms_to_build) == 1:
                logger.info("Building {} package".format(platforms_to_build[0]))
            elif len(platforms_to_build) > 1:
                logger.info(
                    "Building {} packages".format(", ".join(platforms_to_build))
                )
            cmd = "renutil {} launch {} -h distribute \
            {} --destination {}".format(
                registry_cmd,
                config["renutil"]["version"],
                config["project"],
                config["output"],
            )
            for package in platforms_to_build:
                cmd += " --package {}".format(package)
            proc = Popen(cmd, shell=True, stdout=PIPE, stderr=STDOUT)
            for line in proc.stdout:
                line = str(line.strip(), "utf-8")
                if line:
                    logger.debug(line)

    if runnable_tasks:
        with gha_group("Post-Build Tasks"):
            run_tasks(config, runnable_tasks, stage="post-build")


if __name__ == "__main__":
    cli()
