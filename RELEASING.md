# Releasing clipsense

## First time only

### 1. Put the code on GitHub

```bash
gh repo create xCynDevelopment/clipsense --public --source . --push
```

### 2. Let GitHub publish to PyPI

1. Make an account at [pypi.org](https://pypi.org/account/register/) and turn on two-factor login.
2. Go to [pypi.org/manage/account/publishing](https://pypi.org/manage/account/publishing/) and add a
   **pending publisher**:
   - PyPI project name: `clipsense`
   - Owner: `xCynDevelopment`
   - Repository name: `clipsense`
   - Workflow name: `release.yml`
   - Environment name: `pypi`
3. On GitHub, open the repo → **Settings** → **Environments** → **New environment**, and name it `pypi`.

No passwords or tokens are needed after that.

## Every release

1. Bump `version` in `pyproject.toml` and `__version__` in `src/clipsense/__init__.py`.
2. Commit and push.
3. Tag the release:

```bash
git tag v0.1.0
```

```bash
git push --tags
```

GitHub Actions then publishes the package to PyPI, builds `clipsense-installer.exe` and
`clipsense-installer-mac.zip`, and attaches them to a GitHub release. Watch it under the repo's **Actions** tab.
The download links in the README always point to the newest release.

## Test the installer before releasing

```bash
uv run python tests/smoke_test.py
```

```bash
uv run python installer/clipsense_installer.py
```

To make the installer use your local code instead of PyPI, set this first (PowerShell):

```bash
$env:CLIPSENSE_INSTALL_SOURCE = "clipsense[all] @ file:///C:/Users/ads02/Documents/clipsense-project/clipsense"
```
