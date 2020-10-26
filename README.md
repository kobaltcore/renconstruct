# renConstruct
[![CircleCI](https://circleci.com/gh/kobaltcore/renconstruct.svg?style=svg)](https://circleci.com/gh/kobaltcore/renconstruct)
[![Downloads](https://pepy.tech/badge/renconstruct)](https://pepy.tech/project/renconstruct)

A utility script to automatically build Ren'Py applications for multiple platforms.

renConstruct can build distributions for Windows, Linux, macOS and Android, including extra processing steps pre- and post-build.
By default it supports notarization for macOS distributions, a memory limit increase for Windows distributions (using `LARGEADDRESSAWARE`) and cleanup of temporary build artifacts. In addition, a `patch` task is available which enables pre-build patching of Python Ren'Py source files.

Custom pre- and post-build steps can easily be added.

## Installation
renConstruct can be installed via pip:
```bash
$ pip install renconstruct
```

Please note that renConstruct requires Python 3 and will not provide backwards compatibility for Python 2 for the foreseeable future.

## Usage
renConstruct operates based on the following process flow:
- Ensure dependencies are installed
- Validate configuration file
- Install specific version of Ren'Py if necessary
- Run the `pre-build` stage of all active tasks
- Build the Android distribution if enabled
- Build the macOS and Windows/Linux distributions if enabled
- Run the `post-build` stage of all active tasks

In the default configuration, the following tasks are executed at the respective build stage:
- `pre-build`:
    + `None`
- `post-build`:
    + `set_extended_memory_limit`
    + `clean`

### Configuration
renConstruct requires a configuration file to be supplied containing the information required to complete the build process for the various platforms. An empty template is provided in this repository under the name `config.empty.yml`

It consists of the following sections:

#### `tasks`
- `path`: An optional path to a directory containing Python files with custom tasks
- `set_extended_memory_limit`: A value of `true` or `false` determining whether to run this task or not
- `notarize`: A value of `true` or `false` determining whether to run this task or not
- `clean`: A value of `true` or `false` determining whether to run this task or not
- `patch`: A value of `true` or `false` determining whether to run this task or not

#### `build`
- `win`: A value of `true` or `false` determining whether to build the Windows/Linux distribution or not
- `mac`: A value of `true` or `false` determining whether to build the macOS distribution or not
- `android`: A value of `true` or `false` determining whether to build the Android distribution or not


#### `renutil`
- `version`: The version of Ren'Py to use while building the distributions
- `registry`: A path where `renutil` data is stored. Mostly useful for CI environments

#### `renotize`
- `apple_id`: The e-Mail address belonging to the Apple ID you want to use for signing applications.
- `password`: An app-specific password generated through the [management portal](https://appleid.apple.com/account/manage) of your Apple ID.
- `identity`: The identity associated with your Developer Certificate which can be found in `Keychain Access` under the category "My Certificates". It starts with `Developer ID Application:`, however it suffices to provide the 10-character code in the title of the certificate.
- `bundle`: The internal name for your app. This is typically the reverse domain notation of your website plus your application name, i.e. `com.example.mygame`.
- `altool_extra`: An optional string that will be passed on to all `altool` runs in all commands. Useful for selecting an organization when your Apple ID belongs to multiple, for example. Typically you will not have to touch this and you can leave it empty.

### Default Tasks
renConstruct ships with several built-in tasks that enable project-independent functionality.

#### `Clean`
This task runs as the last task in the `post-build` stage. Its purpose is to remove unnecessary artifacts and temporary build data. It achieves this by running `renutil clean`, which will remove all temporary build artifacts from the Ren'Py instance used to build the project. In addition it removes non-universal APK artifacts from the output folder.

Given that the universal APK's are only a few Megabytes larger and more widely compatible, the author opines that shipping the universal versions is preferable in all cases.

#### `Notarize`
This task runs in the `post-build` stage and utilizes the `renotize` utility to automatically notarize the macOS build of the project, if it was generated. The process is entirely automatic, though it may take a few minutes depending on the size of the game and the load on Apple's notarization servers.

#### `Set Extended Memory Limit`
This task runs in the `post-build` stage and operates on the Windows build of the project, if it was generated. Specifically, it modifies the executable file by setting the `LARGEADDRESSAWARE` flag in the header of the file, which enables the final build to access 4GB of RAM on Windows, instead of the typical 2GB when running in regular 32-Bit mode.

Given that Ren'Py already supports 64-Bit operation under Linux and macOS (and through personal experience deploying thusly modified games), this option should be safe to use in effectively all cases.

#### `Patch`
This task runs early in the `pre-build` stage and is capable of applying patch files (code diffs) to Python files of the Ren'Py instance that will be used to build the project. This allows for automatic application of game-specific patches before building, enabling customization at the engine level.

It is important to note that renConstruct does *not* build or rebuild Ren'Py after these patches are applied. As such it is only possible to patch runtime files, which effectively boils down to the pure-Python parts of Ren'Py. Patching the compiled parts is not supported and actively discouraged.

The task works by looking at a directory of patch files (specified in the configuration file) and applying them to equally-named files in the directory of the Ren'Py instance. For this to work, the structure of the patch directory must exactly mirror that of the paths to the actual file to patch, relative to the instance directory.

For example, if you wanted to patch the file `renpy/display/presplash.py` you would generated a patch file and name it `presplash.py`. After that you would put it into the directory `patches/renpy/display/presplash.py`.
Patch files are expected to match the `diff-match-patch` format for diffs. These types of files can be easily generated using the [`diffusor`](https://github.com/kobaltcore/diffusor) command-line utility.

renConstruct will automatically figure out the actual file to apply it to, as well as create a backup of the original file. If any part of the patching process fails, all changes are rolled back. It is also guaranteed that a file will only ever be patched once. Even if a file has been patched before and the patch file has changed, the new patch will be reliably applied to the original, unmodified file and the currently modified version will be replaced by the new version.

### Custom Tasks
Custom tasks can easily be added using the `path` value. It should point to a directory containing Python files.
Each file can contain one or more task, which will be picked up by renConstruct.

A task is an object that looks like this:
```python
class DoSomethingTask():

    PRIORITY = -100

    def __init__(self, name, config):
        self.name = name
        self.config = config

    @staticmethod
    def validate_config(config):
        pass

    def pre_build(self):
        pass

    def post_build(self):
        pass
```

The name of the class must end with `Task` for it to be picked up by renConstruct.
Every custom task will automatically receive a setting in the config file based on the class name split on uppercase letters, converted to lowercase and joined by underscores.
The example task above would receive the config variable `do_something`.

A task can have a custom section in the config file. To support this, each task class can have an optional static method `validate_config`, which is passed the subsection of the config object pertaining to that task. It can then validate this config object in any way it sees fit, raising an Exception when an issue occurs.
The parsed config subsection is then integrated into the full config object, which is then passed back to the task upon initialization.

A task can have two methods `pre_build` and `post_build` (either or both is possible).
They will be called with the validated config object at the specified build stage.
At that point they can do whatever they want. As an example, a custom task could be built to automatically optimize image assets in the game directory before every build.

Each task also has a `PRIORITY` class attribute which has a default value of `0` and determines the order in which to run the tasks. A higher priority means that task will be executed earlier than others with a lower value. Both positive and negative values are possible.

As an example, the built-in `clean` task runs at `PRIORITY = -1000` to ensure it's always the last task to be run.

### Example
```bash
renconstruct -c config.yml -i path/to/my-game/ -o out/
```

## Using `renConstruct` with Gitlab CI
A common use case for headless building of distributions is Continuous Integration.
Find below an example of a GitLab CI configuration file which should work for most projects.

```yaml
# Recent experience shows that using "python:latest" can cause issues
# because its definition may vary per runner. Always specify the exact
# version you intend to use to avoid issues.
image: python:3.8

variables:
    PIP_CACHE_DIR: "$CI_PROJECT_DIR/.cache/pip"

cache:
  paths:
    - .cache/pip
    - venv/

before_script:
  # Downloads renconstruct through pip
  - pip install renconstruct

run:
  script:
    # Runs renconstruct in the project directory, saving the build outputs to a new folder called "artifacts"
    # inside the project directory and utilises the file config.yml to specify reconstruct options.
    - renconstruct -d -i $CI_PROJECT_DIR -o $CI_PROJECT_DIR/artifacts -c $CI_PROJECT_DIR/config.yml

  artifacts:
    paths:
     # Saves the artifacts located in the "artifacts" folder to GitLab
      - $CI_PROJECT_DIR/artifacts/**.*
```

### Command Line Interface
```
Usage: renconstruct.py [OPTIONS]

  A utility script to automatically build Ren'Py applications for multiple
  platforms.

Options:
  -i, --input TEXT   The path to the Ren'Py project to build  [required]
  -o, --output TEXT  The directory to output build artifacts to  [required]
  -c, --config TEXT  The configuration file for this run  [required]
  -d, --debug        If given, shows debug information if
  --help             Show this message and exit.
```

# Disclaimer
renConstruct is a hobby project and not in any way affiliated with Ren'Py. This means that there is no way I can guarantee that it will work at all, or continue to work once it does.
