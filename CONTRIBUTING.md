# Contributing to Sentient

Use feature branches and pull requests against `main`. Keep changes focused and
include the checks you ran in the pull request description.

## Local development

Read README.md and docs/OPERATIONS.md for the application and reporting workflow.
The app source is in `app/` and `web/`. Python dependencies are recorded in
`requirements-app.txt`; browser/test dependencies are in
`requirements-app-test.txt`. Platform-specific inference requirements are kept
separately.

Run the application checks from an installed environment:

```sh
.venv-app/bin/python -m app.checks
```

Use `python` from the appropriate virtual environment on other platforms.

## Local assets

Footage, trained models, runtime catalogues, generated reports and virtual
environments are intentionally excluded from Git. Existing demo operation needs
those assets in the locations described in README.md and demo_config.json.
Cloning the source does not download the installed demo videos or checkpoints.
Arrange asset transfer with the repository owner and respect dataset licences.
Never commit access tokens, `.env` secrets, raw customer recordings or runtime
review databases.

## Analytics changes

Preserve camera-local identity scope and source-time semantics. Do not present
tracker predictions as observed dwell, unscored runs as zero pickup candidates,
or undated footage as a measured store shift. Keep simulated data labelled and out
of anything that describes a real store. Add focused checks for changes to tracking,
review persistence, time windows or report generation.
