# Local Development and Debugging Instructions

## Getting Started

1. Install Docker.
2. Run the following commands:
    ```shell
    make build
    make run
    make install
    make test
    ```

Performance smoke tests are skipped by default. Run them with `make test-perf` to catch order-of-magnitude regressions in the per-feature pipeline.

## Recording README GIFs

The GIFs in `README.md` (interactive map tool and Processing algorithm) are recorded by a scripted QGIS session, so they can be regenerated after UI changes:

```shell
make gifs                          # all scenarios
make gifs SCENARIOS="distance"     # just one (the scenario name is the GIF name)
make gifs THEME=light              # light QGIS theme (default: dark)
```

GIFs are written to `build/gifs/` (gitignored); upload them to the `pptl_images` repo, which the README links to. The setup lives in `tools/record_gifs/`:

- `Dockerfile` extends the project image with Xvfb, openbox, xdotool and ffmpeg.
- `qgis_startup.py` runs inside QGIS: builds the demo layers (a separate, more varied scene for Processing scenarios), loads the plugin, exports screen positions of features and toolbar buttons, and keeps `ui.json` updated with positions of menus, the Processing toolbox and algorithm widget, dialogs, combo-box popups and layer-tree checkboxes.
- `driver.py` moves a real X cursor through each scenario, targeting static points by name and transient UI by `ui.json` key (e.g. `menu:Toolbox`, `param:DISTANCE`, `button:Run`). Add a scenario by adding a function there, its unrecorded preconditions to `SETUP`, and its name to `SCENARIOS` in the `Makefile` (and, for a Processing scenario, to `PROCESSING_SCENARIOS` in `qgis_startup.py`, otherwise it is recorded on the interactive scene).
- `record.sh` ties it together inside the container and fails if the plugin raised any Python error during the recording.

Gotchas: the QGIS image is amd64-only, so on Apple Silicon it runs emulated (roughly a minute per scenario). D-Bus is disabled on purpose, otherwise a GNOME keyring password prompt blocks QGIS startup. The install step needs about 120 MB free in Docker's disk.

## Remote Debugging

These instructions are specific to PyCharm.

1. Set up the `uv` Python interpreter.
2. Run the following commands:
    ```shell
    uv sync
    make install-dev
    ```
3. Set up the Python Debug Server in PyCharm:
    - IDE host name: `127.0.0.1`
    - Port: `53100`
4. Add breakpoint(s) as needed.

### Docker Remote Debugging

1. Perform the additional setup for the Python Debug Server:
    - Path mappings: `<absolute/path/to/repo>=/pptl` (replace the left side with your local clone's absolute path)
2. Uncomment the following line in `tests/test_main_functionality.py`:
    ```python
    # pydevd_pycharm.settrace("host.docker.internal", port=53100, stdoutToServer=True, stderrToServer=True)
    ```
3. Add `import pydevd_pycharm` to `tests/test_main_functionality.py`.

### QGIS Remote Debugging

1. Install `pydevd-pycharm` and `pydevd` into the Python interpreter bundled with QGIS. On macOS:
    ```shell
    /Applications/QGIS.app/Contents/MacOS/bin/python3 -m pip install pydevd-pycharm pydevd
    ```
    On Linux/Windows, invoke `pip` from the QGIS-bundled Python (e.g., the `python3` shipped under the QGIS install directory).
2. Uncomment the following line in `PolygonsParallelToLine/src/pptl.py`:
    ```python
    # pydevd_pycharm.settrace("127.0.0.1", port=53100, stdoutToServer=True, stderrToServer=True)
    ```
3. Add `import pydevd_pycharm` to `PolygonsParallelToLine/src/pptl.py`.
4. In QGIS, navigate to Preferences → System → Environment, and add the following variable:
    - Apply: Append
    - Variable: `QGIS_PLUGINPATH`
    - Value: `<absolute/path/to/repo>` (the absolute path to your local clone of this repository)
5. To reload the plugin, use the "Plugin Reloader" plugin.

## Releasing a New Plugin Version

1. Make sure `CHANGELOG.md`'s `## [Unreleased]` section has the entries you want shipped, then on `main` with a clean working tree run:
   ```shell
   make tag VERSION=X.Y.Z
   ```
   This rewrites `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`, inserts a fresh empty `## [Unreleased]` above it, commits, tags, and pushes both to `origin` atomically. `qgis-plugin-ci` requires a 3-part `MAJOR.MINOR.PATCH` version — `## [1.2]` would be silently ignored and the published changelog would be empty.
2. The `Release` workflow (`.github/workflows/release.yaml`) runs the tests, then publishes to plugins.qgis.org and creates a GitHub Release with the `.zip` attached.

Required GitHub Secrets (one-time setup, repo Settings → Secrets and variables → Actions):
- `OSGEO_USERNAME` — your plugins.qgis.org account username
- `OSGEO_PASSWORD` — your plugins.qgis.org account password

(`GITHUB_TOKEN` is provided automatically by Actions.)

For a staged/experimental release, use a 3-part pre-release tag suffix (e.g. `1.2.0-rc1`). The portal will list it as experimental. A 2-part form like `1.2-rc1` is not detected as a pre-release by `qgis-plugin-ci` and will be published as stable.

## Previewing the GitHub Pages Site Locally

The project page (`_config.yml`) is served by GitHub Pages with the `jekyll-theme-midnight` theme. To preview the site or trial other themes locally:

1. Install Ruby 3.1. The `github-pages` gem pins Jekyll 3.9 / Liquid 4.0.3, which calls `String#tainted?` — removed in Ruby 3.2 — so newer Rubies will not work.
    ```shell
    brew install ruby@3.1
    echo 'export PATH="/opt/homebrew/opt/ruby@3.1/bin:$PATH"' >> ~/.zshrc
    exec zsh
    ```
2. Install the bundled gems and start the server:
    ```shell
    bundle install
    bundle exec jekyll serve
    ```
    The site is served at http://127.0.0.1:4000.
3. To try a different theme, stop the server (Ctrl-C), change `theme:` in `_config.yml`, and restart — Jekyll watches content but not config. The `Gemfile` already pulls in every GitHub-supported theme (`architect`, `cayman`, `dinky`, `hacker`, `leap-day`, `merlot`, `midnight`, `minimal`, `modernist`, `primer`, `slate`, `tactile`, `time-machine`).

The `Gemfile` also lists several stdlib gems (`csv`, `bigdecimal`, `base64`, `logger`, `mutex_m`, `ostruct`, `drb`, `webrick`) that were promoted out of Ruby's default gem set in 3.0–3.4 and are still required by Jekyll 3.9 / its dependencies.
