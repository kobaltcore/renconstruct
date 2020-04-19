# renConstruct
[![CircleCI](https://circleci.com/gh/kobaltcore/renconstruct.svg?style=svg)](https://circleci.com/gh/kobaltcore/renconstruct)
[![Downloads](https://pepy.tech/badge/renconstruct)](https://pepy.tech/project/renconstruct)

A utility script to automatically build Ren'Py applications for multiple platforms.

renConstruct can build distributions for Windows, Linux, macOs and Android, including extra processing steps pre- and post-build.
By default it supports notarization for macOS distributions and a memory limit increase for Windows distributions (using `LARGEADDRESSAWARE`).

Custom pre- and post-build steps can easily be added.

## Installation
renConstruct can be installed via pip:
```bash
$ pip install renconstruct
```

Please note that renConstruct requires Python 3 and will not provide backwards compatibility for Python 2 for the foreseeable future.

## Usage
TODO

### Configuration
TODO

### Example
```bash
renconstruct -c config.yml -i path/to/my-game/ -o out/
```

### Command Line Interface
```
Usage: renconstruct.py [OPTIONS]

  A utility script to automatically build Ren'Py applications for multiple
  platforms.

Options:
  -i, --input TEXT               [required]
  -o, --output TEXT              [required]
  -c, --config TEXT
  -d, --debug / -nd, --no-debug  Print debug information or only regular
                                 output

  --help                         Show this message and exit.
```

# Disclaimer
renConstruct is a hobby project and not in any way affiliated with Ren'Py. This means that there is no way I can guarantee that it will work at all, or continue to work once it does.
