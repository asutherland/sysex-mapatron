## Overview

`schemify.py` knows how to process Roland MIDI reference PDFs and produce JSON
sysex maps as a byproduct.

Right now it's expected that you will manually create a directory structure that
resembles the following, probably involving symlinks.
- `doc-inputs`
  - `midi-ref.pdf`

## Setup

We want the ability to have `pdfminer.six` available as a dependency and to run
under Python3.  The following should accomplish that by setting up a venv
virtualenv that results in `python` being python3 and with `pdfminer.six`
installed and available.

```shell
python3 -m venv env
. env/bin/activate
pip install pdfminer.six
```

Once you've done that, then each session you are hacking on this, then you would
run:
```
. env/bin/activate
```

There are probably better ways to accomplish this.  In particular, to make
VS Code happy, I changed its assumed venv path from `.env` to `env` suggesting
the former is the convention.  However, I'm likely to forget the need to source
the activation script if the directory is hidden by default, and `env` is what
we use for searchfox (mozsearch), so I'm doing it this way for now.

If someone wants to contribute a patch to better follow best practices while
improving / documenting the automation, I'd be happy to have that.