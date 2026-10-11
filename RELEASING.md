# Releasing threedice

Nothing in this file has been run yet. Steps 1 to 3 are done by you in a browser; the rest are commands.
Throughout, the GitHub repository is `rajul-kk/threedice`; change it everywhere (README, `pyproject.toml`, the trusted-publisher
forms) if you pick another name or owner.

## 0. Before anything leaves your machine

* Read `README.md`, `NOTICE` and `pyproject.toml`: the package is an **unofficial redistribution**, credits the 3D-ICE authors,
  asks users to cite the four papers, and lists you as the packager (author and maintainer metadata).
* `docs/email_to_esl_draft.md` is the note to the 3D-ICE maintainers. Plan to send it right after the first public upload
  (step 6), not before.

## 1. Create the GitHub repository

1. github.com > New repository. Owner `rajul-kk`, name `threedice`, **Public**, **empty** (no README, no licence, no .gitignore).
2. Settings > Environments > New environment, twice: `testpypi` and `pypi`. On `pypi` (and on `testpypi` if you like) tick
   *Required reviewers* and add yourself. The environment names must be exactly these.

## 2. Push

```
cd D:\Work\threedice
git remote add origin https://github.com/rajul-kk/threedice.git
git push -u origin main
```

This triggers `build` (`.github/workflows/build.yml`). Watch Actions. **Expect to fix things:** only the Linux x86_64 wheel was
ever built before this workflow existed. aarch64 and macOS are untested; the Windows job is experimental (`continue-on-error`) and
may fail without failing the run. Fix, push, repeat until `sdist`, `wheel linux-x86_64`, `wheel linux-aarch64`, `wheel macos-arm64`
and `wheel macos-x86_64` are green. Do not publish a platform whose job you have not seen pass its smoke test. If a platform stays
red, delete its matrix line (it is then simply not published).

Note the number in the URL of the green run: `https://github.com/rajul-kk/threedice/actions/runs/<run_id>`.

## 3. Register the trusted publishers (no API tokens needed)

Create accounts on https://test.pypi.org and https://pypi.org if you have none (2FA is required). Because the project does not
exist yet, use a *pending* publisher.

**TestPyPI**: https://test.pypi.org/manage/account/publishing/ > "Add a new pending publisher" > GitHub

| field | value |
|---|---|
| PyPI Project Name | `threedice` |
| Owner | `rajul-kk` |
| Repository name | `threedice` |
| Workflow name | `publish.yml` |
| Environment name | `testpypi` |

**PyPI**: https://pypi.org/manage/account/publishing/ > same form

| field | value |
|---|---|
| PyPI Project Name | `threedice` |
| Owner | `rajul-kk` |
| Repository name | `threedice` |
| Workflow name | `publish.yml` |
| Environment name | `pypi` |

First check that the name `threedice` is free on both indexes (https://pypi.org/project/threedice/ should be a 404). If it is
taken, change `name` in `pyproject.toml`, the project name in these forms, and the README install line.

## 4. Publish to TestPyPI

GitHub > Actions > **publish** > Run workflow: `target` = `testpypi`, `run_id` = the green build run, `include_windows` = off.
Approve the environment prompt if you set reviewers. The job downloads the sdist and wheels of that run and uploads them with
`pypa/gh-action-pypi-publish` (OIDC; `skip-existing` is on, so re-running is harmless).

## 5. Verify the TestPyPI upload in a clean container

The package has no dependencies, so only the package itself needs to come from TestPyPI:

```
docker run --rm python:3.11-slim bash -c "
  python -m venv /v && /v/bin/pip install -i https://test.pypi.org/simple/ threedice &&
  /v/bin/python -c 'import threedice; print(threedice.executable(), threedice.ice_commit(), threedice.__version__)' &&
  /v/bin/pip download -i https://test.pypi.org/simple/ --no-deps --no-binary :all: threedice -d /s &&
  tar tzf /s/threedice-*.tar.gz | grep -E 'upstream/|COPYING|NOTICE'"
```

Expected: the path of the bundled binary, `4953952a1ef6d38807ff307212a6f15e5b2ef935`, the version, and the sdist listing with both
`upstream/*.tar.gz`, `COPYING` and `NOTICE`. Then run `/v/bin/3d-ice-emulator some.stk` on a stack of yours. Repeat on an aarch64
machine and on a Mac if you have them, since CI only ran the smoke test there. Also look at
https://test.pypi.org/project/threedice/ : the description must show the "unofficial redistribution" notice, the citation list and
the Source/Upstream links.

## 6. Publish to PyPI (only after you have confirmed step 5)

Actions > **publish** > `target` = `pypi`, same `run_id`. Approve. Verify with `pip install threedice` in a fresh container.
PyPI uploads cannot be replaced: a mistake means a new version (`4.0.0.post2`: bump `version` in `pyproject.toml`, push, new
build run).

Then send the email (`docs/email_to_esl_draft.md`) to the 3D-ICE maintainers. Do not attach the defect reports or patches; those
go separately, once you have decided to send them.

## 7. Tag

```
git tag -a v4.0.0.post1 -m "threedice 4.0.0.post1 (3D-ICE 4953952)"
git push origin v4.0.0.post1
```

Tags `v*` also trigger `build`; publishing stays manual.

## Releasing a new version later

Change `version`. Change `upstream.json` only if you deliberately move the pin; then update README, NOTICE, the `PIN` env in
`build.yml` and `tests/test_threedice.py`, and re-verify against the pinned reference. Push, wait for a green build, run **publish**.

## If something goes wrong

* `invalid-publisher` on upload: the values in step 3 do not match the workflow run exactly (workflow filename, environment,
  owner, repository).
* The aarch64 job errors on the runner label: the arm runner is not available to the repository. Switch that matrix line to
  `ubuntu-latest` and add a `docker/setup-qemu-action@v3` step before cibuildwheel.
* Withdrawing a release: PyPI project page > Manage > Releases > yank or delete.
